"""
Frontend WebSocket broadcast manager.
Three channels: prices, signals, feed events.
Dead connections are silently removed during broadcast.
"""

import json
import logging

from fastapi import WebSocket

logger = logging.getLogger("kalshi.ws_manager")


class WSManager:
    """Manages frontend WebSocket connections and broadcasts."""

    def __init__(self):
        self._price_clients: set[WebSocket] = set()
        self._signal_clients: set[WebSocket] = set()
        self._feed_clients: set[WebSocket] = set()

    # ── Connection management ──────────────────────────────────────────────

    async def connect_prices(self, ws: WebSocket) -> None:
        await ws.accept()
        self._price_clients.add(ws)
        logger.info("Price client connected (%d total)", len(self._price_clients))

    def disconnect_prices(self, ws: WebSocket) -> None:
        self._price_clients.discard(ws)

    async def connect_signals(self, ws: WebSocket) -> None:
        await ws.accept()
        self._signal_clients.add(ws)
        logger.info("Signal client connected (%d total)", len(self._signal_clients))

    def disconnect_signals(self, ws: WebSocket) -> None:
        self._signal_clients.discard(ws)

    async def connect_feed(self, ws: WebSocket) -> None:
        await ws.accept()
        self._feed_clients.add(ws)
        logger.info("Feed client connected (%d total)", len(self._feed_clients))

    def disconnect_feed(self, ws: WebSocket) -> None:
        self._feed_clients.discard(ws)

    # ── Broadcasting ───────────────────────────────────────────────────────

    async def broadcast_prices(self, data: list[dict]) -> None:
        payload = json.dumps({"type": "prices", "data": data})
        await self._broadcast(self._price_clients, payload)

    async def broadcast_signals(self, data: dict) -> None:
        payload = json.dumps({"type": "signals", "data": data}, default=str)
        await self._broadcast(self._signal_clients, payload)

    async def broadcast_feed_event(self, event: dict) -> None:
        payload = json.dumps({"type": "feed", "data": event})
        await self._broadcast(self._feed_clients, payload)

    async def _broadcast(self, clients: set[WebSocket], payload: str) -> None:
        if not clients:
            return
        # Iterate over a snapshot — awaiting send_text() yields control to the
        # event loop, and a connect/disconnect could mutate the set mid-iteration
        # causing RuntimeError: Set changed size during iteration.
        dead = []
        for ws in list(clients):
            try:
                await ws.send_text(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            clients.discard(ws)

    @property
    def client_count(self) -> dict:
        return {
            "prices": len(self._price_clients),
            "signals": len(self._signal_clients),
            "feed": len(self._feed_clients),
        }
