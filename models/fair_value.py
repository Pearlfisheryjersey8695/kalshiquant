"""
Step 2.2 -- Bayesian Fair Value Model
Estimates fair probability by combining:
  1. Base rate prior (historical average price)
  2. Orderbook signal (imbalance + VWAP divergence)
  3. Cross-market signal (correlation divergences)
  4. Time decay (certainty amplification near expiry)
  5. Sentiment signal (economic consensus + AI estimate)

Weights adapt via inverse-error weighting with exponential decay.
fair_value = w1*base + w2*orderbook + w3*cross + w4*decay + w5*sentiment
edge = fair_value - market_price
"""

import logging

import numpy as np
import pandas as pd
from models.base import BaseModel, registry

logger = logging.getLogger("kalshi.fair_value")

COMPONENT_NAMES = ["base_rate", "orderbook", "cross_market", "time_decay", "sentiment"]


class FairValueModel(BaseModel):
    name = "fair_value"

    def __init__(self):
        # Initial weights — external base rate is PRIMARY alpha source
        self._weights = np.array([0.50, 0.15, 0.10, 0.15, 0.10])
        self._base_rates = {}   # ticker -> rolling mean price
        self._cross_corr = {}   # (ticker_a, ticker_b) -> correlation
        self._scored_map = {}   # ticker -> {title, category}
        self._expiry_map = {}   # ticker -> expiration_time string

        # Adaptive weight tracking
        self._error_history: dict[str, list[float]] = {n: [] for n in COMPONENT_NAMES}
        self._decay_factor = 0.95
        self._min_window = 10
        self._weight_history: list[dict] = []

    @property
    def w_base(self):
        return float(self._weights[0])

    @property
    def w_orderbook(self):
        return float(self._weights[1])

    @property
    def w_cross(self):
        return float(self._weights[2])

    @property
    def w_time(self):
        return float(self._weights[3])

    @property
    def w_sentiment(self):
        return float(self._weights[4])

    def set_scored_map(self, scored_map: dict):
        """Set ticker metadata (title, category) for sentiment lookups."""
        self._scored_map = scored_map

    def get_current_weights(self) -> dict[str, float]:
        """Return current adaptive weights for display."""
        return {n: round(float(w), 4) for n, w in zip(COMPONENT_NAMES, self._weights)}

    def get_weight_history(self) -> list[dict]:
        """Return weight history for analysis."""
        return self._weight_history[-50:]

    def fit(self, data: pd.DataFrame):
        """Compute base rates and cross-market correlations from feature data."""
        for ticker, grp in data.groupby("ticker"):
            self._base_rates[ticker] = grp["close"].mean()

        # Cross-correlations between tickers sharing a series prefix
        pivot = data.pivot_table(index=data.index, columns="ticker", values="close")
        pivot = pivot.ffill().dropna(axis=1, how="all")
        if pivot.shape[1] > 1:
            corr = pivot.corr()
            for i, t1 in enumerate(corr.columns):
                for t2 in corr.columns[i+1:]:
                    self._cross_corr[(t1, t2)] = corr.loc[t1, t2]

    # ── Adaptive weight logic ──────────────────────────────────────────────

    def record_component_errors(self, components: dict[str, float], actual_next_price: float):
        """Record prediction error for each component (called during evaluation).
        actual_next_price = the price at the next bar.
        """
        for name in COMPONENT_NAMES:
            if name in components:
                error = abs(components[name] - actual_next_price)
                self._error_history[name].append(error)
                if len(self._error_history[name]) > 200:
                    self._error_history[name] = self._error_history[name][-100:]

        self._adapt_weights()

    def _adapt_weights(self):
        """Compute inverse-error weights from component prediction errors."""
        if all(len(v) < self._min_window for v in self._error_history.values()):
            return  # not enough data

        errors = []
        for name in COMPONENT_NAMES:
            hist = self._error_history[name]
            if len(hist) < self._min_window:
                errors.append(0.1)
                continue
            recent = hist[-50:]
            weights = np.array([
                self._decay_factor ** (len(recent) - 1 - i)
                for i in range(len(recent))
            ])
            weighted_sq_errors = np.array(recent) ** 2 * weights
            rmse = np.sqrt(np.sum(weighted_sq_errors) / np.sum(weights))
            errors.append(max(rmse, 0.001))

        inv_errors = np.array([1.0 / e for e in errors])
        new_weights = inv_errors / inv_errors.sum()

        # Smooth transition: blend 70% new + 30% old to prevent wild swings
        blended = 0.7 * new_weights + 0.3 * self._weights

        # Component-specific floors: external base_rate is PRIMARY alpha source.
        # Sentiment is secondary. Price-trackers (orderbook, cross_market)
        # are accurate but generate zero edge.
        # [base_rate, orderbook, cross_market, time_decay, sentiment]
        FLOORS = np.array([0.25, 0.05, 0.05, 0.10, 0.05])
        blended = np.maximum(blended, FLOORS)
        self._weights = blended / blended.sum()

        # Log
        self._weight_history.append(self.get_current_weights())

    # ── Component signals ──────────────────────────────────────────────────

    def _base_rate_signal(self, ticker, current_price):
        """Base rate from EXTERNAL data — the core alpha source."""
        # Try external feed first
        from data.external_feeds import feed_manager

        # Get hours to expiry (needed for probability model)
        hours_to_expiry = 999
        exp_time = self._expiry_map.get(ticker, "")
        if exp_time:
            try:
                from datetime import datetime, timezone
                exp = datetime.fromisoformat(str(exp_time).replace("Z", "+00:00"))
                hours_to_expiry = max(0, (exp - datetime.now(timezone.utc)).total_seconds() / 3600)
            except Exception:
                pass

        ext_result = feed_manager.get_probability_for_ticker(ticker, current_price, hours_to_expiry)
        if ext_result and not ext_result.get("stale", True):
            return ext_result["probability"]

        # Fallback: historical base rate (weak signal, not alpha)
        return self._base_rates.get(ticker, current_price)

    def _orderbook_signal(self, row):
        """Orderbook imbalance biases fair value up (buy pressure) or down."""
        imb = row.get("orderbook_imbalance", 0)
        mid = row.get("mid_price", row.get("close", 0.5))
        shift = imb * 0.05
        return np.clip(mid + shift, 0.01, 0.99)

    def _cross_market_signal(self, ticker, current_price, all_prices):
        """If correlated markets diverge, fair value adjusts toward peers."""
        adjustments = []
        weights = []
        for (t1, t2), corr in self._cross_corr.items():
            if not np.isfinite(corr) or abs(corr) < 0.3:
                continue
            peer = None
            if t1 == ticker and t2 in all_prices:
                peer = all_prices[t2]
            elif t2 == ticker and t1 in all_prices:
                peer = all_prices[t1]
            if peer is not None:
                peer_base = self._base_rates.get(t1 if t1 != ticker else t2, peer)
                if peer_base > 0:
                    peer_ratio = peer / peer_base
                    adjustments.append(current_price * peer_ratio)
                    weights.append(abs(corr))

        if adjustments:
            weights = np.array(weights)
            adj = np.array(adjustments)
            valid = np.isfinite(adj)
            if valid.any():
                return float(np.average(adj[valid], weights=weights[valid]))
        return current_price

    def _time_decay_signal(self, row, current_price):
        """Near expiry, certainty increases: amplify distance from 0.5."""
        hours = row.get("time_to_expiry_hours", 1000)
        if hours <= 0:
            hours = 0.1
        if hours > 168:
            return current_price
        convergence_strength = max(0, 1 - hours / 168)
        amplification = 0.3
        decayed = 0.5 + (current_price - 0.5) * (1 + convergence_strength * amplification)
        return np.clip(decayed, 0.01, 0.99)

    def _sentiment_signal(self, ticker, current_price):
        """Sentiment: consensus + AI probability estimate."""
        try:
            from pipeline.sentiment import get_consensus_edge
            result = get_consensus_edge(ticker, current_price)
            edge = result.get("consensus_edge", 0)
            return np.clip(current_price + edge, 0.01, 0.99)
        except Exception:
            return current_price

    # ── Predict ────────────────────────────────────────────────────────────

    def predict(self, data: pd.DataFrame) -> pd.DataFrame:
        """Generate fair value estimates for each row."""
        latest = data.groupby("ticker")["close"].last().to_dict()

        results = []
        prev_prices: dict[str, float] = {}  # for error tracking

        for idx, row in data.iterrows():
            ticker = row["ticker"]
            current = row["close"]

            base = self._base_rate_signal(ticker, current)
            ob = self._orderbook_signal(row)
            cross = self._cross_market_signal(ticker, current, latest)
            decay = self._time_decay_signal(row, current)
            sentiment = self._sentiment_signal(ticker, current)

            components = {
                "base_rate": base,
                "orderbook": ob,
                "cross_market": cross,
                "time_decay": decay,
                "sentiment": sentiment,
            }

            # Record errors using previous bar's components vs current price
            prev_key = f"{ticker}_components"
            if prev_key in prev_prices:
                self.record_component_errors(prev_prices[prev_key], current)
            prev_prices[prev_key] = components

            # Compute fair value with current (possibly adapted) weights
            fv = float(np.dot(self._weights, [base, ob, cross, decay, sentiment]))
            fv = np.clip(fv, 0.01, 0.99)
            edge = fv - current

            results.append({
                "timestamp": idx,
                "ticker": ticker,
                "current_price": current,
                "fair_value": round(fv, 4),
                "edge": round(edge, 4),
                "base_rate": round(base, 4),
                "ob_signal": round(ob, 4),
                "cross_signal": round(cross, 4),
                "decay_signal": round(decay, 4),
                "sentiment_signal": round(sentiment, 4),
            })

        return pd.DataFrame(results)

    def get_signals(self, data: pd.DataFrame, min_edge=0.05):
        """Return only markets with |edge| > min_edge."""
        fv_df = self.predict(data)
        latest = fv_df.sort_values("timestamp").groupby("ticker").last().reset_index()
        signals = latest[latest["edge"].abs() >= min_edge].copy()
        signals["direction"] = signals["edge"].apply(
            lambda e: "BUY_YES" if e > 0 else "BUY_NO"
        )
        return signals.sort_values("edge", key=abs, ascending=False)


registry.register(FairValueModel())
