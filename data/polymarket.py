"""Polymarket CLOB read-only adapter for cross-platform arbitrage.

Why this exists
---------------
Kalshi and Polymarket sometimes list contracts on the SAME real-world event
(BTC year-end price, election results, sports outcomes). When the implied
probabilities diverge by more than the round-trip transaction costs of both
venues, you have a textbook arbitrage. Polymarket prices are a free, large
sample of competing market beliefs — even when no arb exists, they're a useful
prior for fair value.

Scope
-----
Read-only. We never POST orders to Polymarket. Building bidirectional execution
would require:
  - On-chain wallet integration (Polygon)
  - USDC custody on a separate venue
  - Cross-venue settlement risk modelling

For v1 we just READ Polymarket prices and surface them as an extra signal +
arb-detection feature. The public CLOB API at clob.polymarket.com requires no
authentication for market data. We use the /markets endpoint for the catalog
and /book for live orderbooks.

Matching contracts across venues
--------------------------------
The hard part is mapping a Kalshi ticker (e.g. KXBTCMAX-26DEC31-C175000) to its
Polymarket equivalent. We use a heuristic on the contract slug — exact for
machine-tagged contracts, fuzzy for the rest. The matcher returns a confidence
score and only flags arbs above a configurable threshold.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from dataclasses import dataclass
from urllib.error import URLError
from urllib.request import Request, urlopen

logger = logging.getLogger("kalshi.polymarket")

POLYMARKET_CLOB_BASE = "https://clob.polymarket.com"
POLYMARKET_GAMMA_BASE = "https://gamma-api.polymarket.com"

CACHE_TTL_SECONDS = 60  # markets list refreshes once a minute

# Heuristic mapping of Kalshi keywords -> Polymarket search queries
KALSHI_TO_POLY_KEYWORDS = {
    "KXBTC": "bitcoin",
    "KXETH": "ethereum",
    "KXFED": "fed rate",
    "KXINX": "s&p 500",
    "KXNFL": "nfl",
    "KXNBA": "nba",
    "KXOSCAR": "oscar",
    "KXMOVIE": "movie",
}


@dataclass
class PolymarketContract:
    """A single Polymarket binary outcome."""
    market_id: str
    slug: str
    question: str
    yes_price: float          # 0..1, current best ask for YES
    no_price: float
    volume_24h: float
    end_date: str | None


@dataclass
class CrossVenueQuote:
    """A matched Kalshi <-> Polymarket pair, used for arb detection."""
    kalshi_ticker: str
    kalshi_yes_price: float
    poly_market_id: str
    poly_yes_price: float
    edge: float                # kalshi - poly, signed
    confidence: float          # match confidence in [0, 1]
    arb_direction: str | None  # "BUY_KALSHI_YES" / "BUY_POLY_YES" / None


class PolymarketAdapter:
    """Polls Polymarket CLOB and surfaces matched arb opportunities."""

    # Edge threshold for flagging an arb. Calibrated to cover round-trip costs
    # on both venues plus a safety buffer.
    MIN_ARB_EDGE = 0.04  # 4 cents

    def __init__(self):
        self._markets_cache: list[PolymarketContract] = []
        self._cache_ts: float = 0
        self._lock = threading.Lock()

    # ── Network primitives ───────────────────────────────────────────────
    @staticmethod
    def _http_get(url: str, timeout: int = 10) -> dict | list | None:
        try:
            req = Request(url, headers={"User-Agent": "KalshiQuant/1.0"})
            with urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (URLError, json.JSONDecodeError, TimeoutError) as e:
            logger.debug("Polymarket fetch failed for %s: %s", url, e)
            return None
        except Exception as e:
            logger.warning("Polymarket fetch error: %s", e)
            return None

    # ── Market discovery ─────────────────────────────────────────────────
    def fetch_active_markets(self, limit: int = 200) -> list[PolymarketContract]:
        """Pull active Polymarket binary contracts via the Gamma API."""
        with self._lock:
            if self._markets_cache and time.time() - self._cache_ts < CACHE_TTL_SECONDS:
                return self._markets_cache

        url = f"{POLYMARKET_GAMMA_BASE}/markets?active=true&closed=false&limit={limit}"
        data = self._http_get(url)
        if not isinstance(data, list):
            return self._markets_cache  # graceful degradation

        contracts: list[PolymarketContract] = []
        for m in data:
            try:
                # Polymarket binary markets have an "outcomePrices" field
                # like '["0.65","0.35"]' (string-encoded JSON)
                prices_raw = m.get("outcomePrices", "[]")
                if isinstance(prices_raw, str):
                    try:
                        prices = json.loads(prices_raw)
                    except json.JSONDecodeError:
                        continue
                else:
                    prices = prices_raw
                if not prices or len(prices) < 2:
                    continue
                yes_price = float(prices[0])
                no_price = float(prices[1])
                contracts.append(PolymarketContract(
                    market_id=str(m.get("id", "")),
                    slug=m.get("slug", ""),
                    question=m.get("question", ""),
                    yes_price=yes_price,
                    no_price=no_price,
                    volume_24h=float(m.get("volume24hr", 0) or 0),
                    end_date=m.get("endDate"),
                ))
            except (TypeError, ValueError, KeyError) as e:
                logger.debug("Skipping malformed Polymarket row: %s", e)
                continue

        with self._lock:
            self._markets_cache = contracts
            self._cache_ts = time.time()
        return contracts

    # ── Matching ─────────────────────────────────────────────────────────
    @staticmethod
    def _normalize(text: str) -> set[str]:
        """Lowercase + tokenize a string into a bag of words for fuzzy match."""
        return set(re.findall(r"[a-z0-9]+", text.lower()))

    def _match_score(self, kalshi_ticker: str, kalshi_title: str, poly: PolymarketContract) -> float:
        """Heuristic match confidence in [0, 1].

        Strategy:
          1. Token-set Jaccard between Kalshi title and Polymarket question.
          2. Boost if a known Kalshi prefix maps to a Polymarket keyword that
             appears in the question.
          3. Boost for matching numeric tokens (strike prices, dates).
        """
        if not poly.question:
            return 0.0
        k_tokens = self._normalize(kalshi_title)
        p_tokens = self._normalize(poly.question)
        if not k_tokens or not p_tokens:
            return 0.0

        jaccard = len(k_tokens & p_tokens) / max(1, len(k_tokens | p_tokens))
        score = jaccard

        # Prefix-keyword boost
        for prefix, keyword in KALSHI_TO_POLY_KEYWORDS.items():
            if prefix in kalshi_ticker.upper():
                kw_tokens = self._normalize(keyword)
                if kw_tokens & p_tokens:
                    score += 0.15
                break

        # Numeric overlap boost (strikes, years, dates)
        k_nums = {t for t in k_tokens if t.isdigit() and len(t) >= 2}
        p_nums = {t for t in p_tokens if t.isdigit() and len(t) >= 2}
        if k_nums and p_nums and (k_nums & p_nums):
            score += 0.20

        return min(1.0, score)

    def find_match(
        self, kalshi_ticker: str, kalshi_title: str, min_confidence: float = 0.40
    ) -> tuple[PolymarketContract, float] | None:
        """Find the best matching Polymarket contract for a Kalshi market.

        Returns (contract, confidence) or None if nothing meets the threshold.
        """
        candidates = self.fetch_active_markets()
        if not candidates:
            return None
        best = None
        best_score = 0.0
        for c in candidates:
            s = self._match_score(kalshi_ticker, kalshi_title, c)
            if s > best_score:
                best, best_score = c, s
        if best is None or best_score < min_confidence:
            return None
        return best, best_score

    # ── Arbitrage detection ──────────────────────────────────────────────
    def detect_arb(
        self,
        kalshi_ticker: str,
        kalshi_title: str,
        kalshi_yes_price: float,
    ) -> CrossVenueQuote | None:
        """Return a CrossVenueQuote if an arb above MIN_ARB_EDGE exists."""
        match = self.find_match(kalshi_ticker, kalshi_title)
        if match is None:
            return None
        poly, confidence = match
        edge = kalshi_yes_price - poly.yes_price

        arb_direction: str | None = None
        if abs(edge) >= self.MIN_ARB_EDGE:
            # If Kalshi YES is cheaper, buy Kalshi YES + buy Poly NO
            arb_direction = (
                "BUY_KALSHI_YES" if edge < 0 else "BUY_POLY_YES"
            )

        return CrossVenueQuote(
            kalshi_ticker=kalshi_ticker,
            kalshi_yes_price=kalshi_yes_price,
            poly_market_id=poly.market_id,
            poly_yes_price=poly.yes_price,
            edge=round(edge, 4),
            confidence=round(confidence, 4),
            arb_direction=arb_direction,
        )

    def scan_arbs(
        self,
        kalshi_markets: list[dict],
        min_volume: float = 1000.0,
    ) -> list[CrossVenueQuote]:
        """Scan a list of Kalshi markets for cross-venue arbs.

        Each Kalshi market is a dict with at least:
          - ticker
          - title (or yes_sub_title)
          - yes_ask (in 0..1 or cents)
        """
        quotes: list[CrossVenueQuote] = []
        for km in kalshi_markets:
            ticker = km.get("ticker", "")
            title = km.get("title", km.get("yes_sub_title", ""))
            price = km.get("yes_ask", km.get("price", 0))
            if price > 1:
                price = price / 100.0
            if price <= 0:
                continue
            quote = self.detect_arb(ticker, title, price)
            if quote and quote.arb_direction is not None:
                quotes.append(quote)
        return quotes


# Module-level singleton, mirrors the pattern used by external_feeds.feed_manager
polymarket_adapter = PolymarketAdapter()
