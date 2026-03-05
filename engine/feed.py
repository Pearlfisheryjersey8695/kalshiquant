"""
Feed event ring buffer for the LiveFeed panel.
Stores recent events (price moves, signal changes, connections, errors)
in a bounded deque for real-time display.
"""

from collections import deque
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class FeedEventType(str, Enum):
    PRICE_MOVE = "PRICE_MOVE"
    SIGNAL_CHANGE = "SIGNAL_CHANGE"
    REGIME_CHANGE = "REGIME_CHANGE"
    TRADE = "TRADE"
    CONNECTION = "CONNECTION"
    ERROR = "ERROR"


@dataclass
class FeedEvent:
    seq: int
    ts: str
    event_type: FeedEventType
    ticker: str
    message: str
    data: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["event_type"] = self.event_type.value
        return d


class FeedLog:
    """Thread-safe ring buffer of feed events."""

    def __init__(self, maxlen: int = 500):
        self._buffer: deque[FeedEvent] = deque(maxlen=maxlen)
        self._seq = 0

    def add(
        self,
        event_type: FeedEventType,
        ticker: str = "",
        message: str = "",
        data: dict | None = None,
    ) -> FeedEvent:
        self._seq += 1
        event = FeedEvent(
            seq=self._seq,
            ts=datetime.now(timezone.utc).isoformat(),
            event_type=event_type,
            ticker=ticker,
            message=message,
            data=data or {},
        )
        self._buffer.append(event)
        return event

    def get_recent(self, limit: int = 50) -> list[dict]:
        items = list(self._buffer)[-limit:]
        return [e.to_dict() for e in items]

    def get_since(self, after_seq: int) -> list[dict]:
        return [e.to_dict() for e in self._buffer if e.seq > after_seq]

    def __len__(self) -> int:
        return len(self._buffer)
