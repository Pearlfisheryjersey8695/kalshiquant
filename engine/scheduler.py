"""
Periodic task scheduler for the real-time engine.
Three loops:
  - 30s:  Broadcast price snapshots to frontend WS clients
  - 5min: Re-run ensemble signals (CPU-bound, in executor)
  - 1h:   Full model refit + signal refresh
"""

import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timezone

from engine.feed import FeedLog, FeedEventType
from engine.market_state import MarketStateStore

logger = logging.getLogger("kalshi.scheduler")

# Intervals in seconds
PRICE_INTERVAL = 30
SIGNAL_INTERVAL = 300   # 5 minutes
REFIT_INTERVAL = 3600   # 1 hour


class Scheduler:
    def __init__(
        self,
        state: MarketStateStore,
        feed: FeedLog,
        ws_manager,
        signals_holder,
    ):
        self._state = state
        self._feed = feed
        self._ws_manager = ws_manager
        self._signals_holder = signals_holder
        self._tasks: list[asyncio.Task] = []
        self._signal_lock = asyncio.Lock()  # prevent concurrent run_ensemble

    def start(self) -> None:
        loop = asyncio.get_event_loop()
        self._tasks = [
            loop.create_task(self._price_loop()),
            loop.create_task(self._signal_loop()),
            loop.create_task(self._refit_loop()),
        ]
        logger.info("Scheduler started (30s/5min/1h loops)")

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks = []

    async def _price_loop(self) -> None:
        """Every 30s: broadcast current prices to frontend WS clients."""
        while True:
            try:
                await asyncio.sleep(PRICE_INTERVAL)
                snapshot = self._state.snapshot_all()
                await self._ws_manager.broadcast_prices(snapshot)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Price broadcast error: %s", e)

    async def _signal_loop(self) -> None:
        """Every 5min: re-run ensemble in executor, update signals, broadcast."""
        # Wait for initial data to settle
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
        """Every 1h: full model refit + signal refresh."""
        await asyncio.sleep(REFIT_INTERVAL)

        while True:
            try:
                logger.info("Running full model refit...")
                await self._run_signals()
                await asyncio.sleep(REFIT_INTERVAL)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Model refit error: %s", e)
                await asyncio.sleep(REFIT_INTERVAL)

    async def _run_signals(self) -> None:
        """
        Run the ensemble pipeline in a thread executor (CPU-bound).
        Uses asyncio.Lock to prevent concurrent runs (refit + signal loops).
        After ensemble returns, overlays live prices from MarketStateStore
        so signals reflect real-time data instead of stale batch prices.
        """
        if self._signal_lock.locked():
            logger.info("Signal run skipped — previous run still in progress")
            return

        async with self._signal_lock:
            loop = asyncio.get_event_loop()

            try:
                result = await loop.run_in_executor(None, self._run_ensemble_sync)
            except Exception as e:
                logger.error("Ensemble execution failed: %s", e)
                self._feed.add(
                    FeedEventType.ERROR,
                    message=f"Signal pipeline failed: {e}",
                )
                return

            if result and result.get("signals"):
                # Overlay live prices from MarketStateStore onto ensemble signals.
                # run_ensemble() reads stale clean_features.parquet — the live WS
                # prices never reach the models. This patch updates current_price
                # and recalculates edge so signals reflect real-time market state.
                self._overlay_live_prices(result)

                old_signals = self._signals_holder.get()
                self._signals_holder.update(result)

                # Broadcast to WS clients
                await self._ws_manager.broadcast_signals(result)

                # Emit feed events for signal changes
                self._emit_signal_events(old_signals, result)

                logger.info(
                    "Signals updated: %d signals at %s",
                    result.get("total_signals", 0),
                    result.get("generated_at", ""),
                )

    def _run_ensemble_sync(self) -> dict:
        """Synchronous wrapper for run_ensemble (runs in thread executor)."""
        # Ensure project root is in path for model imports
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if project_root not in sys.path:
            sys.path.insert(0, project_root)

        from models.ensemble import run_ensemble
        return run_ensemble(portfolio_value=10000)

    def _overlay_live_prices(self, result: dict) -> None:
        """
        Patch ensemble signals with live prices from MarketStateStore.
        Recalculates edge = fair_value - live_price so signals aren't stale.
        """
        updated = 0
        for sig in result.get("signals", []):
            ticker = sig.get("ticker", "")
            live = self._state.get_market(ticker)
            if not live or live["price"] <= 0:
                continue

            live_price = live["price"]
            old_price = sig["current_price"]

            if abs(live_price - old_price) > 0.001:  # meaningful difference
                sig["current_price"] = round(live_price, 4)
                sig["edge"] = round(sig["fair_value"] - live_price, 4)
                updated += 1

        if updated > 0:
            logger.info("Overlaid live prices on %d/%d signals", updated, len(result.get("signals", [])))

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
