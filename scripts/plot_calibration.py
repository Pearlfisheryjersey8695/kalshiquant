"""Generate the calibration reliability diagram from the backfill data.

Reads `data/calibration_training_data.json` (produced by backfill_calibration.py)
and writes `docs/figures/calibration_curve.png`.

Reliability diagram convention (Niculescu-Mizil & Caruana 2005):
  - x-axis: predicted probability (here: market quote in cents-as-decimal)
  - y-axis: empirical settlement frequency
  - bins:   10 quantile bins
  - reference: y = x (perfect calibration)
  - overlay:  the fitted isotonic curve

The bottom panel shows the histogram of quotes — calibration is only meaningful
in regions with samples.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # non-interactive backend
import matplotlib.pyplot as plt
import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_PATH = PROJECT_ROOT / "data" / "calibration_training_data.json"
CAL_PATH = PROJECT_ROOT / "models" / "saved" / "win_prob_calibration.json"
OUT_PATH = PROJECT_ROOT / "docs" / "figures" / "calibration_curve.png"


def _bin_pairs(pairs: list[tuple[float, float]], n_bins: int = 10):
    """Quantile-bin the (quote, outcome) pairs and return per-bin stats."""
    pairs_sorted = sorted(pairs, key=lambda p: p[0])
    n = len(pairs_sorted)
    if n == 0:
        return [], [], [], []

    bin_size = max(1, n // n_bins)
    bin_x, bin_y, bin_se, bin_n = [], [], [], []
    for i in range(0, n, bin_size):
        chunk = pairs_sorted[i:i + bin_size]
        if not chunk:
            continue
        xs = [p[0] for p in chunk]
        ys = [p[1] for p in chunk]
        x_mean = sum(xs) / len(xs)
        y_mean = sum(ys) / len(ys)
        # Standard error of a binomial proportion: sqrt(p(1-p)/n)
        se = math.sqrt(max(y_mean * (1 - y_mean), 1e-9) / len(ys))
        bin_x.append(x_mean)
        bin_y.append(y_mean)
        bin_se.append(se)
        bin_n.append(len(ys))
    return bin_x, bin_y, bin_se, bin_n


def _bootstrap_brier_ci(pairs: list[tuple[float, float]], n_iter: int = 1000) -> tuple[float, float, float]:
    """Bootstrap a 95% CI on the Brier score over the (quote, outcome) pairs."""
    rng = np.random.default_rng(42)
    n = len(pairs)
    if n == 0:
        return 0.0, 0.0, 0.0
    arr = np.array(pairs, dtype=float)
    briers = []
    for _ in range(n_iter):
        idx = rng.integers(0, n, size=n)
        sample = arr[idx]
        b = float(np.mean((sample[:, 0] - sample[:, 1]) ** 2))
        briers.append(b)
    point = float(np.mean((arr[:, 0] - arr[:, 1]) ** 2))
    lo = float(np.quantile(briers, 0.025))
    hi = float(np.quantile(briers, 0.975))
    return point, lo, hi


def main() -> int:
    if not DATA_PATH.exists():
        print(f"ERROR: {DATA_PATH} not found. Run scripts/backfill_calibration.py first.", file=sys.stderr)
        return 1

    with open(DATA_PATH) as f:
        data = json.load(f)

    pairs = [(float(p[0]), float(p[1])) for p in data["pairs"]]
    n_yes = data["n_yes"]
    n_no = data["n_no"]

    bin_x, bin_y, bin_se, bin_n = _bin_pairs(pairs, n_bins=10)
    brier_point, brier_lo, brier_hi = _bootstrap_brier_ci(pairs)

    # Load fitted isotonic curve
    iso_x, iso_y = [], []
    if CAL_PATH.exists():
        with open(CAL_PATH) as f:
            cal = json.load(f)
        iso_x = cal.get("x_grid", [])
        iso_y = cal.get("y_grid", [])

    # ── Plot ──────────────────────────────────────────────────────────
    fig, (ax1, ax2) = plt.subplots(
        2, 1, figsize=(8, 9),
        gridspec_kw={"height_ratios": [3, 1], "hspace": 0.05},
        sharex=True,
    )

    # Top: reliability diagram
    ax1.plot([0, 1], [0, 1], "--", color="gray", linewidth=1, label="Perfect calibration ($y=x$)", zorder=1)
    if iso_x:
        ax1.plot(iso_x, iso_y, "-", color="#1f77b4", linewidth=2,
                 label=f"Fitted isotonic (n={len(pairs)})", zorder=3)

    # Empirical bins with binomial error bars
    ax1.errorbar(
        bin_x, bin_y, yerr=[1.96 * s for s in bin_se],
        fmt="o", color="#d62728", markersize=8, capsize=4,
        label="Empirical (10 quantile bins, 95% CI)", zorder=4,
    )

    ax1.set_xlim(-0.02, 1.02)
    ax1.set_ylim(-0.02, 1.02)
    ax1.set_ylabel("Empirical settlement frequency", fontsize=12)
    ax1.set_title(
        "Kalshi quote calibration: 743 settled markets\n"
        f"Brier (market) = {brier_point:.4f} [{brier_lo:.4f}, {brier_hi:.4f}]   "
        f"vs naive 0.5 = 0.2500   alpha = +{0.25 - brier_point:.4f}",
        fontsize=12,
    )
    ax1.legend(loc="upper left", fontsize=10, framealpha=0.95)
    ax1.grid(True, alpha=0.3)

    # Annotate the most striking finding
    if bin_x:
        # Find the bin closest to 0.30
        target = 0.30
        idx = min(range(len(bin_x)), key=lambda i: abs(bin_x[i] - target))
        x0, y0 = bin_x[idx], bin_y[idx]
        ax1.annotate(
            f"At quote≈{x0:.2f},\nempirical hit rate {y0:.1%}\n(market overprices YES)",
            xy=(x0, y0), xytext=(0.45, 0.15),
            fontsize=10,
            arrowprops=dict(arrowstyle="->", color="black", lw=1),
            bbox=dict(boxstyle="round,pad=0.4", fc="lightyellow", ec="gray"),
        )

    # Bottom: histogram of quotes
    quotes = [p[0] for p in pairs]
    ax2.hist(quotes, bins=30, color="#7f7f7f", alpha=0.7, edgecolor="black", linewidth=0.3)
    ax2.set_xlabel("Market quote (implied YES probability)", fontsize=12)
    ax2.set_ylabel("Count", fontsize=10)
    ax2.set_xlim(-0.02, 1.02)
    ax2.grid(True, alpha=0.3)

    fig.text(
        0.99, 0.01,
        f"Class balance: {n_yes} YES / {n_no} NO  ({n_yes/(n_yes+n_no):.1%})",
        ha="right", fontsize=8, color="gray",
    )

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(OUT_PATH, dpi=150, bbox_inches="tight")
    print(f"Wrote {OUT_PATH}")
    print(f"  Brier: {brier_point:.4f} [95% CI {brier_lo:.4f}, {brier_hi:.4f}]")
    print(f"  Alpha vs naive: +{0.25 - brier_point:.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
