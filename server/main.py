"""
FastAPI entry point — ties together the real-time engine and REST/WS API.

Run: python -m uvicorn server.main:app --host 0.0.0.0 --port 8000
"""

import asyncio
import json
import logging
import os
import sys

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

# Ensure project root is in path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from app.kalshi_client import KalshiClient
from engine.feed import FeedLog, FeedEventType
from engine.market_state import MarketStateStore
from engine.orderbook import OrderbookStore
from engine.ws_client import KalshiWSClient
from engine.scheduler import Scheduler
from server.ws_manager import WSManager
from server.routes import create_router

# ── Logging ────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("kalshi.server")

# ── Data directory ─────────────────────────────────────────────────────────

DATA_DIR = os.path.join(PROJECT_ROOT, "data")
SIGNALS_DIR = os.path.join(PROJECT_ROOT, "signals")


# ── Signals Holder ─────────────────────────────────────────────────────────

class SignalsHolder:
    """In-memory cache of the latest signals. Thread-safe via GIL."""

    def __init__(self):
        self._data: dict = {
            "generated_at": "",
            "portfolio_value": 10000,
            "total_signals": 0,
            "signals": [],
        }

    def load_from_file(self, path: str | None = None) -> None:
        path = path or os.path.join(SIGNALS_DIR, "latest_signals.json")
        try:
            with open(path) as f:
                self._data = json.load(f)
            logger.info("Loaded %d cached signals from %s", len(self._data.get("signals", [])), path)
        except FileNotFoundError:
            logger.warning("No cached signals at %s", path)
        except Exception as e:
            logger.error("Failed to load signals: %s", e)

    def get(self) -> dict:
        return self._data

    def get_by_ticker(self, ticker: str) -> dict | None:
        for s in self._data.get("signals", []):
            if s["ticker"] == ticker:
                return s
        return None

    def update(self, data: dict) -> None:
        self._data = data


# ── Singletons ─────────────────────────────────────────────────────────────

feed = FeedLog(maxlen=500)
state = MarketStateStore(feed)
orderbooks = OrderbookStore()
ws_manager = WSManager()
signals_holder = SignalsHolder()

# REST client (for portfolio queries + WS auth)
rest_client = KalshiClient()

# Kalshi WS client
kalshi_ws = KalshiWSClient(rest_client, state, orderbooks, feed)

# Scheduler
scheduler = Scheduler(state, feed, ws_manager, signals_holder)

# ── FastAPI App ────────────────────────────────────────────────────────────

app = FastAPI(title="KalshiQuant", version="0.3.0")

# CORS (allow all for hackathon)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# REST routes
router = create_router(state, orderbooks, feed, signals_holder, rest_client)
app.include_router(router)


# ── WebSocket Endpoints ───────────────────────────────────────────────────

@app.websocket("/ws/prices")
async def ws_prices(ws: WebSocket):
    await ws_manager.connect_prices(ws)
    try:
        # Send initial snapshot immediately
        snapshot = state.snapshot_all()
        await ws.send_json({"type": "prices", "data": snapshot})
        # Keep connection alive, listen for client messages
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        ws_manager.disconnect_prices(ws)


@app.websocket("/ws/signals")
async def ws_signals(ws: WebSocket):
    await ws_manager.connect_signals(ws)
    try:
        # Send current signals immediately
        await ws.send_json({"type": "signals", "data": signals_holder.get()})
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        ws_manager.disconnect_signals(ws)


@app.websocket("/ws/feed")
async def ws_feed(ws: WebSocket):
    await ws_manager.connect_feed(ws)
    try:
        # Send recent feed events immediately
        recent = feed.get_recent(50)
        await ws.send_json({"type": "feed", "data": recent})
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        ws_manager.disconnect_feed(ws)


# ── Lifecycle ──────────────────────────────────────────────────────────────

@app.on_event("startup")
async def startup():
    logger.info("=" * 60)
    logger.info("  KalshiQuant Server v0.3.0 starting...")
    logger.info("=" * 60)

    # 1. Load market state from scored data
    tickers = state.init_from_scored_markets(DATA_DIR)
    logger.info("Tracking %d markets", len(tickers))

    # 2. Load cached signals
    signals_holder.load_from_file()

    # 3. Start Kalshi WebSocket client
    feed.add(FeedEventType.CONNECTION, message="Server starting up")
    asyncio.create_task(kalshi_ws.connect_and_run())

    # 4. Start scheduler (30s/5min/1h loops)
    scheduler.start()

    logger.info("Server ready — REST at /api/*, WS at /ws/*")


@app.on_event("shutdown")
async def shutdown():
    logger.info("Shutting down...")
    kalshi_ws.stop()
    await scheduler.stop()
    logger.info("Shutdown complete")
