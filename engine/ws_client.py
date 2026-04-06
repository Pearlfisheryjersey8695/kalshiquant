"""
Kalshi WebSocket client with auto-reconnect.
Reuses KalshiClient._sign() for RSA-PSS auth — no crypto duplication.
Streams ticker + orderbook channels to MarketStateStore and OrderbookStore.
"""

import asyncio
import json
import logging

import websockets

from engine.feed import FeedLog, FeedEventType
from engine.market_state import MarketStateStore
from engine.orderbook import OrderbookStore

logger = logging.getLogger("kalshi.ws")

WS_URL = "wss://api.elections.kalshi.com/trade-api/ws/v2"
WS_PATH = "/trade-api/ws/v2"

# Reconnect backoff
INITIAL_BACKOFF = 1
MAX_BACKOFF = 60

# Max tickers per subscribe command (Kalshi limit)
SUBSCRIBE_BATCH_SIZE = 50


class KalshiWSClient:
    """
    Async WebSocket client for Kalshi real-time data.

    Uses the REST client's _sign() method for authentication headers
    during the WebSocket handshake.
    """

    def __init__(
        self,
        rest_client,
        state: MarketStateStore,
        orderbooks: OrderbookStore,
        feed: FeedLog,
    ):
        self._rest = rest_client
        self._state = state
        self._orderbooks = orderbooks
        self._feed = feed
        self._ws = None
        self._running = False
        self._cmd_id = 0
        self._backoff = INITIAL_BACKOFF

    def _auth_headers(self) -> dict:
        """Build auth headers using the REST client's signing method."""
        ts, sig = self._rest._sign("GET", WS_PATH)
        return {
            "KALSHI-ACCESS-KEY": self._rest.api_key,
            "KALSHI-ACCESS-TIMESTAMP": ts,
            "KALSHI-ACCESS-SIGNATURE": sig,
        }

    def _next_cmd_id(self) -> int:
        self._cmd_id += 1
        return self._cmd_id

    async def _subscribe(self, tickers: list[str], channels: list[str] | None = None) -> None:
        """Send subscribe commands for given tickers in batches."""
        if not self._ws or not tickers:
            return

        if channels is None:
            channels = ["ticker", "orderbook_delta"]

        for i in range(0, len(tickers), SUBSCRIBE_BATCH_SIZE):
            batch = tickers[i:i + SUBSCRIBE_BATCH_SIZE]
            cmd = {
                "id": self._next_cmd_id(),
                "cmd": "subscribe",
                "params": {
                    "channels": channels,
                    "market_tickers": batch,
                },
            }
            await self._ws.send(json.dumps(cmd))
            logger.info("Subscribed to %d tickers (%s...)", len(batch), batch[0])

    async def _handle_message(self, raw: str) -> None:
        """Dispatch incoming WS message by type."""
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("Invalid JSON from WS: %s", raw[:200])
            return

        msg_type = data.get("type", "")
        msg = data.get("msg", {})
        sid = data.get("sid", 0)
        seq = data.get("seq", 0)

        if msg_type == "ticker":
            self._state.update_from_ticker_msg(msg)

        elif msg_type == "orderbook_snapshot":
            ticker = msg.get("market_ticker", "")
            if ticker:
                ob = self._orderbooks.get_or_create(ticker)
                ob.apply_snapshot(msg, seq)
                # Also derive price from orderbook snapshot (ticker msgs may not arrive)
                self._update_price_from_orderbook(ticker, ob)

        elif msg_type == "orderbook_delta":
            ticker = msg.get("market_ticker", "")
            if ticker:
                ob = self._orderbooks.get_or_create(ticker)
                ok = ob.apply_delta(msg, seq)
                if not ok:
                    logger.warning("Orderbook seq gap for %s, resubscribing", ticker)
                    await self._subscribe([ticker], ["orderbook_delta"])
                else:
                    # Update price from orderbook after each delta
                    self._update_price_from_orderbook(ticker, ob)

        elif msg_type == "trade":
            # Kalshi v2 may send trade messages — extract price
            ticker = msg.get("market_ticker", "")
            if ticker:
                self._state.update_from_ticker_msg(msg)

        elif msg_type == "error":
            logger.error("WS error: %s", msg)
            self._feed.add(
                FeedEventType.ERROR,
                message=f"WS error: {msg}",
            )

        # Log unknown types for debugging (first 5 only)
        elif msg_type and msg_type not in ("subscribed", ""):
            if not hasattr(self, '_logged_types'):
                self._logged_types = set()
            if msg_type not in self._logged_types and len(self._logged_types) < 5:
                self._logged_types.add(msg_type)
                logger.info("Unknown WS msg type '%s': %s", msg_type, str(msg)[:200])

    def _update_price_from_orderbook(self, ticker: str, ob) -> None:
        """Derive market price from orderbook bid/ask when ticker msgs don't arrive."""
        if not ob._has_snapshot:
            return
        try:
            mid_cents = ob.get_mid_price_cents()
            if mid_cents > 0:
                price = mid_cents / 100.0
                self._state.update_from_ticker_msg({
                    "market_ticker": ticker,
                    "price": mid_cents,
                    "yes_bid": mid_cents - 1 if mid_cents > 1 else 0,
                    "yes_ask": mid_cents + 1 if mid_cents < 100 else 100,
                })
        except Exception:
            pass

    async def connect_and_run(self) -> None:
        """Main async loop: connect, subscribe, receive messages. Auto-reconnects."""
        self._running = True
        tickers = self._state.tracked_tickers()

        if not tickers:
            logger.warning("No tickers to track, WS client idle")
            return

        while self._running:
            try:
                headers = self._auth_headers()
                logger.info("Connecting to Kalshi WebSocket...")

                async with websockets.connect(
                    WS_URL,
                    additional_headers=headers,
                    ping_interval=30,
                    ping_timeout=10,
                    close_timeout=5,
                ) as ws:
                    self._ws = ws
                    self._backoff = INITIAL_BACKOFF  # reset on success

                    logger.info("Connected to Kalshi WebSocket")
                    self._feed.add(
                        FeedEventType.CONNECTION,
                        message=f"Connected, subscribing to {len(tickers)} markets",
                    )

                    await self._subscribe(tickers)

                    async for raw in ws:
                        if not self._running:
                            break
                        await self._handle_message(raw)

            except websockets.ConnectionClosedError as e:
                logger.warning("WS connection closed: %s", e)
            except websockets.InvalidStatusCode as e:
                logger.error("WS auth failed (HTTP %s)", e.status_code)
            except Exception as e:
                logger.error("WS error: %s", e)

            self._ws = None

            # Invalidate all orderbooks — stale data after disconnect is
            # worse than no data. Fresh snapshots arrive on resubscribe.
            self._orderbooks.invalidate_all()

            if not self._running:
                break

            # Reconnect with exponential backoff
            self._feed.add(
                FeedEventType.CONNECTION,
                message=f"Disconnected, reconnecting in {self._backoff}s",
            )
            logger.info("Reconnecting in %ds...", self._backoff)
            await asyncio.sleep(self._backoff)
            self._backoff = min(self._backoff * 2, MAX_BACKOFF)

    def stop(self) -> None:
        """Signal the client to stop and disconnect."""
        self._running = False
        if self._ws:
            asyncio.ensure_future(self._ws.close())
