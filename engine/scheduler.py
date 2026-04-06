"""
Periodic task scheduler for the real-time engine.
Four loops:
  - 30s:  Broadcast price snapshots + update position P&L + quick stop-loss check
  - 5min: Re-run ensemble signals + execution engine (entries/exits)
  - 1h:   Full model refit from batch data

Signal pipeline priority:
  1. LIVE: compute_live_features() -> run_live_ensemble() (requires 20+ snapshots)
  2. BATCH FALLBACK: run_ensemble() with live price overlay (cold start / insufficient WS data)
  3. DEMO FALLBACK: backtest-derived signals (if both above fail)
"""

import asyncio
import logging
import os
import sys
import time
from datetime import datetime, timezone

from engine.feed import FeedLog, FeedEventType
from engine.market_state import MarketStateStore
from engine.orderbook import OrderbookStore

logger = logging.getLogger("kalshi.scheduler")

# Intervals in seconds
PRICE_INTERVAL = 5   # was 30 — every 5 seconds for near-real-time
SIGNAL_INTERVAL = 300   # 5 minutes
REFIT_INTERVAL = 3600   # 1 hour


class Scheduler:
    def __init__(
        self,
        state: MarketStateStore,
        orderbooks: OrderbookStore,
        feed: FeedLog,
        ws_manager,
        signals_holder,
        execution_engine=None,
        position_manager=None,
        risk_engine=None,
        alert_engine=None,
    ):
        self._state = state
        self._orderbooks = orderbooks
        self._feed = feed
        self._ws_manager = ws_manager
        self._signals_holder = signals_holder
        self._execution_engine = execution_engine
        self._position_manager = position_manager
        self._risk_engine = risk_engine
        self._alert_engine = alert_engine
        self._tasks: list[asyncio.Task] = []
        self._signal_lock = asyncio.Lock()  # prevent concurrent runs

        # Live model manager (initialized lazily in executor)
        self._model_mgr = None

        # Pipeline refresh state
        self._kalshi_client = None  # set externally
        self._last_light_refresh = 0
        self._last_full_refresh = 0

    def start(self) -> None:
        loop = asyncio.get_running_loop()
        self._tasks = [
            loop.create_task(self._price_loop()),
            loop.create_task(self._model_init_task()),
            loop.create_task(self._signal_loop()),
            loop.create_task(self._refit_loop()),
            loop.create_task(self._pipeline_refresh_loop()),
        ]
        logger.info("Scheduler started (5s/5min/1h loops + pipeline refresh)")

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks = []

    async def _model_init_task(self) -> None:
        """Fit models from batch data at startup (background, non-blocking)."""
        try:
            await asyncio.sleep(5)  # let WS connect first
            logger.info("Fitting models from batch data (background)...")
            loop = asyncio.get_running_loop()
            ok = await loop.run_in_executor(None, self._init_models_sync)
            if ok:
                logger.info("Models ready for live inference")
                self._feed.add(
                    FeedEventType.CONNECTION,
                    message="Models fitted — live signal pipeline active",
                )
                # Run signals immediately (don't wait for next 5-min cycle)
                await self._run_signals()
            else:
                logger.warning("Model fitting failed, will use batch fallback")
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error("Model init failed: %s", e)

    def _init_models_sync(self) -> bool:
        """Synchronous model fitting (runs in executor)."""
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if project_root not in sys.path:
            sys.path.insert(0, project_root)

        from engine.live_ensemble import LiveModelManager
        self._model_mgr = LiveModelManager()
        return self._model_mgr.fit_from_batch(portfolio_value=10000)

    async def _price_loop(self) -> None:
        """Every 5s: broadcast prices, update position P&L, quick stop-loss."""
        while True:
            try:
                await asyncio.sleep(PRICE_INTERVAL)
                snapshot = self._state.snapshot_all()
                await self._ws_manager.broadcast_prices(snapshot)

                # Update position P&L and check stop-losses
                if self._position_manager and self._execution_engine:
                    self._position_manager.update_prices(self._state)
                    exits = self._execution_engine.check_stop_losses_only()
                    if exits:
                        logger.info("Stop-loss exits: %d", len(exits))
                    # Broadcast position updates
                    await self._broadcast_positions()

                # Auto kill-switch check
                if self._risk_engine and self._position_manager:
                    risk_status = self._risk_engine.check_risk_limits(
                        self._position_manager, self._signals_holder
                    )
                    if risk_status.get("should_kill") and not self._risk_engine.kill_switch_active:
                        self._risk_engine.activate_kill_switch()
                        if self._execution_engine:
                            self._execution_engine.pause()
                        self._feed.add(
                            FeedEventType.ERROR,
                            message=f"AUTO KILL-SWITCH: {', '.join(risk_status['kill_reasons'])}",
                        )
                        logger.warning("Auto kill-switch triggered: %s", risk_status['kill_reasons'])

                # Evaluate smart alerts
                if self._alert_engine:
                    try:
                        self._alert_engine.evaluate(
                            self._position_manager, self._signals_holder,
                            self._risk_engine, self._state, self._execution_engine
                        )
                    except Exception as e:
                        logger.warning("Alert evaluation failed: %s", e)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Price broadcast error: %s", e)

    async def _signal_loop(self) -> None:
        """Every 5min: compute live features -> score with trained models -> broadcast."""
        # Wait for initial data to accumulate
        await asyncio.sleep(60)

        while True:
            try:
                await asyncio.sleep(SIGNAL_INTERVAL)
                logger.info("Running signal refresh...")
                await self._run_signals()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Signal refresh error: %s", e)

    async def _refit_loop(self) -> None:
        """Every 1h: refit models from batch data + run signals."""
        await asyncio.sleep(REFIT_INTERVAL)

        while True:
            try:
                logger.info("Running full model refit...")
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, self._refit_models_sync)
                await self._run_signals()
                await asyncio.sleep(REFIT_INTERVAL)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Model refit error: %s", e)
                await asyncio.sleep(REFIT_INTERVAL)

    async def _pipeline_refresh_loop(self):
        """Refresh pipeline: light every 30min, full every 4h."""
        await asyncio.sleep(10)  # initial delay

        LIGHT_INTERVAL = 1800   # 30 minutes
        FULL_INTERVAL = 14400   # 4 hours

        while True:
            try:
                loop = asyncio.get_running_loop()
                now = time.time()

                if self._kalshi_client:
                    from engine.pipeline_refresh import light_refresh, full_refresh

                    if now - self._last_full_refresh > FULL_INTERVAL:
                        logger.info("Starting full pipeline refresh...")
                        result = await loop.run_in_executor(None, full_refresh, self._kalshi_client)
                        self._last_full_refresh = now
                        self._last_light_refresh = now  # full includes light

                        # Reload market state from fresh scored data
                        data_dir = os.path.join(
                            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data"
                        )
                        new_tickers = self._state.init_from_scored_markets(data_dir)
                        logger.info("Reloaded %d markets from fresh scored data", len(new_tickers))

                        self._feed.add(
                            FeedEventType.CONNECTION,
                            message=f"Pipeline refresh: {result.get('scored_count', 0)} markets scored"
                        )

                    elif now - self._last_light_refresh > LIGHT_INTERVAL:
                        logger.info("Starting light pipeline refresh...")
                        result = await loop.run_in_executor(None, light_refresh, self._kalshi_client)
                        self._last_light_refresh = now

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Pipeline refresh failed: %s", e)

            await asyncio.sleep(300)  # check every 5 min

    def _refit_models_sync(self) -> None:
        """Refit models from batch data (1h cycle)."""
        if self._model_mgr:
            self._model_mgr.fit_from_batch(portfolio_value=10000)
        else:
            self._init_models_sync()

    async def _run_signals(self) -> None:
        """
        Run the signal pipeline. Tries live features first, falls back to batch.
        Uses asyncio.Lock to prevent concurrent runs.
        """
        if self._signal_lock.locked():
            logger.info("Signal run skipped — previous run still in progress")
            return

        async with self._signal_lock:
            # Refresh external data feeds before signal computation
            try:
                from data.external_feeds import feed_manager
                loop_ext = asyncio.get_running_loop()
                ext_data = await loop_ext.run_in_executor(None, feed_manager.get_all_current_data)
                if ext_data:
                    logger.debug("External feeds refreshed: %s", list(ext_data.keys()))
            except Exception as e:
                logger.warning("External feed refresh failed: %s", e)

            loop = asyncio.get_running_loop()
            result = None
            source = "none"

            # ── Try 1: Live feature pipeline ──────────────────────────
            if self._model_mgr and self._model_mgr.is_fitted:
                try:
                    result = await loop.run_in_executor(
                        None, self._run_live_ensemble_sync
                    )
                    if result and result.get("signals"):
                        source = "live"
                        logger.info(
                            "LIVE signal pipeline: %d signals from WebSocket data",
                            len(result["signals"]),
                        )
                    elif result:
                        logger.info(
                            "Live ensemble scored markets but 0 signals passed filters "
                            "(total_signals=%d), falling back to batch",
                            result.get("total_signals", 0),
                        )
                        result = None  # force batch fallback
                except Exception as e:
                    logger.warning("Live ensemble failed: %s, falling back to batch", e)
                    result = None

            # ── Try 2: Batch fallback ──────────────────────────────────
            if not result or not result.get("signals"):
                try:
                    result = await loop.run_in_executor(
                        None, self._run_batch_ensemble_sync
                    )
                    if result and result.get("signals"):
                        source = "batch"
                        self._overlay_live_prices(result)
                        logger.info(
                            "BATCH signal pipeline (fallback): %d signals",
                            len(result["signals"]),
                        )
                except Exception as e:
                    logger.error("Batch ensemble failed: %s", e)
                    result = None

            # ── Try 3: Demo fallback ──────────────────────────────────
            if not result or not result.get("signals"):
                try:
                    from engine.demo_mode import load_demo_signals
                    result = load_demo_signals(portfolio_value=10000)
                    # Filter demo signals too — remove expired/dead markets
                    self._overlay_live_prices(result)
                    source = "demo"
                    logger.info("DEMO signal pipeline (fallback): %d signals after live filter",
                                len(result.get("signals", [])))
                except Exception as e:
                    logger.warning("Demo mode failed: %s", e)
                    return

            if result and result.get("signals"):
                # Tag signal source
                result["signal_source"] = source

                # ── Merge parlay signals (SIG sports playbook) ────────
                if hasattr(self, '_parlay_pricer') and self._parlay_pricer:
                    try:
                        loop = asyncio.get_running_loop()
                        parlay_signals = await loop.run_in_executor(
                            None, self._parlay_pricer.generate_signals, self._state
                        )
                        if parlay_signals:
                            existing_tickers = {s["ticker"] for s in result["signals"]}
                            new_parlays = [s for s in parlay_signals if s["ticker"] not in existing_tickers]
                            result["signals"].extend(new_parlays)
                            result["total_signals"] = len(result["signals"])
                            if new_parlays:
                                logger.info("Parlay pricer: +%d signals (%d total)",
                                           len(new_parlays), result["total_signals"])
                    except Exception as e:
                        logger.debug("Parlay signal generation failed: %s", e)

                # Final quality filter: remove bogus edges from ANY source (including parlays)
                result["signals"] = [
                    s for s in result["signals"]
                    if abs(s.get("edge", 0)) <= 0.30
                ]
                result["total_signals"] = len(result["signals"])

                old_signals = self._signals_holder.get()
                self._signals_holder.update(result)

                # Broadcast to WS clients
                await self._ws_manager.broadcast_signals(result)

                # Emit feed events for signal changes
                self._emit_signal_events(old_signals, result)

                self._feed.add(
                    FeedEventType.SIGNAL_CHANGE,
                    message=f"Signals refreshed ({source}): {result.get('total_signals', 0)} signals",
                )

                # ── Execution engine: evaluate entries + exits ──────────
                entries_count = 0
                exits_count = 0
                if self._execution_engine and self._position_manager:
                    try:
                        signals_list = result.get("signals", [])

                        # If QuantBrain is active, it handles entries (but we still need exits)
                        brain_active = hasattr(self, '_quant_brain') and self._quant_brain is not None
                        if brain_active:
                            try:
                                brain_decision = self._quant_brain.run_cycle()
                                entries_count = brain_decision.get("entries_executed", 0)
                                if entries_count > 0:
                                    logger.info("QuantBrain: %d entries in cycle %d",
                                                entries_count, brain_decision["cycle"])
                            except Exception as e:
                                logger.error("QuantBrain cycle failed: %s", e)
                                brain_active = False  # fallback to direct execution

                        # Evaluate exits (always handled by execution engine directly)
                        regimes = {}
                        for s in signals_list:
                            regimes[s["ticker"]] = s.get("regime", "UNKNOWN")
                        exits = self._execution_engine.evaluate_exits(signals_list, regimes)
                        exits_count = len(exits)

                        # Only do direct entries if QuantBrain is not active
                        if not brain_active:
                            entries = self._execution_engine.evaluate_entries(signals_list)
                            entries_count = len(entries)

                        heat = self._position_manager.get_portfolio_heat()
                        logger.info(
                            "Cycle: %d signals, %d entries, %d exits, heat=%.0f%%",
                            len(signals_list), entries_count, exits_count, heat * 100,
                        )

                        # Broadcast updated positions
                        await self._broadcast_positions()
                    except Exception as e:
                        logger.error("Execution engine error: %s", e)

                logger.info(
                    "Signals updated [%s]: %d signals at %s",
                    source.upper(),
                    result.get("total_signals", 0),
                    result.get("generated_at", ""),
                )

    def _run_live_ensemble_sync(self) -> dict:
        """Synchronous live ensemble (runs in executor)."""
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if project_root not in sys.path:
            sys.path.insert(0, project_root)

        from engine.live_features import compute_live_features
        from engine.live_ensemble import run_live_ensemble

        t0 = time.time()
        features = compute_live_features(
            self._state, self._orderbooks, self._feed,
        )

        if features.empty:
            logger.info("Live features empty (not enough WS history yet)")
            return {}

        result = run_live_ensemble(
            features, self._state, self._orderbooks,
            self._model_mgr, portfolio_value=10000,
        )

        elapsed = time.time() - t0
        logger.info("Live signal cycle: %.1fs total", elapsed)
        return result

    def _run_batch_ensemble_sync(self) -> dict:
        """Synchronous batch ensemble fallback (runs in executor)."""
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if project_root not in sys.path:
            sys.path.insert(0, project_root)

        from models.ensemble import run_ensemble
        return run_ensemble(portfolio_value=10000)

    def _overlay_live_prices(self, result: dict) -> None:
        """
        Patch batch ensemble signals with live prices from MarketStateStore.
        Recalculates edge = fair_value - live_price so signals aren't stale.
        REMOVES signals for markets without live prices (expired/dead).
        """
        updated = 0
        live_signals = []

        for sig in result.get("signals", []):
            ticker = sig.get("ticker", "")
            live = self._state.get_market(ticker)

            if not live or live["price"] <= 0:
                logger.debug("Removing stale signal for %s (no live price)", ticker)
                continue

            # Sanity check: reject signals with absurd edges (> 30 cents)
            # These come from the batch model computing on markets it has no training data for
            if abs(sig.get("edge", 0)) > 0.30:
                logger.debug("Removing bogus signal for %s (edge=%.2f too large)", ticker, sig["edge"])
                continue

            live_price = live["price"]
            old_price = sig["current_price"]

            if abs(live_price - old_price) > 0.001:
                sig["current_price"] = round(live_price, 4)
                sig["edge"] = round(sig["fair_value"] - live_price, 4)
                # Recalculate net_edge
                from models.risk_model import kalshi_fee_rt
                fee = kalshi_fee_rt(live_price)
                sig["net_edge"] = round(abs(sig["edge"]) - fee, 4)
                sig["fee_impact"] = round(fee, 4)
                updated += 1

            live_signals.append(sig)

        # Replace signals with only live-priced ones
        removed = len(result.get("signals", [])) - len(live_signals)
        result["signals"] = live_signals
        result["total_signals"] = len(live_signals)

        if removed > 0:
            logger.info("Removed %d stale signals (no live price), %d remain", removed, len(live_signals))
        if updated > 0:
            logger.info("Overlaid live prices on %d signals", updated)

    async def _broadcast_positions(self) -> None:
        """Broadcast position updates to WS clients."""
        if not self._position_manager:
            return
        try:
            data = {
                "open": self._position_manager.get_open_positions(),
                "summary": self._position_manager.get_summary(),
            }
            await self._ws_manager.broadcast_positions(data)
        except Exception as e:
            logger.debug("Position broadcast error: %s", e)

    def _emit_signal_events(self, old: dict, new: dict) -> None:
        """Compare old vs new signals and emit feed events for changes."""
        old_signals = {s["ticker"]: s for s in old.get("signals", [])}
        new_signals = {s["ticker"]: s for s in new.get("signals", [])}

        for ticker, sig in new_signals.items():
            old_sig = old_signals.get(ticker)
            if not old_sig:
                self._feed.add(
                    FeedEventType.SIGNAL_CHANGE,
                    ticker=ticker,
                    message=f"New signal: {sig['direction']} {ticker} (edge {sig['edge']:+.2f})",
                    data={"direction": sig["direction"], "edge": sig["edge"]},
                )
            elif old_sig["direction"] != sig["direction"]:
                self._feed.add(
                    FeedEventType.SIGNAL_CHANGE,
                    ticker=ticker,
                    message=f"Signal flip: {old_sig['direction']} -> {sig['direction']} {ticker}",
                    data={"old": old_sig["direction"], "new": sig["direction"]},
                )
