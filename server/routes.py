"""
REST API endpoints for the FastAPI server.
All model/risk calls use run_in_executor to avoid blocking the async loop.
"""

import asyncio
import logging
import os
import sys
import time
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query, Request

from engine.feed import FeedLog, FeedEventType
from engine.market_state import MarketStateStore
from engine.orderbook import OrderbookStore
from engine.strategies import load_strategies_from_manager
from server.schemas import (
    MarketResponse, HistoryPoint, FeedEventResponse,
    RiskResponse, PortfolioResponse,
)

logger = logging.getLogger("kalshi.routes")


def create_router(
    state: MarketStateStore,
    orderbooks: OrderbookStore,
    feed: FeedLog,
    signals_holder,
    rest_client,
    execution_engine=None,
    position_manager=None,
    risk_engine=None,
    strategy_manager=None,
    alert_engine=None,
    quant_brain=None,
    parlay_pricer=None,
) -> APIRouter:
    router = APIRouter(prefix="/api")

    # ── Markets ────────────────────────────────────────────────────────────

    @router.get("/markets", response_model=list[MarketResponse])
    async def get_markets():
        return state.get_all_markets()

    @router.get("/markets/{ticker}", response_model=MarketResponse)
    async def get_market(ticker: str):
        m = state.get_market(ticker)
        if not m:
            raise HTTPException(404, f"Market {ticker} not found")
        return m

    # ── Signals ────────────────────────────────────────────────────────────

    @router.get("/signals")
    async def get_signals():
        return signals_holder.get()

    @router.get("/signals/{ticker}")
    async def get_signal(ticker: str):
        sig = signals_holder.get_by_ticker(ticker)
        if not sig:
            raise HTTPException(404, f"No signal for {ticker}")
        return sig

    # ── Portfolio ──────────────────────────────────────────────────────────

    @router.get("/portfolio", response_model=PortfolioResponse)
    async def get_portfolio():
        loop = asyncio.get_running_loop()
        try:
            balance_data = await loop.run_in_executor(None, rest_client.get_balance)
            positions_data = await loop.run_in_executor(None, rest_client.get_positions)
            return {
                "balance": balance_data.get("balance", 0) / 100.0,  # cents -> dollars
                "positions": positions_data.get("market_positions", []),
            }
        except Exception as e:
            logger.error("Portfolio fetch failed: %s", e)
            raise HTTPException(502, f"Could not fetch portfolio: {e}")

    # ── Risk ───────────────────────────────────────────────────────────────

    @router.get("/risk", response_model=RiskResponse)
    async def get_risk():
        loop = asyncio.get_running_loop()
        try:
            result = await loop.run_in_executor(None, _compute_risk, signals_holder)
            return result
        except Exception as e:
            logger.error("Risk computation failed: %s", e)
            raise HTTPException(502, f"Risk computation failed: {e}")

    # ── History ────────────────────────────────────────────────────────────

    @router.get("/history/{ticker}", response_model=list[HistoryPoint])
    async def get_history(ticker: str, limit: int = Query(200, le=2000)):
        hist = state.get_history(ticker, limit=limit)
        if len(hist) >= 10:
            return hist
        # Fallback: load from historical CSV when live data is sparse
        loop = asyncio.get_running_loop()
        csv_hist = await loop.run_in_executor(None, _load_history_from_csv, ticker, limit)
        if csv_hist:
            # Append any live points that are newer than CSV data
            if hist and csv_hist:
                last_csv_ts = csv_hist[-1]["ts"]
                live_newer = [h for h in hist if h.get("ts", "") > last_csv_ts]
                csv_hist.extend(live_newer)
            return csv_hist[-limit:]
        if hist:
            return hist
        # Third fallback: fetch from Kalshi REST API
        try:
            trades_data = await loop.run_in_executor(
                None, lambda: rest_client.get_trades(ticker, limit=limit)
            )
            trades_list = trades_data.get("trades", [])
            if trades_list:
                kalshi_hist = []
                for t in trades_list:
                    # Support API v2 (yes_price_dollars) and v1 (yes_price in cents)
                    if "yes_price_dollars" in t:
                        p = float(t["yes_price_dollars"])
                    else:
                        p = t.get("yes_price", 0)
                        p = p / 100.0 if p > 1 else float(p)
                    vol = int(float(t.get("count_fp", t.get("count", 0))))
                    kalshi_hist.append({
                        "ts": t.get("created_time", ""),
                        "price": p, "yes_bid": p, "yes_ask": p,
                        "volume": vol,
                    })
                kalshi_hist.sort(key=lambda x: x["ts"])
                return kalshi_hist[-limit:]
        except Exception as e:
            logger.warning("Kalshi REST trades fallback failed for %s: %s", ticker, e)
        if not state.get_market(ticker):
            raise HTTPException(404, f"Market {ticker} not found")
        return []

    # ── Feed ───────────────────────────────────────────────────────────────

    @router.get("/feed", response_model=list[FeedEventResponse])
    async def get_feed(limit: int = Query(50, le=500)):
        return feed.get_recent(limit=limit)

    # ── Backtest ──────────────────────────────────────────────────────────

    @router.get("/backtest")
    async def get_backtest():
        loop = asyncio.get_running_loop()
        try:
            result = await loop.run_in_executor(None, _load_or_run_backtest)
            return result
        except Exception as e:
            logger.error("Backtest failed: %s", e)
            raise HTTPException(502, f"Backtest failed: {e}")

    @router.post("/backtest/run")
    async def run_backtest_fresh():
        """Force a fresh backtest run — ignores cached results."""
        loop = asyncio.get_running_loop()
        try:
            result = await loop.run_in_executor(None, _run_fresh_backtest)
            return result
        except Exception as e:
            logger.error("Fresh backtest failed: %s", e)
            raise HTTPException(502, f"Backtest failed: {e}")

    # ── Simulated Portfolio ───────────────────────────────────────────────

    @router.get("/sim-portfolio")
    async def get_sim_portfolio():
        """Return simulated portfolio state from signals."""
        sig_data = signals_holder.get()
        sig_list = sig_data.get("signals", [])
        portfolio_value = sig_data.get("portfolio_value", 10000)

        positions = []
        total_deployed = 0
        for s in sig_list:
            if s.get("recommended_contracts", 0) > 0:
                size = s.get("risk", {}).get("size_dollars", 0)
                total_deployed += size
                positions.append({
                    "ticker": s["ticker"],
                    "title": s.get("title", ""),
                    "category": s.get("category", ""),
                    "direction": s["direction"],
                    "contracts": s["recommended_contracts"],
                    "entry_price": s["current_price"],
                    "fair_value": s.get("fair_value", 0),
                    "edge": s.get("edge", 0),
                    "size_dollars": size,
                    "stop_loss": s.get("risk", {}).get("stop_loss", 0),
                    "take_profit": s.get("risk", {}).get("take_profit", 0),
                    "max_loss": s.get("risk", {}).get("true_max_loss", 0),
                })

        # Category allocation
        cat_alloc = {}
        for p in positions:
            cat = p.get("category", "Other") or "Other"
            cat_alloc[cat] = cat_alloc.get(cat, 0) + p["size_dollars"]

        return {
            "portfolio_value": portfolio_value,
            "cash": portfolio_value - total_deployed,
            "deployed": total_deployed,
            "positions": positions,
            "category_allocation": cat_alloc,
            "utilization": total_deployed / portfolio_value if portfolio_value > 0 else 0,
        }

    # ── Sentiment ──────────────────────────────────────────────────────

    @router.get("/sentiment/{ticker}")
    async def get_sentiment_for_ticker(ticker: str):
        m = state.get_market(ticker)
        if not m:
            raise HTTPException(404, f"Market {ticker} not found")
        loop = asyncio.get_running_loop()
        try:
            result = await loop.run_in_executor(
                None, _compute_sentiment, ticker, m.get("title", ""), m.get("category", ""), m["price"]
            )
            return result
        except Exception as e:
            logger.error("Sentiment failed for %s: %s", ticker, e)
            raise HTTPException(502, f"Sentiment failed: {e}")

    # ── Arbitrage ────────────────────────────────────────────────────────

    @router.get("/arbitrage")
    async def get_arbitrage():
        loop = asyncio.get_running_loop()
        try:
            result = await loop.run_in_executor(None, _compute_arbitrage, state)
            return result
        except Exception as e:
            logger.error("Arbitrage scan failed: %s", e)
            raise HTTPException(502, f"Arbitrage scan failed: {e}")

    # ── Correlations ────────────────────────────────────────────────────

    @router.get("/correlations")
    async def get_correlations():
        loop = asyncio.get_running_loop()
        try:
            result = await loop.run_in_executor(None, _compute_correlations, state)
            return result
        except Exception as e:
            logger.error("Correlation computation failed: %s", e)
            raise HTTPException(502, f"Correlation computation failed: {e}")

    # ── Vol Surface ────────────────────────────────────────────────────────

    @router.get("/vol-surface/{event}")
    async def get_vol_surface(event: str):
        loop = asyncio.get_running_loop()
        try:
            result = await loop.run_in_executor(None, _compute_vol_surface, state, event)
            if not result:
                raise HTTPException(404, f"No series data for {event}")
            return result
        except HTTPException:
            raise
        except Exception as e:
            logger.error("Vol surface failed: %s", e)
            raise HTTPException(502, f"Vol surface failed: {e}")

    # ── Regimes ───────────────────────────────────────────────────────────

    @router.get("/regimes")
    async def get_regimes():
        """Return current regime + probs for all markets from signals."""
        sig_data = signals_holder.get()
        sig_list = sig_data.get("signals", [])
        return [
            {
                "ticker": s["ticker"],
                "regime": s.get("regime", "UNKNOWN"),
                "regime_probs": s.get("regime_probs", {}),
            }
            for s in sig_list
        ]

    # ── Per-Market Risk ────────────────────────────────────────────────────

    @router.get("/market-risk/bulk")
    async def get_bulk_market_risk():
        """Compute risk metrics for all markets with active signals. Cached 60s."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, _compute_bulk_risk, signals_holder, state, position_manager
        )

    @router.get("/market-risk/{ticker}")
    async def get_market_risk(ticker: str):
        """Compute per-market risk metrics for the Signal Detail panel."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, _compute_market_risk, ticker, signals_holder, state, position_manager
        )

    # ── Positions (execution engine) ─────────────────────────────────────

    @router.get("/positions")
    async def get_positions_list():
        """All open positions with live P&L."""
        if not position_manager:
            return {"open": [], "summary": {"open_positions": 0, "portfolio_heat": 0}}
        return {
            "open": position_manager.get_open_positions(),
            "summary": position_manager.get_summary(),
        }

    @router.get("/positions/history")
    async def get_positions_history():
        """Closed positions with realized P&L."""
        if not position_manager:
            return {"closed": []}
        return {"closed": position_manager.get_closed_positions()}

    @router.get("/execution/status")
    async def get_execution_status():
        """Engine status: running/paused, heat, position count."""
        if not execution_engine:
            return {"running": False, "paused": True, "open_positions": 0}
        return execution_engine.get_status()

    @router.post("/execution/pause")
    async def pause_execution():
        if not execution_engine:
            raise HTTPException(400, "No execution engine")
        execution_engine.pause()
        return {"status": "paused"}

    @router.post("/execution/resume")
    async def resume_execution():
        if not execution_engine:
            raise HTTPException(400, "No execution engine")
        execution_engine.resume()
        return {"status": "running"}

    @router.post("/positions/{ticker}/close")
    async def manual_close_position(ticker: str):
        """Manual emergency close for a position."""
        if not position_manager:
            raise HTTPException(400, "No position manager")
        if not position_manager.has_position(ticker):
            raise HTTPException(404, f"No open position for {ticker}")
        closed = position_manager.close_position(ticker, reason="MANUAL_CLOSE")
        if not closed:
            raise HTTPException(500, "Failed to close position")
        if feed:
            feed.add(
                FeedEventType.TRADE,
                ticker=ticker,
                message=f"MANUAL CLOSE {ticker}: realized=${closed.realized_pnl:.2f}",
            )
        return closed.to_dict()

    # ── Strategy CRUD ─────────────────────────────────────────────────────

    @router.get("/strategies")
    async def list_strategies():
        if not strategy_manager:
            return []
        return strategy_manager.list_strategies()

    @router.get("/strategies/{strategy_id}")
    async def get_strategy(strategy_id: str):
        if not strategy_manager:
            raise HTTPException(404, "Strategy manager not available")
        s = strategy_manager.get_strategy(strategy_id)
        if not s:
            raise HTTPException(404, f"Strategy {strategy_id} not found")
        return s

    @router.post("/strategies")
    async def create_strategy(request: Request):
        """Create a new strategy. Accepts optional JSON body for configuration."""
        if not strategy_manager:
            raise HTTPException(500, "Strategy manager not available")
        try:
            body = await request.json()
        except Exception:
            body = {}
        return strategy_manager.create_strategy(body)

    @router.post("/strategies/{strategy_id}/update")
    async def update_strategy_post(strategy_id: str, request: Request):
        """Update strategy via POST with JSON body."""
        if not strategy_manager:
            raise HTTPException(500, "Strategy manager not available")
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(400, "Invalid JSON body")
        result = strategy_manager.update_strategy(strategy_id, body)
        if not result:
            raise HTTPException(404, f"Strategy {strategy_id} not found")
        # Sync updated strategy to execution engine
        load_strategies_from_manager(strategy_manager)
        return result

    @router.delete("/strategies/{strategy_id}")
    async def delete_strategy(strategy_id: str):
        if not strategy_manager:
            raise HTTPException(500, "Strategy manager not available")
        if strategy_manager.delete_strategy(strategy_id):
            return {"deleted": True}
        raise HTTPException(404, f"Strategy {strategy_id} not found")

    # ── Risk Engine Endpoints ──────────────────────────────────────────────

    @router.get("/risk-engine/portfolio")
    async def get_portfolio_risk():
        if not risk_engine:
            return {}
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, risk_engine.get_portfolio_risk, position_manager, signals_holder
        )

    @router.get("/risk-engine/correlations")
    async def get_risk_correlations():
        if not risk_engine:
            return {"tickers": [], "indices": [], "matrix": {}}
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, risk_engine.get_correlation_matrix, signals_holder
        )

    @router.get("/risk-engine/pnl-calendar")
    async def get_pnl_calendar():
        if not risk_engine:
            return {"daily": {}, "weeks": []}
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, risk_engine.get_pnl_calendar, position_manager
        )

    @router.get("/risk-engine/equity-curve")
    async def get_equity_curve():
        if not risk_engine:
            return {"points": [], "drawdown": []}
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, risk_engine.get_equity_curve, position_manager
        )

    @router.post("/risk-engine/kill-switch")
    async def toggle_kill_switch(activate: bool = True):
        if not risk_engine:
            raise HTTPException(500, "Risk engine not available")
        if activate:
            risk_engine.activate_kill_switch()
        else:
            risk_engine.deactivate_kill_switch()
        return {"kill_switch": risk_engine.kill_switch_active}

    # ── Analytics ──────────────────────────────────────────────────────────

    @router.get("/analytics")
    async def get_analytics():
        if not position_manager:
            return {"pnl_curve": [], "drawdown": {}, "attribution": {}, "sector_heatmap": [], "win_loss": {}}
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, _compute_analytics, position_manager)

    # ── Orderbook Health ─────────────────────────────────────────────────

    @router.get("/orderbook-health")
    async def get_orderbook_health():
        if hasattr(orderbooks, 'get_health_report'):
            return orderbooks.get_health_report()
        return {"total": 0, "fresh": 0, "stale": 0, "dead": 0}

    # ── Transaction Cost Analysis ─────────────────────────────────────────

    @router.get("/risk-engine/tca")
    async def get_tca():
        if not risk_engine:
            return {"trades": 0}
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, risk_engine.compute_tca, position_manager)

    # ── Calibration ────────────────────────────────────────────────────────

    @router.get("/calibration")
    async def get_calibration():
        """Calibration metrics: Brier score, calibration curve, go-live readiness."""
        from analysis.calibration_tracker import calibration_tracker
        return calibration_tracker.get_summary()

    # ── Health ─────────────────────────────────────────────────────────────

    @router.get("/health")
    async def health():
        return {
            "status": "ok",
            "execution_mode": "live" if os.getenv("LIVE_TRADING", "").lower() in ("1", "true", "yes") else "paper",
            "tracked_markets": len(state.tracked_tickers()),
            "feed_events": len(feed),
            "execution_engine": execution_engine.get_status() if execution_engine else None,
        }

    # ── Kalshi REST API Endpoints ─────────────────────────────────────────

    @router.get("/kalshi/markets")
    async def get_kalshi_markets(
        limit: int = Query(100, le=1000),
        status: str = Query("open"),
        series_ticker: str = Query(None),
        cursor: str = Query(None),
    ):
        """Fetch markets directly from Kalshi REST API."""
        loop = asyncio.get_running_loop()
        try:
            if series_ticker:
                markets = await loop.run_in_executor(
                    None, rest_client.get_markets_in_series, series_ticker
                )
                return {"markets": markets, "cursor": None}
            else:
                params = {"limit": limit, "status": status}
                if cursor:
                    params["cursor"] = cursor
                result = await loop.run_in_executor(
                    None, rest_client.get_markets, limit, cursor
                )
                return result
        except Exception as e:
            logger.error("Kalshi markets fetch failed: %s", e)
            raise HTTPException(502, f"Kalshi API error: {e}")

    @router.get("/kalshi/search")
    async def search_kalshi_markets(q: str = Query(..., min_length=1)):
        """Search Kalshi markets by title/ticker. Fetches from REST API and filters."""
        loop = asyncio.get_running_loop()
        try:
            all_markets = await loop.run_in_executor(
                None, lambda: rest_client.get_all_markets(limit=200)
            )
            query = q.lower()
            results = [
                m for m in all_markets
                if query in m.get("ticker", "").lower()
                or query in m.get("title", "").lower()
                or query in m.get("event_ticker", "").lower()
                or query in m.get("subtitle", "").lower()
            ][:20]
            return {"results": results, "total": len(results), "query": q}
        except Exception as e:
            logger.error("Kalshi search failed: %s", e)
            raise HTTPException(502, f"Kalshi API error: {e}")

    @router.get("/kalshi/markets/{ticker}")
    async def get_kalshi_market(ticker: str):
        """Fetch a single market directly from Kalshi REST API."""
        loop = asyncio.get_running_loop()
        try:
            result = await loop.run_in_executor(None, rest_client.get_market, ticker)
            return result
        except Exception as e:
            logger.error("Kalshi market %s fetch failed: %s", ticker, e)
            raise HTTPException(502, f"Kalshi API error: {e}")

    @router.get("/kalshi/orderbook/{ticker}")
    async def get_kalshi_orderbook(ticker: str, depth: int = Query(20, le=100)):
        """Fetch live orderbook depth from Kalshi REST API."""
        loop = asyncio.get_running_loop()
        try:
            result = await loop.run_in_executor(
                None, lambda: rest_client.get_orderbook(ticker, depth=depth)
            )
            return result
        except Exception as e:
            logger.error("Kalshi orderbook %s fetch failed: %s", ticker, e)
            raise HTTPException(502, f"Kalshi API error: {e}")

    @router.get("/kalshi/trades/{ticker}")
    async def get_kalshi_trades(ticker: str, limit: int = Query(100, le=1000)):
        """Fetch recent trades from Kalshi REST API for candlestick chart data."""
        loop = asyncio.get_running_loop()
        try:
            result = await loop.run_in_executor(
                None, lambda: rest_client.get_trades(ticker, limit=limit)
            )
            return result
        except Exception as e:
            logger.error("Kalshi trades %s fetch failed: %s", ticker, e)
            raise HTTPException(502, f"Kalshi API error: {e}")

    @router.post("/kalshi/bootstrap")
    async def bootstrap_markets():
        """Fetch all open markets from Kalshi REST API and add them to the market state tracker."""
        loop = asyncio.get_running_loop()
        try:
            markets = await loop.run_in_executor(
                None, lambda: rest_client.get_all_markets(limit=200)
            )
            added = 0
            for m in markets:
                ticker = m.get("ticker", "")
                if ticker and not state.get_market(ticker):
                    if hasattr(state, 'add_market'):
                        state.add_market({
                            "ticker": ticker,
                            "title": m.get("title", ""),
                            "category": m.get("category", ""),
                            "price": (m.get("yes_bid", 0) + m.get("yes_ask", 0)) / 200.0 if m.get("yes_bid", 0) > 1 else (m.get("yes_bid", 0) + m.get("yes_ask", 0)) / 2.0,
                            "yes_bid": m.get("yes_bid", 0) / 100.0 if m.get("yes_bid", 0) > 1 else m.get("yes_bid", 0),
                            "yes_ask": m.get("yes_ask", 0) / 100.0 if m.get("yes_ask", 0) > 1 else m.get("yes_ask", 0),
                            "volume": m.get("volume", 0),
                            "open_interest": m.get("open_interest", 0),
                            "expiration_time": m.get("expiration_time", ""),
                            "tradability_score": 0,
                            "last_update_ts": m.get("last_price_ts", ""),
                        })
                        added += 1

            return {
                "status": "ok",
                "fetched": len(markets),
                "added": added,
                "total_tracked": len(state.tracked_tickers()),
            }
        except Exception as e:
            logger.error("Market bootstrap failed: %s", e)
            raise HTTPException(502, f"Market bootstrap failed: {e}")

    # ── Morning Brief ─────────────────────────────────────────────────────

    @router.get("/morning-brief")
    async def get_morning_brief():
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, _compute_morning_brief,
            position_manager, signals_holder, risk_engine, feed, state, execution_engine
        )

    # ── Trade Journal ─────────────────────────────────────────────────────

    @router.get("/journal")
    async def get_journal(
        category: str = Query(None),
        regime: str = Query(None),
        strategy: str = Query(None),
        ticker: str = Query(None),
        exit_reason: str = Query(None),
        from_date: str = Query(None),
        to_date: str = Query(None),
        min_pnl: float = Query(None),
        max_pnl: float = Query(None),
        sort_by: str = Query("entry_time"),
        limit: int = Query(100, le=500),
    ):
        if not position_manager:
            return []
        loop = asyncio.get_running_loop()
        filters = {k: v for k, v in {
            "category": category, "regime": regime, "strategy": strategy,
            "ticker": ticker, "exit_reason": exit_reason,
            "from_date": from_date, "to_date": to_date,
            "min_pnl": min_pnl, "max_pnl": max_pnl,
        }.items() if v is not None}
        return await loop.run_in_executor(
            None, position_manager.query_journal, filters, sort_by, limit
        )

    @router.get("/journal/summary")
    async def get_journal_summary():
        if not position_manager:
            return {"total_trades": 0}
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, _compute_journal_summary, position_manager)

    @router.post("/journal/{ticker}/{entry_time}/notes")
    async def add_journal_note(ticker: str, entry_time: str, request: Request):
        if not position_manager:
            raise HTTPException(500, "Position manager not available")
        try:
            body = await request.json()
            note = body.get("note", "")
        except Exception:
            raise HTTPException(400, "Invalid JSON")
        ok = position_manager.add_journal_note(ticker, entry_time, note)
        if ok:
            return {"status": "ok"}
        raise HTTPException(500, "Failed to save note")

    # ── Alerts ────────────────────────────────────────────────────────────

    @router.get("/alerts")
    async def get_alerts_list(limit: int = Query(50, le=200), level: str = Query(None)):
        if not alert_engine:
            return []
        return alert_engine.get_alerts(limit=limit, level=level)

    @router.get("/alerts/count")
    async def get_alert_count():
        if not alert_engine:
            return {"CRITICAL": 0, "WARN": 0, "INFO": 0}
        return alert_engine.get_unacknowledged_count()

    # ── Position Risk Heatmap ─────────────────────────────────────────────

    @router.get("/risk-engine/position-heatmap")
    async def get_position_heatmap():
        if not risk_engine or not position_manager:
            return {"positions": [], "clusters": [], "category_concentration": {}, "expiry_buckets": {}}
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, risk_engine.get_position_risk_heatmap, position_manager, signals_holder, state
        )

    # ── Pipeline Refresh ─────────────────────────────────────────────────

    @router.get("/pipeline/status")
    async def get_pipeline_status():
        """Get pipeline refresh status and data freshness."""
        from engine.pipeline_refresh import get_last_refresh
        data_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")

        refresh = get_last_refresh()

        # Check file ages
        files = {}
        for name in ["market_universe.csv", "tradeable_markets.csv", "scored_markets.csv"]:
            path = os.path.join(data_dir, name)
            if os.path.exists(path):
                import pandas as pd
                mtime = os.path.getmtime(path)
                age_hours = (time.time() - mtime) / 3600
                df = pd.read_csv(path)
                files[name] = {
                    "rows": len(df),
                    "last_modified": datetime.fromtimestamp(mtime, timezone.utc).isoformat(),
                    "age_hours": round(age_hours, 1),
                    "stale": age_hours > 4,
                }
            else:
                files[name] = {"rows": 0, "last_modified": None, "stale": True}

        return {
            "last_refresh": refresh,
            "files": files,
            "tracked_markets": len(state.tracked_tickers()),
        }

    @router.post("/pipeline/refresh")
    async def trigger_pipeline_refresh(mode: str = Query("light")):
        """Trigger a pipeline refresh. mode=light (30s) or full (5min)."""
        from engine.pipeline_refresh import light_refresh, full_refresh
        loop = asyncio.get_running_loop()
        if mode == "full":
            result = await loop.run_in_executor(None, full_refresh, rest_client)
        else:
            result = await loop.run_in_executor(None, light_refresh, rest_client)

        # Reload market state
        data_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
        new_tickers = state.init_from_scored_markets(data_dir)

        return {**result, "reloaded_markets": len(new_tickers)}

    # ── Benchmarks ─────────────────────────────────────────────────────────

    @router.get("/benchmarks")
    async def get_benchmarks():
        """Get benchmark reference data for BTC and S&P 500 from tracked markets."""
        benchmarks = {}

        # Find BTC reference from our tracked markets
        all_markets = state.get_all_markets()
        btc_markets = [m for m in all_markets if "BTC" in m.get("ticker", "")]
        if btc_markets:
            # Use the highest-volume BTC market as reference
            btc_ref = max(btc_markets, key=lambda m: m.get("volume", 0))
            benchmarks["BTC"] = {
                "ticker": btc_ref["ticker"],
                "price": btc_ref.get("price", 0),
                "title": btc_ref.get("title", ""),
                "volume": btc_ref.get("volume", 0),
            }

        # Find S&P 500 / index reference
        idx_markets = [m for m in all_markets if any(x in m.get("ticker", "") for x in ["INX", "SPY", "SPX"])]
        if idx_markets:
            idx_ref = max(idx_markets, key=lambda m: m.get("volume", 0))
            benchmarks["SP500"] = {
                "ticker": idx_ref["ticker"],
                "price": idx_ref.get("price", 0),
                "title": idx_ref.get("title", ""),
                "volume": idx_ref.get("volume", 0),
            }

        # Fed rate reference
        fed_markets = [m for m in all_markets if "FED" in m.get("ticker", "")]
        if fed_markets:
            fed_ref = max(fed_markets, key=lambda m: m.get("volume", 0))
            benchmarks["FED"] = {
                "ticker": fed_ref["ticker"],
                "price": fed_ref.get("price", 0),
                "title": fed_ref.get("title", ""),
                "volume": fed_ref.get("volume", 0),
            }

        return benchmarks

    # ── QuantBrain ─────────────────────────────────────────────────────

    @router.get("/brain/status")
    async def get_brain_status():
        if not quant_brain:
            return {"active": False}
        return {**quant_brain.get_status(), "active": True}

    @router.get("/brain/lessons")
    async def get_brain_lessons():
        if not quant_brain:
            return []
        return quant_brain.learner.get_lessons_learned()

    @router.get("/brain/theses")
    async def get_brain_theses():
        if not quant_brain:
            return {}
        return {k: v.to_dict() for k, v in quant_brain._pending_theses.items()}

    @router.get("/brain/rl-policy")
    async def get_rl_policy():
        if not quant_brain:
            return {}
        return quant_brain.learner.get_performance_by_state()

    @router.get("/brain/decisions")
    async def get_brain_decisions(limit: int = Query(20, le=100)):
        if not quant_brain:
            return []
        return list(quant_brain._decision_log)[:limit]

    # ── Parlay Pricer ─────────────────────────────────────────────────

    @router.get("/parlays")
    async def get_parlay_scan():
        """Get latest parlay decomposition results — mispricings sorted by edge."""
        if not parlay_pricer:
            return []
        return parlay_pricer.get_last_scan()

    @router.post("/parlays/scan")
    async def trigger_parlay_scan():
        """Force a fresh parlay scan. Takes 30-60s due to API rate limits."""
        if not parlay_pricer:
            return {"error": "Parlay pricer not available"}
        loop = asyncio.get_running_loop()
        results = await loop.run_in_executor(None, parlay_pricer.scan_all_parlays, state)
        return {"tradeable": len(results), "results": results}

    @router.get("/parlays/{ticker}")
    async def get_parlay_detail(ticker: str):
        """Decompose a single parlay into legs with fair value."""
        if not parlay_pricer:
            return {"error": "Parlay pricer not available"}
        market = state.get_market(ticker)
        if not market:
            raise HTTPException(404, f"Market {ticker} not found")
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(None, parlay_pricer.price_parlay, market)
        if not result:
            raise HTTPException(404, "Could not decompose parlay")
        return result

    # ── External Data Feeds ──────────────────────────────────────────────

    @router.get("/external-data")
    async def get_external_data():
        """Current external market data from all feeds."""
        from data.external_feeds import feed_manager
        loop = asyncio.get_running_loop()
        data = await loop.run_in_executor(None, feed_manager.get_all_current_data)
        health = feed_manager.get_feed_health()
        return {"data": data, "health": health}

    @router.get("/external-data/probability/{ticker}")
    async def get_external_probability(ticker: str, hours: float = Query(24)):
        """Get external-data-derived probability for a specific market."""
        from data.external_feeds import feed_manager
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None, feed_manager.get_probability_for_ticker, ticker, 0.5, hours
        )
        if result is None:
            raise HTTPException(404, f"No external model for {ticker}")
        return result

    return router


def _compute_morning_brief(position_manager, signals_holder, risk_engine, feed, state, execution_engine):
    """Assemble the morning briefing for the PM."""
    from datetime import datetime, timezone, timedelta

    # Session boundary: 6PM ET previous day
    now = datetime.now(timezone.utc)
    session_start = (now - timedelta(hours=14)).replace(hour=22, minute=0, second=0)  # rough UTC approximation
    session_start_iso = session_start.isoformat()

    # 1. Overnight P&L
    overnight_trades = []
    overnight_pnl = 0
    if position_manager:
        for p in position_manager.get_closed_positions():
            if p.get("exit_time", "") > session_start_iso:
                overnight_trades.append(p)
                overnight_pnl += p.get("realized_pnl", 0)

    biggest_winner = max(overnight_trades, key=lambda t: t.get("realized_pnl", 0), default=None)
    biggest_loser = min(overnight_trades, key=lambda t: t.get("realized_pnl", 0), default=None)

    # 2. Positions at risk
    positions_at_risk = []
    open_positions = position_manager.get_open_positions() if position_manager else []
    signal_map = {s["ticker"]: s for s in signals_holder.get().get("signals", [])} if signals_holder else {}

    for pos in open_positions:
        ticker = pos.get("ticker", "")
        risk_flags = []

        # Edge decay
        current_signal = signal_map.get(ticker)
        edge_at_entry = pos.get("edge_at_entry", 0)
        current_edge = current_signal.get("edge", 0) if current_signal else 0
        if edge_at_entry != 0 and abs(current_edge) < abs(edge_at_entry) * 0.5:
            risk_flags.append("edge_decayed")
        if not current_signal:
            risk_flags.append("signal_dropped")

        # Unrealized loss
        unrealized = pos.get("unrealized_pnl", 0)
        entry_cost = pos.get("entry_cost", 0)
        if entry_cost > 0 and unrealized < -entry_cost * 0.5:
            risk_flags.append("large_loss")

        # Expiration
        market = state.get_market(ticker) if state else None
        hours_to_expiry = 999
        if market and market.get("expiration_time"):
            try:
                exp = datetime.fromisoformat(market["expiration_time"].replace("Z", "+00:00"))
                hours_to_expiry = (exp - now).total_seconds() / 3600
                if hours_to_expiry < 24:
                    risk_flags.append("expiring_soon")
            except Exception:
                pass

        if risk_flags:
            positions_at_risk.append({
                "ticker": ticker,
                "direction": pos.get("direction", ""),
                "entry_price": pos.get("entry_price", 0),
                "current_price": pos.get("current_price", 0),
                "unrealized_pnl": unrealized,
                "edge_at_entry": edge_at_entry,
                "current_edge": current_edge,
                "hours_to_expiry": round(hours_to_expiry, 1),
                "risk_flags": risk_flags,
                "hold_time_minutes": pos.get("hold_time_minutes", 0),
            })

    # 3. Top opportunities
    top_opps = []
    open_tickers = {p.get("ticker") for p in open_positions}
    for s in sorted(signals_holder.get().get("signals", []),
                     key=lambda x: abs(x.get("net_edge", 0)) * x.get("meta_quality", 0), reverse=True):
        if s["ticker"] not in open_tickers and s.get("direction") != "HOLD":
            opp = {
                "ticker": s["ticker"],
                "title": s.get("title", ""),
                "direction": s["direction"],
                "edge": s.get("edge", 0),
                "net_edge": s.get("net_edge", 0),
                "confidence": s.get("confidence", 0),
                "regime": s.get("regime", ""),
                "recommended_contracts": s.get("recommended_contracts", 0),
            }
            # Add sentiment analysis for each opportunity
            try:
                from pipeline.sentiment import get_sentiment
                sent = get_sentiment(
                    s["ticker"], s.get("title", ""), s.get("category", ""),
                    s.get("current_price", 0.5),
                )
                opp["sentiment_edge"] = sent["sentiment_edge"]
                opp["sentiment_reasoning"] = sent["reasoning"]
            except Exception:
                opp["sentiment_edge"] = 0
                opp["sentiment_reasoning"] = ""
            top_opps.append(opp)
            if len(top_opps) >= 5:
                break

    # 4. Recent alerts (from feed)
    recent_alerts = []
    if feed:
        for evt in feed.get_recent(100):
            if evt.get("ts", "") > session_start_iso:
                if evt.get("event_type") in ("ERROR", "TRADE", "SIGNAL_CHANGE"):
                    recent_alerts.append(evt)

    # 5. Expiring today
    expiring_today = []
    today_str = now.strftime("%Y-%m-%d")
    if state:
        for m in state.get_all_markets():
            exp_time = m.get("expiration_time", "")
            if exp_time.startswith(today_str):
                is_position = m["ticker"] in open_tickers
                expiring_today.append({
                    "ticker": m["ticker"],
                    "title": m.get("title", ""),
                    "price": m.get("price", 0),
                    "expiration_time": exp_time,
                    "has_position": is_position,
                })

    # 6. Portfolio snapshot
    summary = position_manager.get_summary() if position_manager else {}

    # 7. News relevant to tracked markets
    from server.news_aggregator import fetch_news_for_markets, get_market_context

    all_markets = state.get_all_markets() if state else []
    try:
        news = fetch_news_for_markets(all_markets, max_per_category=4)
    except Exception as e:
        logger.warning("News fetch failed: %s", e)
        news = []

    # 8. Market context insights
    try:
        market_context = get_market_context(all_markets)
    except Exception:
        market_context = []

    return {
        "date": now.strftime("%Y-%m-%d"),
        "time": now.strftime("%H:%M UTC"),
        "overnight_pnl": round(overnight_pnl, 2),
        "overnight_trades": len(overnight_trades),
        "biggest_winner": {"ticker": biggest_winner.get("ticker", ""), "pnl": biggest_winner.get("realized_pnl", 0)} if biggest_winner else None,
        "biggest_loser": {"ticker": biggest_loser.get("ticker", ""), "pnl": biggest_loser.get("realized_pnl", 0)} if biggest_loser else None,
        "positions_at_risk": positions_at_risk,
        "top_opportunities": top_opps,
        "recent_alerts": recent_alerts[:20],
        "expiring_today": expiring_today,
        "portfolio": summary,
        "open_positions_count": len(open_positions),
        "news": news,
        "market_context": market_context,
    }


def _compute_journal_summary(position_manager) -> dict:
    """Aggregated journal stats."""
    all_trades = position_manager.query_journal({"status": "CLOSED"}, limit=1000)
    if not all_trades:
        return {"total_trades": 0}

    pnls = [t.get("realized_pnl", 0) for t in all_trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]

    # Exit reason breakdown
    exit_reasons = {}
    strategy_breakdown = {}
    regime_breakdown = {}

    for t in all_trades:
        er = t.get("exit_reason", "UNKNOWN")
        exit_reasons[er] = exit_reasons.get(er, 0) + 1

        strat = t.get("strategy_at_entry", "unknown")
        if strat not in strategy_breakdown:
            strategy_breakdown[strat] = {"trades": 0, "pnl": 0, "wins": 0}
        strategy_breakdown[strat]["trades"] += 1
        strategy_breakdown[strat]["pnl"] += t.get("realized_pnl", 0)
        if t.get("realized_pnl", 0) > 0:
            strategy_breakdown[strat]["wins"] += 1

        regime = t.get("regime_at_entry", "unknown")
        if regime not in regime_breakdown:
            regime_breakdown[regime] = {"trades": 0, "pnl": 0, "wins": 0}
        regime_breakdown[regime]["trades"] += 1
        regime_breakdown[regime]["pnl"] += t.get("realized_pnl", 0)
        if t.get("realized_pnl", 0) > 0:
            regime_breakdown[regime]["wins"] += 1

    # Finalize breakdowns
    for d in [strategy_breakdown, regime_breakdown]:
        for k, v in d.items():
            v["win_rate"] = round(v["wins"] / v["trades"], 4) if v["trades"] > 0 else 0
            v["pnl"] = round(v["pnl"], 2)

    hold_times = [t.get("hold_time_minutes", 0) for t in all_trades if t.get("hold_time_minutes", 0) > 0]

    return {
        "total_trades": len(all_trades),
        "win_rate": round(len(wins) / len(all_trades), 4) if all_trades else 0,
        "total_pnl": round(sum(pnls), 2),
        "avg_pnl": round(sum(pnls) / len(pnls), 2) if pnls else 0,
        "avg_win": round(sum(wins) / len(wins), 2) if wins else 0,
        "avg_loss": round(sum(losses) / len(losses), 2) if losses else 0,
        "best_trade": round(max(pnls), 2) if pnls else 0,
        "worst_trade": round(min(pnls), 2) if pnls else 0,
        "avg_hold_minutes": round(sum(hold_times) / len(hold_times), 1) if hold_times else 0,
        "exit_reasons": exit_reasons,
        "by_strategy": strategy_breakdown,
        "by_regime": regime_breakdown,
    }


def _load_or_run_backtest() -> dict:
    """Load cached backtest results, or run backtest if not cached."""
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    import json
    cached_path = os.path.join(project_root, "signals", "backtest_results.json")
    try:
        with open(cached_path) as f:
            data = json.load(f)
        if data.get("total_trades", 0) > 0:
            return data
    except (FileNotFoundError, json.JSONDecodeError):
        pass

    from models.backtest import run_backtest
    return run_backtest(portfolio_value=10000)


def _run_fresh_backtest() -> dict:
    """Force a fresh backtest — rebuild features from Kalshi trades + run backtest."""
    import json
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    logger.info("Starting fresh backtest — rebuilding features from trade history...")

    # Step 1: Build fresh features from Kalshi trade history for scored markets
    data_dir = os.path.join(project_root, "data")
    scored_path = os.path.join(data_dir, "scored_markets.csv")

    try:
        import pandas as pd
        import numpy as np
        from app.kalshi_client import KalshiClient
        from datetime import datetime, timezone
        import time as _time

        scored = pd.read_csv(scored_path)
        client = KalshiClient()

        all_features = []
        fetched = 0

        for _, row in scored.iterrows():
            ticker = row["ticker"]

            # Fetch trade history from Kalshi REST API (paginate for depth)
            try:
                trades = client.paginate(
                    "/trade-api/v2/markets/trades", "trades",
                    {"limit": 100, "ticker": ticker}, max_pages=10
                )
            except Exception as e:
                logger.debug("Trade fetch failed for %s: %s", ticker, e)
                _time.sleep(0.5)
                continue

            if len(trades) < 10:
                continue

            # Convert trades to feature rows (5-minute OHLCV bars)
            trade_rows = []
            for t in trades:
                # Support API v2 (yes_price_dollars) and v1 (yes_price cents)
                if "yes_price_dollars" in t:
                    price = float(t["yes_price_dollars"])
                else:
                    price = t.get("yes_price", 0)
                    if isinstance(price, str):
                        price = float(price)
                    if price > 1:
                        price = price / 100.0
                volume = int(float(t.get("count_fp", t.get("count", 1))))
                trade_rows.append({
                    "time": pd.Timestamp(t.get("created_time", "")),
                    "price": price,
                    "volume": max(volume, 1),
                })

            if not trade_rows:
                continue

            tdf = pd.DataFrame(trade_rows).sort_values("time").set_index("time")

            # Resample to 5-minute OHLCV bars
            ohlcv = tdf["price"].resample("5min").ohlc().dropna()
            vol = tdf["volume"].resample("5min").sum()
            ohlcv["volume"] = vol
            ohlcv = ohlcv.dropna()

            if len(ohlcv) < 10:
                continue

            # Build feature rows
            for ts, bar in ohlcv.iterrows():
                close = float(bar["close"])
                spread = float(bar.get("high", close)) - float(bar.get("low", close))

                all_features.append({
                    "timestamp": ts,
                    "ticker": ticker,
                    "category": row.get("category", ""),
                    "regime": row.get("regime", "UNKNOWN"),
                    "open": float(bar["open"]),
                    "high": float(bar["high"]),
                    "low": float(bar["low"]),
                    "close": close,
                    "volume": int(bar["volume"]),
                    "volume_1h": int(bar["volume"]),
                    "spread": round(spread, 4),
                    "mid_price": close,
                    "spread_pct": round(spread / close, 4) if close > 0 else 0,
                    "orderbook_imbalance": 0,
                    "volatility_1h": 0,
                    "time_to_expiry_hours": 999,
                })

            fetched += 1
            if fetched % 5 == 0:
                _time.sleep(0.5)  # Rate limit

        if not all_features:
            logger.warning("No features generated — not enough trade data")
            return _load_or_run_backtest()

        features_df = pd.DataFrame(all_features)
        features_df = features_df.set_index("timestamp")
        features_df.index.name = None

        # Compute rolling features
        for ticker in features_df["ticker"].unique():
            mask = features_df["ticker"] == ticker
            closes = features_df.loc[mask, "close"]
            features_df.loc[mask, "volatility_1h"] = closes.rolling(12, min_periods=2).std().fillna(0)

        # Save fresh features
        features_path = os.path.join(data_dir, "clean_features.csv")
        features_df.to_csv(features_path)
        logger.info("Generated %d feature rows from %d tickers", len(features_df), fetched)

    except Exception as e:
        logger.error("Feature rebuild failed: %s — using existing features", e)

    # Step 2: Run backtest on fresh features
    from models.backtest import run_backtest
    result = run_backtest(portfolio_value=10000)
    logger.info("Fresh backtest complete: %d trades, $%.2f P&L",
                result.get("total_trades", 0), result.get("final_pnl", 0))
    return result


def _compute_sentiment(ticker: str, title: str, category: str, price: float) -> dict:
    """Synchronous sentiment computation (runs in executor)."""
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    from pipeline.sentiment import get_sentiment
    return get_sentiment(ticker, title, category, price)


def _compute_arbitrage(state) -> list:
    """Synchronous arbitrage scan (runs in executor)."""
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    from models.arbitrage import scan_arbitrage
    markets = state.get_all_markets()
    return scan_arbitrage(markets)


def _compute_correlations(state) -> dict:
    """Synchronous correlation matrix computation (runs in executor)."""
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    import numpy as np
    from models.features import load_features

    features = load_features()
    pivot = features.pivot_table(index=features.index, columns="ticker", values="close")
    pivot = pivot.ffill().dropna(axis=1, how="all")
    corr = pivot.corr()

    tickers = list(corr.columns)
    matrix = []
    for i, t1 in enumerate(tickers):
        for j, t2 in enumerate(tickers):
            if i <= j:
                val = float(corr.loc[t1, t2])
                if np.isfinite(val):
                    matrix.append({"t1": t1, "t2": t2, "corr": round(val, 3)})

    # Flag divergences: highly correlated pairs with abnormal spread
    divergences = []
    for entry in matrix:
        if entry["t1"] != entry["t2"] and abs(entry["corr"]) > 0.7:
            m1 = state.get_market(entry["t1"])
            m2 = state.get_market(entry["t2"])
            if m1 and m2:
                spread = abs(m1["price"] - m2["price"])
                if spread > 0.10:
                    divergences.append({
                        "t1": entry["t1"], "t2": entry["t2"],
                        "correlation": entry["corr"],
                        "spread": round(spread, 4),
                        "signal": "MEAN_REVERSION",
                    })

    return {"tickers": tickers, "matrix": matrix, "divergences": divergences}


def _compute_vol_surface(state, event_prefix: str) -> dict | None:
    """Synchronous vol surface computation (runs in executor)."""
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    from models.vol_surface import get_vol_surface_for_event
    markets = state.get_all_markets()
    return get_vol_surface_for_event(markets, event_prefix)


def _load_history_from_csv(ticker: str, limit: int) -> list[dict]:
    """Load historical price data from clean_features.csv as fallback."""
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    csv_path = os.path.join(project_root, "data", "clean_features.csv")
    try:
        import pandas as pd
        df = pd.read_csv(csv_path, index_col=0, parse_dates=True)
        sub = df[df["ticker"] == ticker].copy()
        if sub.empty:
            return []
        sub = sub.sort_index().tail(limit)
        result = []
        for ts, row in sub.iterrows():
            price = float(row.get("close", 0))
            result.append({
                "ts": ts.isoformat(),
                "price": price,
                "yes_bid": float(row.get("close", price)) - float(row.get("spread", 0.02)) / 2,
                "yes_ask": float(row.get("close", price)) + float(row.get("spread", 0.02)) / 2,
                "volume": int(row.get("volume_1h", row.get("volume", 0))),
            })
        return result
    except Exception as e:
        logger.warning("CSV history fallback failed for %s: %s", ticker, e)
        return []


def _compute_risk(signals_holder) -> dict:
    """Synchronous risk computation (runs in executor)."""
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    from models.features import load_features
    from models.risk_model import RiskModel

    signals_data = signals_holder.get()
    signals_list = signals_data.get("signals", [])

    features = load_features()
    risk = RiskModel(portfolio_value=10000)
    risk.fit(features)

    positions = [
        {
            "ticker": s["ticker"],
            "contracts": s["recommended_contracts"],
            "current_price": s["current_price"],
        }
        for s in signals_list
        if s.get("recommended_contracts", 0) > 0
    ]

    var_95 = risk.portfolio_var(positions)

    return {
        "var_95": var_95,
        "positions": positions,
    }


def _compute_analytics(position_manager) -> dict:
    """Compute all analytics from position history (runs in executor)."""
    from datetime import datetime, timezone
    from collections import defaultdict

    positions = position_manager.get_all_positions_chronological()
    if not positions:
        return {
            "pnl_curve": [],
            "drawdown": {
                "max_drawdown_pct": 0, "max_drawdown_dollars": 0,
                "current_drawdown_pct": 0, "current_drawdown_dollars": 0,
                "drawdown_duration_minutes": 0, "drawdown_curve": [],
            },
            "attribution": {"by_category": {}, "by_regime": {}, "by_strategy": {}, "by_hour": {}},
            "sector_heatmap": [],
            "win_loss": {
                "total_wins": 0, "total_losses": 0,
                "avg_win": 0, "avg_loss": 0,
                "largest_win": 0, "largest_loss": 0,
                "max_consecutive_wins": 0, "max_consecutive_losses": 0,
                "current_streak": 0, "current_streak_type": "none",
                "win_distribution": [], "loss_distribution": [],
            },
            "monthly_returns": [],
            "rolling_sharpe": [],
        }

    # ── P&L curve ──────────────────────────────────────────────────────
    closed = [p for p in positions if p.get("status") == "CLOSED" and p.get("exit_time")]
    closed.sort(key=lambda p: p.get("exit_time", ""))

    pnl_curve = []
    cumulative = 0.0
    for p in closed:
        cumulative += p.get("realized_pnl", 0)
        pnl_curve.append({
            "ts": p["exit_time"],
            "cumulative_pnl": round(cumulative, 2),
            "unrealized": 0,
        })

    # Append current unrealized from open positions
    open_positions = [p for p in positions if p.get("status") in ("OPEN", "PARTIAL")]
    total_unrealized = sum(p.get("unrealized_pnl", 0) for p in open_positions)
    if open_positions:
        pnl_curve.append({
            "ts": datetime.now(timezone.utc).isoformat(),
            "cumulative_pnl": round(cumulative + total_unrealized, 2),
            "unrealized": round(total_unrealized, 2),
        })

    # ── Drawdown ───────────────────────────────────────────────────────
    bankroll = position_manager.bankroll
    peak = bankroll
    max_dd_pct = 0.0
    max_dd_dollars = 0.0
    dd_curve = []
    dd_start_ts = None

    for point in pnl_curve:
        equity = bankroll + point["cumulative_pnl"]
        peak = max(peak, equity)
        dd_dollars = peak - equity
        dd_pct = dd_dollars / peak if peak > 0 else 0
        if dd_pct > max_dd_pct:
            max_dd_pct = dd_pct
            max_dd_dollars = dd_dollars
        dd_curve.append({"ts": point["ts"], "drawdown_pct": round(dd_pct, 4)})

    current_equity = bankroll + (pnl_curve[-1]["cumulative_pnl"] if pnl_curve else 0)
    current_dd_dollars = max(0, peak - current_equity)
    current_dd_pct = current_dd_dollars / peak if peak > 0 else 0

    drawdown = {
        "max_drawdown_pct": round(max_dd_pct, 4),
        "max_drawdown_dollars": round(max_dd_dollars, 2),
        "current_drawdown_pct": round(current_dd_pct, 4),
        "current_drawdown_dollars": round(current_dd_dollars, 2),
        "drawdown_duration_minutes": 0,
        "drawdown_curve": dd_curve,
    }

    # ── Attribution ────────────────────────────────────────────────────
    by_category = defaultdict(lambda: {"pnl": 0, "trades": 0, "wins": 0})
    by_regime = defaultdict(lambda: {"pnl": 0, "trades": 0, "wins": 0})
    by_strategy = defaultdict(lambda: {"pnl": 0, "trades": 0, "wins": 0})
    by_hour = defaultdict(lambda: {"pnl": 0, "trades": 0})

    for p in closed:
        pnl = p.get("realized_pnl", 0)
        cat = p.get("category", "Unknown") or "Unknown"
        regime = p.get("regime_at_entry", "Unknown") or "Unknown"
        strategy = p.get("strategy_at_entry", "convergence") or "convergence"

        by_category[cat]["pnl"] += pnl
        by_category[cat]["trades"] += 1
        if pnl > 0:
            by_category[cat]["wins"] += 1

        by_regime[regime]["pnl"] += pnl
        by_regime[regime]["trades"] += 1
        if pnl > 0:
            by_regime[regime]["wins"] += 1

        by_strategy[strategy]["pnl"] += pnl
        by_strategy[strategy]["trades"] += 1
        if pnl > 0:
            by_strategy[strategy]["wins"] += 1

        try:
            hour = datetime.fromisoformat(p["entry_time"]).hour
            by_hour[str(hour)]["pnl"] += pnl
            by_hour[str(hour)]["trades"] += 1
        except Exception:
            pass

    def finalize_attr(d):
        result = {}
        for k, v in d.items():
            entry = {"pnl": round(v["pnl"], 2), "trades": v["trades"]}
            if "wins" in v:
                entry["win_rate"] = round(v["wins"] / v["trades"], 4) if v["trades"] > 0 else 0
            result[k] = entry
        return result

    attribution = {
        "by_category": finalize_attr(by_category),
        "by_regime": finalize_attr(by_regime),
        "by_strategy": finalize_attr(by_strategy),
        "by_hour": finalize_attr(by_hour),
    }

    # ── Sector heatmap ─────────────────────────────────────────────────
    sector_heatmap = []
    for cat, stats in by_category.items():
        avg_hold = 0
        cat_trades = [p for p in closed if (p.get("category") or "Unknown") == cat]
        if cat_trades:
            hold_times = []
            for p in cat_trades:
                try:
                    entry = datetime.fromisoformat(p["entry_time"])
                    exit_t = datetime.fromisoformat(p["exit_time"])
                    hold_times.append((exit_t - entry).total_seconds() / 60)
                except Exception:
                    pass
            avg_hold = sum(hold_times) / len(hold_times) if hold_times else 0

        sector_heatmap.append({
            "category": cat,
            "pnl": round(stats["pnl"], 2),
            "trades": stats["trades"],
            "win_rate": round(stats["wins"] / stats["trades"], 4) if stats["trades"] > 0 else 0,
            "avg_hold_minutes": round(avg_hold, 1),
        })

    # ── Win/Loss analysis ──────────────────────────────────────────────
    pnls = [p.get("realized_pnl", 0) for p in closed]
    wins = [x for x in pnls if x > 0]
    losses = [x for x in pnls if x < 0]

    max_consec_wins = max_consec_losses = current_streak = 0
    current_type = "none"
    cw = cl = 0
    for pnl in pnls:
        if pnl > 0:
            cw += 1
            cl = 0
            max_consec_wins = max(max_consec_wins, cw)
        elif pnl < 0:
            cl += 1
            cw = 0
            max_consec_losses = max(max_consec_losses, cl)
        else:
            cw = cl = 0

    if pnls:
        if pnls[-1] > 0:
            current_type = "win"
            current_streak = cw
        elif pnls[-1] < 0:
            current_type = "loss"
            current_streak = cl

    win_loss = {
        "total_wins": len(wins),
        "total_losses": len(losses),
        "avg_win": round(sum(wins) / len(wins), 2) if wins else 0,
        "avg_loss": round(sum(losses) / len(losses), 2) if losses else 0,
        "largest_win": round(max(wins), 2) if wins else 0,
        "largest_loss": round(min(losses), 2) if losses else 0,
        "max_consecutive_wins": max_consec_wins,
        "max_consecutive_losses": max_consec_losses,
        "current_streak": current_streak,
        "current_streak_type": current_type,
        "win_distribution": [round(w, 2) for w in sorted(wins)],
        "loss_distribution": [round(l, 2) for l in sorted(losses, reverse=True)],
    }

    # ── Monthly returns ────────────────────────────────────────────────
    monthly = defaultdict(lambda: {"gross": 0, "net": 0, "trades": 0, "wins": 0})
    for p in closed:
        try:
            month = p.get("exit_time", "")[:7]  # "YYYY-MM"
            if month:
                monthly[month]["net"] += p.get("realized_pnl", 0)
                monthly[month]["gross"] += abs(p.get("realized_pnl", 0) + p.get("fees_paid", 0))
                monthly[month]["trades"] += 1
                if p.get("realized_pnl", 0) > 0:
                    monthly[month]["wins"] += 1
        except Exception:
            pass

    monthly_returns = []
    for month, stats in sorted(monthly.items()):
        monthly_returns.append({
            "month": month,
            "net_return": round(stats["net"], 2),
            "trades": stats["trades"],
            "win_rate": round(stats["wins"] / stats["trades"], 4) if stats["trades"] > 0 else 0,
        })

    # ── Rolling Sharpe (30-trade window) ──────────────────────────────
    rolling_sharpe = []
    import numpy as np
    if len(pnls) >= 10:
        window = min(30, len(pnls))
        for i in range(window, len(pnls) + 1):
            window_pnls = pnls[i-window:i]
            std = np.std(window_pnls, ddof=1)
            sharpe = np.mean(window_pnls) / std if std > 0 else 0
            rolling_sharpe.append({
                "trade_num": i,
                "sharpe": round(float(sharpe), 4),
                "ts": closed[i-1].get("exit_time", "") if i-1 < len(closed) else "",
            })

    return {
        "pnl_curve": pnl_curve,
        "drawdown": drawdown,
        "attribution": attribution,
        "sector_heatmap": sector_heatmap,
        "win_loss": win_loss,
        "monthly_returns": monthly_returns,
        "rolling_sharpe": rolling_sharpe,
    }


def _compute_bulk_risk(signals_holder, state, position_manager) -> dict:
    """Compute risk for all signaled markets in one call."""
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    from models.risk_model import kalshi_fee_rt

    signals_data = signals_holder.get() if signals_holder else {}
    result = {}

    for sig in signals_data.get("signals", []):
        ticker = sig["ticker"]
        price = sig.get("current_price", 0.5)
        edge = sig.get("edge", 0)
        confidence = sig.get("confidence", 0)

        fee_rt = kalshi_fee_rt(price)
        vol_est = min(abs(price * 0.15), 0.20)
        var95 = round(vol_est * 1.645, 4)

        # EV per contract
        net_edge = abs(edge) - fee_rt
        ev = round(net_edge * confidence, 4) if net_edge > 0 else 0

        result[ticker] = {
            "var95": var95,
            "fee_rt": round(fee_rt, 4),
            "ev_per_contract": ev,
            "net_edge": round(net_edge, 4),
        }

    return result


def _compute_market_risk(ticker: str, signals_holder, state, position_manager) -> dict:
    """Compute per-market risk metrics for the Signal Detail panel."""
    import math
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    from models.risk_model import RiskModel, kalshi_fee, kalshi_fee_rt
    from models.features import load_features

    sig_data = signals_holder.get()
    signal = None
    for s in sig_data.get("signals", []):
        if s["ticker"] == ticker:
            signal = s
            break

    market = state.get_market(ticker) if state else None
    price = market["price"] if market and market["price"] > 0 else (signal["current_price"] if signal else 0.5)

    # Base risk computations
    fee_rt = kalshi_fee_rt(price)

    # VaR for 1 contract
    vol_est = min(abs(price * 0.15), 0.20)  # ~15% of price or 20c max
    var95_1ct = round(vol_est * 1.645, 4)
    var99_1ct = round(vol_est * 2.326, 4)

    # Max loss for 1 contract
    if signal:
        direction = signal.get("direction", "BUY_YES")
        if direction == "BUY_YES":
            max_loss_1ct = round(price + fee_rt, 4)
        else:
            max_loss_1ct = round((1.0 - price) + fee_rt, 4)
    else:
        max_loss_1ct = round(price + fee_rt, 4)

    # Win/loss probability from signal confidence
    confidence = signal["confidence"] if signal else 0.5
    edge = signal["edge"] if signal else 0
    pred_agrees = signal.get("price_prediction_1h", 0) != 0 and (
        (signal.get("price_prediction_1h", 0) > 0 and edge > 0) or
        (signal.get("price_prediction_1h", 0) < 0 and edge < 0)
    ) if signal else False

    # Probability of win — derived from confidence + edge sign agreement
    prob_win = max(0.1, min(0.9, 0.5 + confidence * 0.3 + (0.05 if pred_agrees else 0)))
    prob_loss = 1.0 - prob_win

    # Expected value per contract
    win_payout = abs(edge) if signal else 0
    loss_amount = var95_1ct
    ev = round(prob_win * win_payout - prob_loss * loss_amount - fee_rt, 4)

    # Kelly
    if loss_amount > 0 and win_payout > 0:
        kelly_raw = (prob_win * win_payout - prob_loss * loss_amount) / win_payout if win_payout > 0 else 0
        kelly_pct = round(max(0, kelly_raw) * 100, 2)
    else:
        kelly_pct = 0

    half_kelly = round(kelly_pct / 2, 2)

    # Sharpe/Sortino estimates (from signal data if available)
    risk_details = signal.get("risk", {}) if signal else {}
    sharpe_est = round(ev / vol_est if vol_est > 0 else 0, 2)
    sortino_est = round(sharpe_est * 1.2, 2)  # approximate

    # Max drawdown estimate
    max_dd = round(-var99_1ct * (risk_details.get("contracts", 1)), 2)

    # Correlations — compute from features if available
    corr_sp = 0.0
    corr_btc = 0.0
    try:
        features = load_features()
        ticker_data = features[features["ticker"] == ticker]
        if len(ticker_data) >= 20 and "close" in ticker_data.columns:
            import numpy as np

            def _safe_corr(a, b):
                if len(a) < 5 or np.std(a) == 0 or np.std(b) == 0:
                    return 0.0
                c = np.corrcoef(a, b)[0, 1]
                return float(c) if np.isfinite(c) else 0.0

            returns = ticker_data["close"].pct_change(fill_method=None).dropna().values
            all_returns = features.pivot_table(index=features.index, columns="ticker", values="close").pct_change(fill_method=None).dropna()
            if len(all_returns) >= 20:
                avg_market = all_returns.mean(axis=1).values[-len(returns):]
                if len(avg_market) == len(returns):
                    corr_sp = round(_safe_corr(returns, avg_market), 2)
                btc_cols = [c for c in all_returns.columns if "BTC" in c.upper()]
                if btc_cols:
                    btc_returns = all_returns[btc_cols[0]].values[-len(returns):]
                    if len(btc_returns) == len(returns):
                        corr_btc = round(_safe_corr(returns, btc_returns), 2)
    except Exception:
        pass

    # Liquidity risk
    vol_24h = market.get("volume", 0) if market else 0
    spread = (market.get("yes_ask", 0) - market.get("yes_bid", 0)) if market else 0
    liq_risk = "LOW" if vol_24h > 1000 and spread < 0.05 else "MED" if vol_24h > 100 else "HIGH"

    return {
        "ticker": ticker,
        "var95": round(var95_1ct, 4),
        "var99": round(var99_1ct, 4),
        "max_loss_1ct": round(max_loss_1ct, 4),
        "prob_win": round(prob_win, 4),
        "prob_loss": round(prob_loss, 4),
        "expected_value": ev,
        "kelly_pct": kelly_pct,
        "half_kelly_pct": half_kelly,
        "sharpe_7d": sharpe_est,
        "sortino_7d": sortino_est,
        "max_drawdown": max_dd,
        "corr_sp500": corr_sp,
        "corr_btc": corr_btc,
        "liquidity_risk": liq_risk,
    }
