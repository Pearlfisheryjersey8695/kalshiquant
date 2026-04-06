"""
Phase 1.2 -- Statistical Tradability Check
For each tradeable market: fetch trade history, run ADF / Hurst /
autocorrelation / variance tests, assign a 0-100 tradability score.
Output: data/scored_markets.csv (score >= 40 only).
"""

import sys, os, time, warnings
from datetime import datetime, timezone, timedelta

warnings.filterwarnings("ignore")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.kalshi_client import KalshiClient
import math
import pandas as pd
import numpy as np
import requests as req

RETRY = 3
MIN_TRADES_FOR_STATS = 10     # need at least this many trades to run tests
MIN_SCORE = 35                 # tradability threshold
MAX_TRADE_PAGES = 20           # fetch up to 2000 trades per market


# ── Helpers ──────────────────────────────────────────────────────────────

def fetch_all_trades(client, ticker, max_pages=MAX_TRADE_PAGES):
    """Paginate the trades endpoint, return list of trades (newest first)."""
    trades = []
    params = {"limit": 100, "ticker": ticker}
    for _ in range(max_pages):
        for attempt in range(RETRY):
            try:
                resp = client.get("/trade-api/v2/markets/trades", params=params)
                break
            except req.HTTPError as e:
                if attempt < RETRY - 1 and e.response is not None and e.response.status_code in (429, 502, 503):
                    time.sleep(2 ** attempt)
                else:
                    return trades
        batch = resp.get("trades", [])
        trades.extend(batch)
        cursor = resp.get("cursor")
        if not cursor or not batch:
            break
        params["cursor"] = cursor
    return trades


def trades_to_series(trades):
    """Convert trades list to a pandas Series of prices indexed by time."""
    if not trades:
        return pd.Series(dtype=float)
    rows = []
    for t in trades:
        # Support both API v1 (yes_price in cents) and v2 (yes_price_dollars)
        if "yes_price_dollars" in t:
            price = float(t["yes_price_dollars"])
        elif "yes_price" in t:
            price = t["yes_price"] / 100.0 if t["yes_price"] > 1 else float(t["yes_price"])
        else:
            continue
        rows.append({
            "time": pd.Timestamp(t["created_time"]),
            "price": price,
        })
    df = pd.DataFrame(rows).sort_values("time")
    df = df.set_index("time")
    return df["price"]


# ── Statistical Tests ────────────────────────────────────────────────────

def variance_score(prices):
    """
    Rolling variance check. More variance = more tradeable.
    Returns 0-100 score: 0 = stale (< 0.5% stdev), 100 = highly volatile.
    """
    if len(prices) < 5:
        return 0.0
    std = prices.std()
    # prediction market prices are 0-1 probabilities
    # 0.02 std = moderate, 0.05+ = highly volatile
    score = min(std / 0.05 * 100, 100)
    return round(score, 1)


def adf_score(prices):
    """
    ADF test for stationarity. Stationary = mean-reverting = tradeable.
    Returns 0-100 score and regime label.
    """
    if len(prices) < 20:
        return 0.0, "INSUFFICIENT_DATA"
    try:
        from statsmodels.tsa.stattools import adfuller
        result = adfuller(prices.values, maxlag=min(10, len(prices) // 3))
        adf_stat, p_value = result[0], result[1]
        if p_value < 0.01:
            return 90.0, "MEAN_REVERTING"
        elif p_value < 0.05:
            return 70.0, "MEAN_REVERTING"
        elif p_value < 0.10:
            return 50.0, "WEAK_MEAN_REVERT"
        else:
            return 30.0, "TRENDING"
    except Exception:
        return 0.0, "ADF_FAILED"


def hurst_exponent(prices):
    """
    Simplified Hurst exponent via R/S analysis.
    H < 0.5 = mean reverting, H = 0.5 = random walk, H > 0.5 = trending.
    Returns (H, score 0-100).
    """
    if len(prices) < 20:
        return 0.5, 0.0

    ts = prices.values
    n = len(ts)

    max_k = min(n // 2, 100)
    if max_k < 4:
        return 0.5, 0.0

    lags = range(2, max_k)
    rs = []
    for lag in lags:
        parts = [ts[i:i+lag] for i in range(0, n - lag + 1, lag)]
        if len(parts) < 1:
            continue
        rs_vals = []
        for part in parts:
            if len(part) < 2:
                continue
            mean = np.mean(part)
            devs = np.cumsum(part - mean)
            r = np.max(devs) - np.min(devs)
            s = np.std(part, ddof=1)
            if s > 0:
                rs_vals.append(r / s)
        if rs_vals:
            rs.append((lag, np.mean(rs_vals)))

    if len(rs) < 3:
        return 0.5, 0.0

    log_lags = np.log([r[0] for r in rs])
    log_rs = np.log([r[1] for r in rs])
    H = np.polyfit(log_lags, log_rs, 1)[0]
    H = np.clip(H, 0, 1)

    # Score: further from 0.5 (random walk) = more predictable = better
    distance = abs(H - 0.5)
    score = min(distance / 0.3 * 100, 100)

    return round(H, 3), round(score, 1)


def _safe_logit_series(prices):
    """Convert price series to logit space for symmetric analysis."""
    clamped = prices.clip(0.01, 0.99)
    return np.log(clamped / (1 - clamped))


def autocorr_score(prices):
    """
    Autocorrelation of RETURNS at lags 1-10. Significant autocorrelation
    in returns = exploitable serial dependence = predictable.
    NOTE: Testing price levels (not returns) is trivially significant
    for any persistent process and tells you nothing about tradability.
    Uses logit-space returns for symmetric analysis on bounded prices.
    Returns 0-100 score.
    """
    if len(prices) < 15:
        return 0.0

    # Use logit-space returns for symmetric analysis on bounded prices
    logit_prices = _safe_logit_series(prices)
    returns = logit_prices.diff().dropna()
    if len(returns) < 15:
        return 0.0

    n = len(returns)
    threshold = 2.0 / np.sqrt(n)  # 95% significance band

    sig_count = 0
    total_strength = 0.0
    for lag in range(1, min(11, n // 2)):
        ac = returns.autocorr(lag=lag)
        if pd.notna(ac):
            total_strength += abs(ac)
            if abs(ac) > threshold:
                sig_count += 1

    # More significant lags + stronger autocorrelation = better
    score = (sig_count / 10 * 50) + (min(total_strength / 2.0, 1.0) * 50)
    return round(min(score, 100), 1)


# ── Main ─────────────────────────────────────────────────────────────────

def main():
    client = KalshiClient()
    data_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")

    df = pd.read_csv(os.path.join(data_dir, "tradeable_markets.csv"))
    print(f"Loaded {len(df)} tradeable markets")

    results = []
    for i, (_, row) in enumerate(df.iterrows()):
        ticker = row["ticker"]

        # Fetch trade history
        trades = fetch_all_trades(client, ticker)
        prices = trades_to_series(trades)

        n_trades = len(trades)
        timespan_hours = 0
        if len(prices) >= 2:
            timespan_hours = (prices.index[-1] - prices.index[0]).total_seconds() / 3600

        # Run tests
        if n_trades >= MIN_TRADES_FOR_STATS:
            var_sc = variance_score(prices)
            adf_sc, regime = adf_score(prices)
            H, hurst_sc = hurst_exponent(prices)
            ac_sc = autocorr_score(prices)
        else:
            var_sc, adf_sc, hurst_sc, ac_sc = 0.0, 0.0, 0.0, 0.0
            H, regime = 0.5, "INSUFFICIENT_DATA"

        # ADF test has reduced validity for prices bounded in [0,1]
        # Near 0 or 1, prices are mechanically mean-reverting due to bounds
        # Discount stationarity finding for extreme prices
        if n_trades >= MIN_TRADES_FOR_STATS and len(prices) >= 2:
            mean_price = float(prices.mean())
            if mean_price < 0.10 or mean_price > 0.90:
                adf_sc = adf_sc * 0.25
            elif mean_price < 0.15 or mean_price > 0.85:
                adf_sc = adf_sc * 0.5

        # Weighted tradability score
        # Volume weight 25%, Spread tightness 20%, Price variance 20%,
        # Autocorrelation 20%, Orderbook depth 15%
        vol_sc = min(row.get("volume", 0) / 50000 * 100, 100)
        spread_sc = max(0, 100 - row.get("spread", 10) * 10)  # lower spread = better
        depth_sc = min(row.get("depth_dollars", 0) / 5000 * 100, 100)

        total_score = (
            0.15 * vol_sc       # was 0.25
            + 0.25 * spread_sc  # was 0.20 — tighter spread matters more
            + 0.20 * var_sc
            + 0.25 * ac_sc      # was 0.20 — predictability matters more
            + 0.15 * depth_sc
        )

        results.append({
            "ticker": ticker,
            "title": row.get("title", ""),
            "category": row.get("category", ""),
            "volume": row.get("volume", 0),
            "open_interest": row.get("open_interest", 0),
            "spread": row.get("spread", 0),
            "depth_dollars": row.get("depth_dollars", 0),
            "n_trades": n_trades,
            "timespan_hours": round(timespan_hours, 1),
            "variance_score": var_sc,
            "adf_score": adf_sc,
            "regime": regime,
            "hurst_H": H,
            "hurst_score": hurst_sc,
            "autocorr_score": ac_sc,
            "volume_score": round(vol_sc, 1),
            "spread_score": round(spread_sc, 1),
            "depth_score": round(depth_sc, 1),
            "tradability_score": round(total_score, 1),
        })

        if (i + 1) % 10 == 0:
            print(f"  {i+1}/{len(df)} scored ...")

    res_df = pd.DataFrame(results).sort_values("tradability_score", ascending=False)

    # Save all scores
    all_path = os.path.join(data_dir, "all_scored_markets.csv")
    res_df.to_csv(all_path, index=False)

    # Filter to score >= MIN_SCORE
    scored = res_df[res_df["tradability_score"] >= MIN_SCORE].copy()
    scored_path = os.path.join(data_dir, "scored_markets.csv")
    scored.to_csv(scored_path, index=False)

    # ── Summary ──────────────────────────────────────────────────────────
    print("\n" + "=" * 80)
    print("  STATISTICAL TRADABILITY RESULTS")
    print("=" * 80)
    print(f"  {len(scored)} of {len(res_df)} pass score >= {MIN_SCORE}")
    print(f"  Score range: {res_df['tradability_score'].min():.1f} - {res_df['tradability_score'].max():.1f}")

    # Regime breakdown
    print(f"\n  Regime breakdown:")
    for regime, cnt in res_df["regime"].value_counts().items():
        print(f"    {regime:<25} {cnt:>4}")

    # Top 15 by score
    print(f"\n  Top 15 by tradability score:")
    print(f"  {'Ticker':<45} {'Cat':<10} {'Score':>6} {'Regime':<20} {'Var':>5} {'ADF':>5} {'AC':>5} {'H':>5}")
    print("  " + "-" * 105)
    for _, r in res_df.head(15).iterrows():
        cat = (r["category"] or "-")[:9]
        print(f"  {r['ticker']:<45} {cat:<10} {r['tradability_score']:>6.1f} {r['regime']:<20} {r['variance_score']:>5.1f} {r['adf_score']:>5.1f} {r['autocorr_score']:>5.1f} {r['hurst_H']:>5.3f}")

    print(f"\n  Files:")
    print(f"    {scored_path}  ({len(scored)} rows, score >= {MIN_SCORE})")
    print(f"    {all_path}  ({len(res_df)} rows, all scores)")
    print("=" * 80)


if __name__ == "__main__":
    main()
