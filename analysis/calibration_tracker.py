"""
Calibration tracker: measures whether our fair value estimates are accurate.

The single most important diagnostic — if the Brier score is worse than
"always predict market price," the model has NEGATIVE alpha.

Tracks:
  - Brier score per market category
  - Calibration curve (predicted probability vs actual frequency)
  - Edge accuracy (FV estimate vs settlement)
  - Win/loss analysis by conviction level
"""

import json
import logging
import os
import math
from collections import defaultdict
from datetime import datetime, timezone

logger = logging.getLogger("kalshi.calibration")

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TRACKER_PATH = os.path.join(PROJECT_ROOT, "models", "saved", "calibration_data.json")


class CalibrationTracker:
    """Track fair value predictions vs actual outcomes."""

    def __init__(self):
        self.records: list = []  # {ticker, predicted_prob, market_price, settlement, category, timestamp}
        self._load()

    def record_prediction(self, ticker: str, predicted_prob: float, market_price: float,
                          category: str = "", external_prob: float = None):
        """Record a fair value prediction for later comparison with settlement."""
        self.records.append({
            "ticker": ticker,
            "predicted_prob": round(predicted_prob, 4),
            "market_price": round(market_price, 4),
            "external_prob": round(external_prob, 4) if external_prob is not None else None,
            "settlement": None,  # filled when market settles
            "category": category,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    def record_settlement(self, ticker: str, settlement_price: float):
        """Record the actual settlement (0 or 1) for a market."""
        for r in reversed(self.records):
            if r["ticker"] == ticker and r["settlement"] is None:
                r["settlement"] = round(settlement_price, 4)
                break
        self._save()

    def get_brier_score(self, category: str = None) -> dict:
        """
        Brier score = mean((predicted - outcome)^2).
        Lower is better. 0.25 = always predicting 0.5 (naive).

        Also computes Brier score for market price (benchmark).
        Alpha = market_brier - model_brier > 0 means we're better than market.
        """
        settled = [r for r in self.records if r["settlement"] is not None]
        if category:
            settled = [r for r in settled if r["category"] == category]

        if not settled:
            return {"model_brier": None, "market_brier": None, "alpha": None,
                    "n_settled": 0, "message": "No settled markets yet"}

        model_scores = [(r["predicted_prob"] - r["settlement"]) ** 2 for r in settled]
        market_scores = [(r["market_price"] - r["settlement"]) ** 2 for r in settled]
        naive_score = 0.25  # always predict 0.5

        model_brier = sum(model_scores) / len(model_scores)
        market_brier = sum(market_scores) / len(market_scores)
        alpha = market_brier - model_brier  # positive = we're better

        return {
            "model_brier": round(model_brier, 4),
            "market_brier": round(market_brier, 4),
            "naive_brier": naive_score,
            "alpha": round(alpha, 4),
            "better_than_market": alpha > 0,
            "better_than_naive": model_brier < naive_score,
            "n_settled": len(settled),
        }

    def get_calibration_curve(self, n_bins: int = 10) -> list:
        """
        Calibration curve: group predictions into probability bins,
        compare predicted probability to actual frequency of YES outcomes.

        Perfect calibration: predicted 30% → actually happens 30% of the time.
        """
        settled = [r for r in self.records if r["settlement"] is not None]
        if not settled:
            return []

        bin_size = 1.0 / n_bins
        bins = []
        for i in range(n_bins):
            low = i * bin_size
            high = (i + 1) * bin_size
            mid = (low + high) / 2

            in_bin = [r for r in settled if low <= r["predicted_prob"] < high]
            if in_bin:
                avg_predicted = sum(r["predicted_prob"] for r in in_bin) / len(in_bin)
                actual_freq = sum(1 for r in in_bin if r["settlement"] > 0.5) / len(in_bin)
                bins.append({
                    "bin_mid": round(mid, 2),
                    "avg_predicted": round(avg_predicted, 4),
                    "actual_frequency": round(actual_freq, 4),
                    "count": len(in_bin),
                    "calibration_error": round(abs(avg_predicted - actual_freq), 4),
                })

        return bins

    def get_category_breakdown(self) -> dict:
        """Brier score and trade count per market category."""
        categories = defaultdict(list)
        for r in self.records:
            if r["settlement"] is not None:
                categories[r.get("category", "unknown")].append(r)

        result = {}
        for cat, records in categories.items():
            model_scores = [(r["predicted_prob"] - r["settlement"]) ** 2 for r in records]
            result[cat] = {
                "brier": round(sum(model_scores) / len(model_scores), 4),
                "n_settled": len(records),
                "avg_edge": round(
                    sum(abs(r["predicted_prob"] - r["market_price"]) for r in records) / len(records), 4
                ),
            }
        return result

    def get_summary(self) -> dict:
        """Full calibration summary for the dashboard."""
        total = len(self.records)
        settled = sum(1 for r in self.records if r["settlement"] is not None)
        pending = total - settled

        brier = self.get_brier_score()
        curve = self.get_calibration_curve()
        by_cat = self.get_category_breakdown()

        # External model accuracy (if available)
        ext_records = [r for r in self.records
                       if r.get("external_prob") is not None and r["settlement"] is not None]
        ext_brier = None
        if ext_records:
            ext_scores = [(r["external_prob"] - r["settlement"]) ** 2 for r in ext_records]
            ext_brier = round(sum(ext_scores) / len(ext_scores), 4)

        return {
            "total_predictions": total,
            "settled": settled,
            "pending": pending,
            "brier_score": brier,
            "external_brier": ext_brier,
            "calibration_curve": curve,
            "by_category": by_cat,
            "go_live_ready": (
                brier.get("better_than_market", False)
                and settled >= 50
                and brier.get("model_brier", 1) < 0.20
            ),
        }

    def _save(self):
        try:
            os.makedirs(os.path.dirname(TRACKER_PATH), exist_ok=True)
            with open(TRACKER_PATH, "w") as f:
                json.dump(self.records[-1000:], f)  # keep last 1000
        except Exception as e:
            logger.warning("Calibration save failed: %s", e)

    def _load(self):
        try:
            with open(TRACKER_PATH) as f:
                self.records = json.load(f)
            logger.info("Loaded %d calibration records", len(self.records))
        except (FileNotFoundError, json.JSONDecodeError):
            self.records = []


# Module singleton
calibration_tracker = CalibrationTracker()
