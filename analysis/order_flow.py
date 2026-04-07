"""Order flow toxicity signals: VPIN and Kyle's lambda.

These are the two market-microstructure metrics that *actually* matter for
short-horizon prediction-market trading. They tell you whether you're trading
against informed flow, which is the single biggest source of adverse selection.

VPIN (Volume-synchronized Probability of INformed trading)
----------------------------------------------------------
Easley, López de Prado & O'Hara (2012). Bucket the trade tape by equal *volume*
(not equal time) into V buckets. Within each bucket, classify volume as
buy-initiated or sell-initiated using a "bulk volume classification" based on
the standardized price change. VPIN = mean(|V_buy - V_sell| / V_total) over the
last N buckets.

Interpretation:
  VPIN ≈ 0    → balanced flow, low toxicity
  VPIN ≈ 0.5  → heavily one-sided, likely informed flow
  VPIN > 0.6  → strong adverse selection signal — REDUCE size or skip trade

Kyle's Lambda (price impact coefficient)
-----------------------------------------
Albert Kyle (1985). The slope coefficient from regressing price changes on
signed trade volume. λ = ΔP / (signed Q). High λ = market is illiquid OR there
is informed flow moving prices on small volume.

For prediction markets specifically:
  λ < 0.0001 / contract → deep, liquid book — large positions OK
  λ > 0.001 / contract  → shallow book — slippage risk on size

These functions are pure math. They take a trade list and a recent price series
and return a scalar score. The signal layer can use them as features (additional
input to the ensemble) or as gates (skip-trade conditions).
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass
class OrderFlowMetrics:
    vpin: float           # in [0, 1]; higher = more toxic
    kyle_lambda: float    # price impact per unit volume; in price units
    n_trades: int
    n_buckets: int
    avg_bucket_volume: float
    toxicity_label: str   # "low" / "moderate" / "high"


def _bulk_volume_classification(prices: list[float], volumes: list[int]) -> tuple[float, float]:
    """Estimate buy and sell volume from a sequence of trade ticks using BVC.

    BVC (Easley/López de Prado/O'Hara 2012):
        z = (P_t - P_{t-1}) / sigma_dP
        buy_frac  = N(z)         (standard normal CDF)
        sell_frac = 1 - buy_frac

    Returns (total_buy_volume, total_sell_volume).
    """
    if len(prices) < 2 or len(volumes) != len(prices):
        return 0.0, 0.0

    diffs = [prices[i] - prices[i - 1] for i in range(1, len(prices))]
    if not diffs:
        return 0.0, 0.0
    mean = sum(diffs) / len(diffs)
    var = sum((d - mean) ** 2 for d in diffs) / max(1, len(diffs))
    sigma = math.sqrt(var) if var > 0 else 1e-6

    def norm_cdf(x: float) -> float:
        if x > 6:
            return 1.0
        if x < -6:
            return 0.0
        t = 1.0 / (1.0 + 0.2316419 * abs(x))
        d = 0.3989423 * math.exp(-x * x / 2)
        p = d * t * (0.3193815 + t * (-0.3565638 + t * (1.781478 + t * (-1.821256 + t * 1.330274))))
        return 1.0 - p if x > 0 else p

    buy = 0.0
    sell = 0.0
    for i, d in enumerate(diffs):
        z = d / sigma if sigma > 0 else 0.0
        buy_frac = norm_cdf(z)
        v = volumes[i + 1]
        buy += buy_frac * v
        sell += (1.0 - buy_frac) * v
    return buy, sell


def compute_vpin(
    trades: list[dict],
    n_buckets: int = 5,
    bucket_volume: int | None = None,
) -> tuple[float, int, float]:
    """VPIN over the last `n_buckets` volume buckets.

    Parameters
    ----------
    trades : list[dict]
        Each trade has at least keys: 'yes_price' (cents int OR fractional float)
        and 'count' (volume).
    n_buckets : int
        Number of volume buckets to compute imbalance over.
    bucket_volume : int | None
        Volume per bucket. If None, computed as total_volume / (n_buckets * 2)
        so we get roughly twice the requested buckets across the tape.

    Returns
    -------
    (vpin, n_buckets_used, avg_bucket_volume)
    """
    if not trades:
        return 0.0, 0, 0.0

    # Normalise prices to [0,1] regardless of cent vs decimal convention
    prices = []
    volumes = []
    for t in trades:
        p = t.get("yes_price", 0.5)
        if p > 1:
            p = p / 100.0
        prices.append(p)
        volumes.append(int(t.get("count", 1)))

    total_vol = sum(volumes)
    if total_vol < n_buckets:
        return 0.0, 0, 0.0

    if bucket_volume is None:
        bucket_volume = max(1, total_vol // (n_buckets * 2))

    # Walk the tape, accumulating into buckets of size `bucket_volume`
    bucket_imbalances = []
    cur_bucket_prices = []
    cur_bucket_vols = []
    cur_total = 0
    for p, v in zip(prices, volumes):
        cur_bucket_prices.append(p)
        cur_bucket_vols.append(v)
        cur_total += v
        if cur_total >= bucket_volume:
            buy, sell = _bulk_volume_classification(cur_bucket_prices, cur_bucket_vols)
            tot = buy + sell
            if tot > 0:
                bucket_imbalances.append(abs(buy - sell) / tot)
            cur_bucket_prices = []
            cur_bucket_vols = []
            cur_total = 0

    if not bucket_imbalances:
        return 0.0, 0, float(bucket_volume)

    # VPIN = mean of imbalances over the last N buckets
    recent = bucket_imbalances[-n_buckets:]
    vpin = sum(recent) / len(recent)
    return vpin, len(recent), float(bucket_volume)


def compute_kyle_lambda(trades: list[dict], min_trades: int = 10) -> float:
    """Kyle's lambda: price impact per unit signed volume.

    Estimated by OLS-style regression of price changes on signed volume:

        ΔP_i = λ * signed_volume_i + ε_i

    where signed_volume = +V if uptick, -V if downtick. Returns the slope as
    price units per contract. Returns 0 if too few trades.
    """
    if len(trades) < min_trades:
        return 0.0

    prices = []
    signed_vols = []
    last_price = None
    for t in trades:
        p = t.get("yes_price", 0.5)
        if p > 1:
            p = p / 100.0
        v = int(t.get("count", 1))
        if last_price is not None:
            sign = 1 if p > last_price else (-1 if p < last_price else 0)
            if sign != 0:
                prices.append(p - last_price)  # ΔP
                signed_vols.append(sign * v)
        last_price = p

    if len(prices) < min_trades // 2:
        return 0.0

    # OLS slope: λ = Σ(x*y) / Σ(x²)  (no intercept since both center near 0)
    num = sum(x * y for x, y in zip(signed_vols, prices))
    den = sum(x * x for x in signed_vols)
    if den == 0:
        return 0.0
    return num / den


def order_flow_metrics(trades: list[dict]) -> OrderFlowMetrics:
    """Compute the full order-flow metric bundle for one ticker."""
    vpin, n_b, avg_bv = compute_vpin(trades)
    kyle = compute_kyle_lambda(trades)

    if vpin < 0.30:
        label = "low"
    elif vpin < 0.55:
        label = "moderate"
    else:
        label = "high"

    return OrderFlowMetrics(
        vpin=round(vpin, 4),
        kyle_lambda=round(kyle, 6),
        n_trades=len(trades),
        n_buckets=n_b,
        avg_bucket_volume=round(avg_bv, 2),
        toxicity_label=label,
    )


def is_toxic(metrics: OrderFlowMetrics, vpin_threshold: float = 0.55) -> bool:
    """True when flow toxicity is high enough to skip a new entry.

    The execution engine uses this as an entry gate: if VPIN > threshold, the
    market is being moved by likely-informed flow and we shouldn't add to it.
    """
    return metrics.vpin >= vpin_threshold
