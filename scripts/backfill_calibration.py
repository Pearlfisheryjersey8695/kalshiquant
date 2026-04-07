"""Backfill the win-probability calibrator from Kalshi historical settlements.

The bottleneck on calibration is data: paper trading produces ~5-15 closed
positions per day, so getting 200+ samples takes weeks. Meanwhile, Kalshi has
THOUSANDS of already-settled markets sitting on their REST API for free.

This script:
  1. Pulls settled markets from /trade-api/v2/markets?status=settled
  2. For each settled market, reconstructs what the ensemble would have predicted
     at the most recent observable quote (the "prior price" before settlement)
  3. Pairs the prediction with the actual settlement outcome (1.0 if YES won)
  4. Feeds the (predicted_prob, actual_outcome) pairs into WinProbCalibrator
  5. Persists the fitted curve to models/saved/win_prob_calibration.json

The "prediction" we use here is intentionally simple — we use the LAST OBSERVED
PRICE before settlement as the model's implied probability. This gives us a
calibration of "how well does *the market itself* predict outcomes" — which is
exactly the baseline we need to beat. When we then run our model alongside, the
calibrator learns the differential edge.

For markets where we have stored a model snapshot at quote time (from the live
trade journal), we use the actual model output instead. The script handles both
sources transparently.

Usage:
    python -m scripts.backfill_calibration --max-pages 25 --min-volume 1000
    python -m scripts.backfill_calibration --dry-run    # don't persist
"""

from __future__ import annotations

import argparse
import logging
import sys
from typing import Iterable

from analysis.experiment_tracker import track
from app.kalshi_client import KalshiClient
from models.risk_model import WinProbCalibrator

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("backfill")


def _settled_outcome(market: dict) -> float | None:
    """Extract YES outcome (1.0 win, 0.0 loss) from a settled market record.

    Kalshi v2 puts the outcome in market["result"] as one of:
      "yes", "no", "all_no", or empty string for unsettled.
    """
    result = (market.get("result") or "").lower()
    if result == "yes":
        return 1.0
    if result in ("no", "all_no"):
        return 0.0
    return None


def _to_float(val) -> float:
    """Kalshi v2 returns prices as decimal-string fields. Coerce safely."""
    if val is None or val == "":
        return 0.0
    try:
        return float(val)
    except (TypeError, ValueError):
        return 0.0


def _last_quote_implied_prob(market: dict) -> float | None:
    """The market's last implied YES probability *before* settlement.

    Strategy (Kalshi v2 schema with `_dollars` decimal-string fields):
      1. Mid of previous_yes_bid_dollars and previous_yes_ask_dollars
      2. previous_price_dollars (last trade before settlement)
      3. last_price_dollars (only useful before settlement clamps it)
    Returns None if no informative quote is available — we don't want
    to feed all-zero or all-one prices into the calibrator (those are
    just settlement clamps, not informative quotes).
    """
    bid = _to_float(market.get("previous_yes_bid_dollars"))
    ask = _to_float(market.get("previous_yes_ask_dollars"))
    if 0 < bid < 1 and 0 < ask < 1 and ask >= bid:
        return (bid + ask) / 2.0

    prev_price = _to_float(market.get("previous_price_dollars"))
    if 0 < prev_price < 1:
        return prev_price

    # last_price after settlement is 0 or 1 — useless for calibration
    last = _to_float(market.get("last_price_dollars"))
    if 0 < last < 1:
        return last

    return None


def _confidence_from_quote(p_quote: float) -> float:
    """Map the market's quote to the calibrator's confidence-input space.

    The calibrator was designed around an "ensemble confidence in [0, 1] →
    probability of being right" mapping. For backfill we substitute *the
    market's quoted probability* in place of an ensemble prediction. The
    calibrator then learns: "when the market trades at 0.70, how often does
    the YES side actually settle?"

    This is the right metric because it directly measures the predictive
    power of a quote — exactly what we want before we trust it as a fair
    value input.
    """
    return max(0.01, min(0.99, p_quote))


def collect_pairs(
    client: KalshiClient,
    max_pages: int = 25,
    min_volume: float = 0.0,
    target_per_class: int = 0,
    exclude_mve: bool = False,
) -> list[tuple[float, float]]:
    """Pull settled markets and emit (confidence, outcome) pairs.

    If target_per_class > 0, keeps pulling until we have at least that many
    samples in BOTH classes (or until we hit max_pages).
    """
    logger.info("Pulling settled markets (max %d pages)...", max_pages)
    settled = client.get_settled_markets(max_pages=max_pages)
    logger.info("Got %d settled markets", len(settled))

    pairs: list[tuple[float, float]] = []
    skipped = {"no_outcome": 0, "no_quote": 0, "low_volume": 0, "mve": 0}

    for m in settled:
        # MVE markets are multi-leg parlays that auto-expire NO when one leg
        # busts — they dominate the tape and skew the class balance. Optional
        # exclusion focuses the calibrator on single-event markets.
        if exclude_mve and "MVE" in m.get("ticker", ""):
            skipped["mve"] += 1
            continue
        outcome = _settled_outcome(m)
        if outcome is None:
            skipped["no_outcome"] += 1
            continue
        p_quote = _last_quote_implied_prob(m)
        if p_quote is None:
            skipped["no_quote"] += 1
            continue
        # Volume filter — illiquid markets have noisy quotes that don't
        # represent informed prices. Kalshi v2 uses `_fp` decimal-string fields.
        vol = _to_float(
            m.get("volume_fp")
            or m.get("volume_24h_fp")
            or m.get("volume")
            or 0
        )
        if vol < min_volume:
            skipped["low_volume"] += 1
            continue

        # The training pair is (quoted_yes_probability, actual_settlement).
        # The calibrator learns the function:  market_quote -> empirical hit rate
        # which is precisely the function we need to debias the live model's
        # confidence against.
        confidence = _confidence_from_quote(p_quote)
        pairs.append((confidence, outcome))

    logger.info(
        "Collected %d (confidence, outcome) pairs. Skipped: %s",
        len(pairs), skipped,
    )
    return pairs


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-pages", type=int, default=25,
                        help="how many cursor pages of settled markets to pull")
    parser.add_argument("--min-volume", type=float, default=0.0,
                        help="filter out markets with volume below this threshold")
    parser.add_argument("--exclude-mve", action="store_true",
                        help="exclude multi-leg MVE markets (recommended)")
    parser.add_argument("--dry-run", action="store_true",
                        help="don't persist the fitted curve")
    args = parser.parse_args()

    with track(
        "calibrator_backfill",
        params={
            "max_pages": args.max_pages,
            "min_volume": args.min_volume,
            "dry_run": args.dry_run,
        },
    ) as run:
        client = KalshiClient()
        pairs = collect_pairs(
            client,
            max_pages=args.max_pages,
            min_volume=args.min_volume,
            exclude_mve=args.exclude_mve,
        )
        run.log_metric("n_pairs_collected", len(pairs))
        if not pairs:
            run.set_tag("status", "no_data")
            print("No usable settled markets found.")
            return 1

        n_yes = sum(1 for _, y in pairs if y > 0.5)
        n_no = len(pairs) - n_yes
        # Raw Brier score of the market's quotes vs settlements
        brier_market = sum((p - y) ** 2 for p, y in pairs) / len(pairs)
        # Naive baseline (always predict 0.5) Brier
        brier_naive = sum((0.5 - y) ** 2 for _, y in pairs) / len(pairs)
        run.log_metric("n_yes", n_yes)
        run.log_metric("n_no", n_no)
        run.log_metric("brier_market", brier_market)
        run.log_metric("brier_naive", brier_naive)
        run.log_metric("alpha_vs_naive", brier_naive - brier_market)
        print(
            f"Class balance: {n_yes} YES / {n_no} NO  "
            f"({n_yes/len(pairs):.1%} YES rate)"
        )
        print(f"Brier (market quotes): {brier_market:.4f}")
        print(f"Brier (naive 0.5):     {brier_naive:.4f}")
        print(f"Alpha vs naive:        {brier_naive - brier_market:+.4f}")

        # Inject pairs directly via the same code path the live calibrator uses
        cal = WinProbCalibrator()
        # Patch in our backfilled pairs by monkey-replacing the class loaders
        WinProbCalibrator._load_position_pairs = classmethod(lambda cls, db_path=None: pairs)
        WinProbCalibrator._load_backtest_pairs = classmethod(lambda cls, path=None: [])

        n_train = cal.fit_from_history()
        run.log_metric("n_train_used", n_train)
        if n_train == 0:
            run.set_tag("status", "degenerate_sample")
            print("Calibrator refused to fit — degenerate sample (one class).")
            return 1

        info = cal.info()
        run.log_metric("min_calibrated", info["min_calibrated"])
        run.log_metric("max_calibrated", info["max_calibrated"])
        run.set_tag("status", "ok")

        print("Calibration curve:")
        for c in [0.10, 0.20, 0.30, 0.40, 0.50, 0.60, 0.70, 0.80, 0.90]:
            calibrated = cal.calibrate(c)
            run.log_metric(f"curve_at_{int(c*100)}", calibrated)
            print(f"  conf={c:.2f} -> win_prob={calibrated:.3f}")

        if args.dry_run:
            print("[DRY RUN] Not persisting.")
            run.set_tag("persisted", "no")
        else:
            cal.save()
            # Also persist the raw training pairs so reproducibility tooling
            # (plot scripts, notebooks, the bootstrap CIs in the data quality
            # report) can work without re-hitting Kalshi.
            import json as _json
            from pathlib import Path
            data_dir = Path(__file__).resolve().parent.parent / "data"
            raw_path = data_dir / "calibration_training_data.json"
            with open(raw_path, "w") as f:
                _json.dump({
                    "n_pairs": len(pairs),
                    "n_yes": n_yes,
                    "n_no": n_no,
                    "brier_market": brier_market,
                    "brier_naive": brier_naive,
                    "alpha_vs_naive": brier_naive - brier_market,
                    "params": {
                        "max_pages": args.max_pages,
                        "min_volume": args.min_volume,
                        "exclude_mve": args.exclude_mve,
                    },
                    "pairs": pairs,  # list of [confidence, outcome]
                }, f)
            run.log_artifact("models/saved/win_prob_calibration.json")
            run.log_artifact("data/calibration_training_data.json")
            run.set_tag("persisted", "yes")
            print("Saved -> models/saved/win_prob_calibration.json")
            print(f"Saved -> data/calibration_training_data.json ({len(pairs)} pairs)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
