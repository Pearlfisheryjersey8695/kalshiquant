"""
REST API endpoints for the FastAPI server.
All model/risk calls use run_in_executor to avoid blocking the async loop.
"""

import asyncio
import logging
import os
import sys

from fastapi import APIRouter, HTTPException, Query

from engine.feed import FeedLog
from engine.market_state import MarketStateStore
from engine.orderbook import OrderbookStore
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
        loop = asyncio.get_event_loop()
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
        loop = asyncio.get_event_loop()
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
        if not hist and not state.get_market(ticker):
            raise HTTPException(404, f"Market {ticker} not found")
        return hist

    # ── Feed ───────────────────────────────────────────────────────────────

    @router.get("/feed", response_model=list[FeedEventResponse])
    async def get_feed(limit: int = Query(50, le=500)):
        return feed.get_recent(limit=limit)

    # ── Backtest ──────────────────────────────────────────────────────────

    @router.get("/backtest")
    async def get_backtest():
        loop = asyncio.get_event_loop()
        try:
            # Try cached results first
            result = await loop.run_in_executor(None, _load_or_run_backtest)
            return result
        except Exception as e:
            logger.error("Backtest failed: %s", e)
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
        loop = asyncio.get_event_loop()
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
        loop = asyncio.get_event_loop()
        try:
            result = await loop.run_in_executor(None, _compute_arbitrage, state)
            return result
        except Exception as e:
            logger.error("Arbitrage scan failed: %s", e)
            raise HTTPException(502, f"Arbitrage scan failed: {e}")

    # ── Correlations ────────────────────────────────────────────────────

    @router.get("/correlations")
    async def get_correlations():
        loop = asyncio.get_event_loop()
        try:
            result = await loop.run_in_executor(None, _compute_correlations, state)
            return result
        except Exception as e:
            logger.error("Correlation computation failed: %s", e)
            raise HTTPException(502, f"Correlation computation failed: {e}")

    # ── Vol Surface ────────────────────────────────────────────────────────

    @router.get("/vol-surface/{event}")
    async def get_vol_surface(event: str):
        loop = asyncio.get_event_loop()
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

    # ── Health ─────────────────────────────────────────────────────────────

    @router.get("/health")
    async def health():
        return {
            "status": "ok",
            "tracked_markets": len(state.tracked_tickers()),
            "feed_events": len(feed),
        }

    return router


def _load_or_run_backtest() -> dict:
    """Load cached backtest results, or run backtest if not cached."""
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    import json
    cached_path = os.path.join(project_root, "signals", "backtest_results.json")
    try:
        with open(cached_path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        pass

    from models.backtest import run_backtest
    return run_backtest(portfolio_value=10000)


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
