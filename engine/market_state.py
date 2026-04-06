"""
In-memory market state store.
Initialized from scored_markets.csv, updated by Kalshi WebSocket ticker messages.
All prices stored as probability [0,1] — cents divided by 100 ONCE here.
"""

import logging
import os
from collections import deque
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone

import pandas as pd

from engine.feed import FeedLog, FeedEventType

logger = logging.getLogger("kalshi.state")

HISTORY_MAXLEN = 2000


@dataclass
class TickerState:
    ticker: str
    price: float = 0.0           # [0,1] probability
    yes_bid: float = 0.0         # [0,1]
    yes_ask: float = 0.0         # [0,1]
    volume: int = 0
    open_interest: int = 0
    last_update_ts: str = ""
    title: str = ""
    category: str = ""
    tradability_score: float = 0.0
    expiration_time: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class MarketSnapshot:
    ts: str
    price: float
    yes_bid: float
    yes_ask: float
    volume: int

    def to_dict(self) -> dict:
        return asdict(self)


class MarketStateStore:
    """Central in-memory store for all tracked markets."""

    RECENT_TRADES_MAXLEN = 200

    def __init__(self, feed: FeedLog):
        self._markets: dict[str, TickerState] = {}
        self._history: dict[str, deque] = {}
        self._recent_trades: dict[str, deque] = {}
        self._feed = feed

    def init_from_scored_markets(self, data_dir: str) -> list[str]:
        """
        Load scored_markets.csv + tradeable_markets.csv, create TickerState
        per market with metadata. Returns list of tickers to subscribe to.
        """
        scored_path = os.path.join(data_dir, "scored_markets.csv")
        tradeable_path = os.path.join(data_dir, "tradeable_markets.csv")

        tickers = []

        # Load tradeable_markets for full metadata
        try:
            tradeable = pd.read_csv(tradeable_path)
            tradeable_map = tradeable.set_index("ticker").to_dict("index")
        except (FileNotFoundError, Exception) as e:
            logger.warning("Could not load tradeable_markets.csv: %s", e)
            tradeable_map = {}

        # Load scored_markets for tradability scores
        try:
            scored = pd.read_csv(scored_path)
        except FileNotFoundError:
            logger.warning("scored_markets.csv not found, no markets loaded")
            return []

        for _, row in scored.iterrows():
            ticker = row["ticker"]
            tradeable_info = tradeable_map.get(ticker, {})

            # Initial price from snapshot data
            # Handle both cents (>1) and probability (0-1) formats
            raw_bid = tradeable_info.get("yes_bid", 0)
            raw_ask = tradeable_info.get("yes_ask", 0)
            yes_bid = raw_bid / 100.0 if raw_bid > 1 else float(raw_bid)
            yes_ask = raw_ask / 100.0 if raw_ask > 1 else float(raw_ask)
            # Also try last_price if no bid/ask
            raw_last = tradeable_info.get("last_price", 0)
            last_price = raw_last / 100.0 if raw_last > 1 else float(raw_last)
            price = (yes_bid + yes_ask) / 2.0 if (yes_bid > 0 and yes_ask > 0) else last_price

            state = TickerState(
                ticker=ticker,
                price=price,
                yes_bid=yes_bid,
                yes_ask=yes_ask,
                volume=int(tradeable_info.get("volume", row.get("volume", 0))),
                open_interest=int(tradeable_info.get("open_interest", row.get("open_interest", 0))),
                title=str(tradeable_info.get("title", row.get("title", ""))),
                category=str(tradeable_info.get("category", row.get("category", ""))),
                tradability_score=float(row.get("tradability_score", 0)),
                expiration_time=str(tradeable_info.get("expiration_time", "")),
            )
            self._markets[ticker] = state
            self._history[ticker] = deque(maxlen=HISTORY_MAXLEN)
            tickers.append(ticker)

        logger.info("Initialized %d markets from scored data", len(tickers))
        return tickers

    def add_market(self, data: dict) -> None:
        """Add a market dynamically (e.g., from WS messages for untracked tickers)."""
        ticker = data.get("ticker", "")
        if not ticker or ticker in self._markets:
            return
        state = TickerState(
            ticker=ticker,
            title=str(data.get("title", "")),
            category=str(data.get("category", "")),
            price=float(data.get("price", 0)),
            yes_bid=float(data.get("yes_bid", 0)),
            yes_ask=float(data.get("yes_ask", 0)),
            volume=int(data.get("volume", 0)),
            open_interest=int(data.get("open_interest", 0)),
            expiration_time=str(data.get("expiration_time", "")),
            tradability_score=float(data.get("tradability_score", 0)),
        )
        self._markets[ticker] = state
        self._history[ticker] = deque(maxlen=HISTORY_MAXLEN)

    def update_from_ticker_msg(self, msg: dict) -> None:
        """
        Process a Kalshi WS ticker message.
        Supports both API v1 (cents) and v2 (dollars) formats.
        Auto-adds markets not yet in the store.
        """
        ticker = msg.get("market_ticker", "")
        if not ticker:
            return

        # Auto-add if not tracked (enables discovery of new markets)
        if ticker not in self._markets:
            self.add_market({"ticker": ticker})

        state = self._markets[ticker]
        old_price = state.price

        # Parse price — support both cents (int) and dollars (string/float)
        def _parse_price(val, fallback=0.0):
            if val is None:
                return fallback
            if isinstance(val, str):
                try:
                    v = float(val)
                    return v  # dollars format (0.0-1.0)
                except (ValueError, TypeError):
                    return fallback
            if isinstance(val, (int, float)):
                return val / 100.0 if val > 1 else float(val)
            return fallback

        # Try v2 fields first (_dollars suffix), fall back to v1 (cents)
        new_price = _parse_price(msg.get("yes_price_dollars", msg.get("price")))
        yes_bid = _parse_price(msg.get("yes_bid_dollars", msg.get("yes_bid")))
        yes_ask = _parse_price(msg.get("yes_ask_dollars", msg.get("yes_ask")))

        # Volume — support both int and _fp string
        volume = msg.get("volume", msg.get("volume_fp", state.volume))
        if isinstance(volume, str):
            try:
                volume = int(float(volume))
            except (ValueError, TypeError):
                volume = state.volume

        oi = msg.get("open_interest", msg.get("open_interest_fp", state.open_interest))
        if isinstance(oi, str):
            try:
                oi = int(float(oi))
            except (ValueError, TypeError):
                oi = state.open_interest

        state.price = new_price if new_price > 0 else state.price
        state.yes_bid = yes_bid if yes_bid > 0 else state.yes_bid
        state.yes_ask = yes_ask if yes_ask > 0 else state.yes_ask
        state.volume = volume
        state.open_interest = oi
        state.last_update_ts = datetime.now(timezone.utc).isoformat()

        # Append to history
        snap = MarketSnapshot(
            ts=state.last_update_ts,
            price=state.price,
            yes_bid=state.yes_bid,
            yes_ask=state.yes_ask,
            volume=state.volume,
        )
        self._history[ticker].append(snap)

        # Emit feed event if price moved > 2 cents
        if old_price > 0 and abs(state.price - old_price) > 0.02:
            direction = "+" if state.price > old_price else ""
            move = state.price - old_price
            self._feed.add(
                FeedEventType.PRICE_MOVE,
                ticker=ticker,
                message=f"{ticker} {direction}{move:.2f} -> {state.price:.2f}",
                data={"old_price": old_price, "new_price": state.price},
            )

    def get_market(self, ticker: str) -> dict | None:
        state = self._markets.get(ticker)
        return state.to_dict() if state else None

    def get_all_markets(self) -> list[dict]:
        return [s.to_dict() for s in self._markets.values()]

    def get_history(self, ticker: str, limit: int = 200) -> list[dict]:
        hist = self._history.get(ticker)
        if not hist:
            return []
        items = list(hist)[-limit:]
        return [s.to_dict() for s in items]

    def snapshot_all(self) -> list[dict]:
        """Return current state of all markets for broadcast."""
        return self.get_all_markets()

    def record_trade(self, msg: dict) -> None:
        """Record a trade for VPIN calculation."""
        ticker = msg.get("market_ticker", "")
        if not ticker:
            return
        if ticker not in self._recent_trades:
            self._recent_trades[ticker] = deque(maxlen=self.RECENT_TRADES_MAXLEN)
        self._recent_trades[ticker].append({
            "yes_price": msg.get("yes_price", msg.get("price", 50)),
            "count": msg.get("count", 1),
            "ts": datetime.now(timezone.utc).isoformat(),
        })

    def get_recent_trades(self, ticker: str) -> list[dict]:
        """Get recent trades for a ticker (for VPIN calculation)."""
        trades = self._recent_trades.get(ticker)
        return list(trades) if trades else []

    def tracked_tickers(self) -> list[str]:
        return list(self._markets.keys())
