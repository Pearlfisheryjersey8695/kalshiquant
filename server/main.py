"""
FastAPI entry point — ties together the real-time engine and REST/WS API.

Run: python -m uvicorn server.main:app --host 0.0.0.0 --port 8000
"""

import asyncio
import json
import logging
import os
import sys
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

# Ensure project root is in path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from app.kalshi_client import KalshiClient

# Future real-time pipeline modules — not yet implemented.
# Wrapped in try/except so the server can start without them.
try:
    from engine.feed import FeedLog, FeedEventType
except ImportError:
    FeedLog = None
    FeedEventType = None

try:
    from engine.market_state import MarketStateStore
except ImportError:
    MarketStateStore = None

try:
    from engine.orderbook import OrderbookStore
except ImportError:
    OrderbookStore = None

try:
    from engine.ws_client import KalshiWSClient
except ImportError:
    KalshiWSClient = None

from engine.scheduler import Scheduler
from engine.position_manager import PositionManager
from engine.execution_engine import ExecutionEngine
from engine.strategies import load_strategies_from_manager
from models.risk_model import RiskModel
from server.ws_manager import WSManager
from server.risk_engine import RiskEngine
from server.strategy_manager import StrategyManager
from server.routes import create_router
from engine.quant_brain import QuantBrain
from engine.parlay_pricer import ParlayPricer

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
    """In-memory cache of the latest signals. Thread-safe via explicit lock."""

    def __init__(self):
        self._lock = threading.Lock()
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
                data = json.load(f)
            with self._lock:
                self._data = data
            logger.info("Loaded %d cached signals from %s", len(data.get("signals", [])), path)
        except FileNotFoundError:
            logger.warning("No cached signals at %s", path)
        except Exception as e:
            logger.error("Failed to load signals: %s", e)

    def get(self) -> dict:
        with self._lock:
            return self._data

    def get_by_ticker(self, ticker: str) -> dict | None:
        with self._lock:
            for s in self._data.get("signals", []):
                if s["ticker"] == ticker:
                    return s
        return None

    def update(self, data: dict) -> None:
        with self._lock:
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

# Position manager + execution engine (paper trading)
position_manager = PositionManager(bankroll=10000.0)
risk_model = RiskModel(portfolio_value=10000)
risk_engine = RiskEngine(bankroll=10000.0)
strategy_manager = StrategyManager()

from server.alert_engine import AlertEngine
alert_engine = AlertEngine()
execution_engine = ExecutionEngine(
    position_manager=position_manager,
    risk_model=risk_model,
    feed=feed,
    state=state,
    orderbooks=orderbooks,
    kalshi_client=rest_client,
)

# QuantBrain autonomous trading agent
quant_brain = QuantBrain(
    execution_engine=execution_engine,
    position_manager=position_manager,
    risk_model=risk_model,
    state=state,
    orderbooks=orderbooks,
    signals_holder=signals_holder,
    feed=feed,
)

# Scheduler
scheduler = Scheduler(
    state, orderbooks, feed, ws_manager, signals_holder,
    execution_engine=execution_engine,
    position_manager=position_manager,
    risk_engine=risk_engine,
    alert_engine=alert_engine,
)
scheduler._kalshi_client = rest_client
scheduler._quant_brain = quant_brain

# Parlay decomposition engine
parlay_pricer = ParlayPricer(rest_client)
scheduler._parlay_pricer = parlay_pricer

# Store background task references so exceptions aren't silently lost
_background_tasks: list[asyncio.Task] = []


# ── Lifespan ──────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("=" * 60)
    logger.info("  KalshiQuant Server v0.3.0 starting...")
    logger.info("=" * 60)

    # 1. Load market state from scored data
    tickers = state.init_from_scored_markets(DATA_DIR)
    logger.info("Tracking %d markets", len(tickers))

    # 2. Load cached signals
    signals_holder.load_from_file()

    # 2b. Sync bankroll with Kalshi API
    try:
        real_balance = position_manager.sync_bankroll(rest_client)
        logger.info("Bankroll synced with Kalshi: $%.2f", real_balance)
    except Exception as e:
        logger.warning("Bankroll sync failed, using default: %s", e)

    # 3. Start Kalshi WebSocket client — store task reference
    feed.add(FeedEventType.CONNECTION, message="Server starting up")
    ws_task = asyncio.create_task(kalshi_ws.connect_and_run())
    _background_tasks.append(ws_task)

    # 4. Start scheduler (30s/5min/1h loops)
    scheduler.start()

    # 5. Sync strategy configs from SQLite manager to execution engine
    synced = load_strategies_from_manager(strategy_manager)
    logger.info("Loaded %d strategy configs from manager", synced)

    logger.info("Server ready — REST at /api/*, WS at /ws/*")

    yield

    # Shutdown
    logger.info("Shutting down...")
    kalshi_ws.stop()
    await scheduler.stop()
    # Cancel background tasks
    for task in _background_tasks:
        task.cancel()
    await asyncio.gather(*_background_tasks, return_exceptions=True)
    _background_tasks.clear()
    logger.info("Shutdown complete")


# ── FastAPI App ────────────────────────────────────────────────────────────

app = FastAPI(title="KalshiQuant", version="0.3.0", lifespan=lifespan)

# CORS — restrict to known Next.js dev server origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:3001"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# REST routes
router = create_router(
    state, orderbooks, feed, signals_holder, rest_client,
    execution_engine=execution_engine,
    position_manager=position_manager,
    risk_engine=risk_engine,
    strategy_manager=strategy_manager,
    alert_engine=alert_engine,
    quant_brain=quant_brain,
    parlay_pricer=parlay_pricer,
)
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


@app.websocket("/ws/positions")
async def ws_positions(ws: WebSocket):
    await ws_manager.connect_positions(ws)
    try:
        # Send current positions immediately
        data = {
            "open": position_manager.get_open_positions(),
            "summary": position_manager.get_summary(),
        }
        await ws.send_json({"type": "positions", "data": data})
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        ws_manager.disconnect_positions(ws)
