"""
Pydantic response models for the FastAPI endpoints.
"""

from pydantic import BaseModel
from typing import Any


class MarketResponse(BaseModel):
    ticker: str
    price: float
    yes_bid: float
    yes_ask: float
    volume: int
    open_interest: int
    last_update_ts: str
    title: str
    category: str
    tradability_score: float
    expiration_time: str


class HistoryPoint(BaseModel):
    ts: str
    price: float
    yes_bid: float
    yes_ask: float
    volume: int


class SignalResponse(BaseModel):
    ticker: str
    title: str = ""
    category: str = ""
    current_price: float
    fair_value: float
    edge: float
    direction: str
    confidence: float
    regime: str
    strategy: str
    price_prediction_1h: int
    prediction_confidence: float
    recommended_contracts: int
    risk: dict = {}
    hedge: dict | None = None
    reasons: list[str] = []
    volume: int = 0
    open_interest: int = 0
    tradability_score: float = 0
    expiration_time: str | None = None


class SignalsEnvelope(BaseModel):
    generated_at: str
    portfolio_value: float
    total_signals: int
    signals: list[SignalResponse]


class BalanceResponse(BaseModel):
    balance: float


class PositionItem(BaseModel):
    ticker: str
    count: int
    side: str
    avg_price: float = 0


class PortfolioResponse(BaseModel):
    balance: float
    positions: list[dict] = []


class RiskResponse(BaseModel):
    var_95: float
    positions: list[dict] = []


class FeedEventResponse(BaseModel):
    seq: int
    ts: str
    event_type: str
    ticker: str
    message: str
    data: dict = {}


class ArbitrageOpportunity(BaseModel):
    type: str
    prefix: str
    buy_ticker: str
    sell_ticker: str
    buy_price: float
    sell_price: float
    edge: float
    description: str


class CorrelationEntry(BaseModel):
    t1: str
    t2: str
    corr: float


class DivergenceAlert(BaseModel):
    t1: str
    t2: str
    correlation: float
    spread: float
    signal: str


class CorrelationResponse(BaseModel):
    tickers: list[str]
    matrix: list[CorrelationEntry]
    divergences: list[DivergenceAlert]


class SentimentResponse(BaseModel):
    consensus_prob: float = 0.0
    consensus_edge: float = 0.0
    source: str = ""
    details: str = ""
    ai_prob: float = 0.0
    ai_edge: float = 0.0
    reasoning: str = ""
    sentiment_edge: float = 0.0
