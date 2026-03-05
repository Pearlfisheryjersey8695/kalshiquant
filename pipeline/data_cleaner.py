"""
Phase 1.3 -- Data Cleaning Pipeline
For each scored market: fetch full trade + orderbook history, clean, engineer
features, output data/clean_features.parquet.
"""

import sys, os, time, warnings
from datetime import datetime, timezone, timedelta

warnings.filterwarnings("ignore")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.kalshi_client import KalshiClient
import pandas as pd
import numpy as np
import requests as req

RETRY = 3
MAX_TRADE_PAGES = 50          # up to 5000 trades per market
RESAMPLE_FREQ = "5min"        # base resolution for feature engineering


def fetch_all_trades(client, ticker):
    trades = []
    params = {"limit": 100, "ticker": ticker}
    for _ in range(MAX_TRADE_PAGES):
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


def fetch_orderbook_snapshot(client, ticker):
    for attempt in range(RETRY):
        try:
            ob = client.get_orderbook(ticker, depth=20)
            return ob.get("orderbook", ob)
        except Exception:
            if attempt < RETRY - 1:
                time.sleep(1)
    return None


def build_price_series(trades, freq=RESAMPLE_FREQ):
    """Resample trades into regular OHLCV bars."""
    if not trades:
        return pd.DataFrame()

    rows = []
    for t in trades:
        rows.append({
            "time": pd.Timestamp(t["created_time"]),
            "price": t["yes_price"] / 100.0,
            "volume": t.get("count", 0),
        })

    df = pd.DataFrame(rows).sort_values("time").set_index("time")

    ohlcv = df["price"].resample(freq).ohlc()
    ohlcv["volume"] = df["volume"].resample(freq).sum()
    ohlcv = ohlcv.dropna(subset=["open"])

    return ohlcv


def clean_and_engineer(ohlcv, market_row, orderbook):
    """
    Apply cleaning + feature engineering to one market's OHLCV data.
    Returns a DataFrame of feature snapshots.
    """
    if len(ohlcv) < 3:
        return pd.DataFrame()

    df = ohlcv.copy()

    # ── Missing data: forward-fill gaps < 5 bars, flag larger gaps ───────
    full_idx = pd.date_range(df.index.min(), df.index.max(), freq=RESAMPLE_FREQ)
    df = df.reindex(full_idx)
    gap_mask = df["close"].isna()
    gap_lengths = gap_mask.astype(int).groupby((~gap_mask).cumsum()).cumsum()
    df["data_gap"] = gap_lengths > 5

    # Forward-fill short gaps
    df = df.ffill(limit=5)
    df = df.dropna(subset=["close"])

    if len(df) < 3:
        return pd.DataFrame()

    # ── Outlier detection in logit space (flag, don't remove) ───────────
    # Prediction market prices are bounded [0,1]. Normal assumptions behind
    # z-scores and Bollinger bands break near boundaries. Logit transform
    # maps [0,1] -> (-inf, +inf) where normal assumptions hold.
    close_clipped = df["close"].clip(0.01, 0.99)
    logit_close = np.log(close_clipped / (1 - close_clipped))

    logit_mean = logit_close.rolling(20, min_periods=3).mean()
    logit_std = logit_close.rolling(20, min_periods=3).std()

    # Z-score in logit space (stays logit for ML — XGBoost uses ordinal splits)
    df["zscore"] = ((logit_close - logit_mean) / logit_std.replace(0, np.nan)).fillna(0)

    # Bollinger bands: compute in logit, convert back to probability for flagging
    logit_upper = logit_mean + 2 * logit_std
    logit_lower = logit_mean - 2 * logit_std
    boll_upper = 1 / (1 + np.exp(-logit_upper))
    boll_lower = 1 / (1 + np.exp(-logit_lower))
    df["outlier_flag"] = (df["close"] > boll_upper) | (df["close"] < boll_lower)

    # ── Core features ────────────────────────────────────────────────────
    df["mid_price"] = df["close"]  # in probability space 0-1

    # ── SNAPSHOT-ONLY features (current state, NOT historical) ────────
    # These come from a single API snapshot and MUST NOT be used as
    # historical time-series features. They are set only on the last row
    # to prevent look-ahead bias in ML training.
    yes_bid = market_row.get("yes_bid", 0) / 100.0
    yes_ask = market_row.get("yes_ask", 0) / 100.0
    spread_val = yes_ask - yes_bid if (yes_ask > 0 and yes_bid > 0) else 0

    # Orderbook depth (snapshot)
    bid_depth = 0
    ask_depth = 0
    if orderbook:
        mid_cents = df["close"].iloc[-1] * 100
        for price, qty in orderbook.get("yes", []):
            if abs(price - mid_cents) <= 5:
                if price <= mid_cents:
                    bid_depth += qty
                else:
                    ask_depth += qty
        for price, qty in orderbook.get("no", []):
            no_mid = 100 - mid_cents
            if abs(price - no_mid) <= 5:
                if price <= no_mid:
                    ask_depth += qty
                else:
                    bid_depth += qty

    total_depth = bid_depth + ask_depth
    imbalance = (bid_depth - ask_depth) / total_depth if total_depth > 0 else 0

    # Initialize as NaN (unknown for historical bars)
    df["spread"] = np.nan
    df["spread_pct"] = np.nan
    df["bid_depth_5c"] = np.nan
    df["ask_depth_5c"] = np.nan
    df["orderbook_imbalance"] = np.nan

    # Only populate the LAST row (current snapshot)
    df.loc[df.index[-1], "spread"] = spread_val
    df.loc[df.index[-1], "spread_pct"] = spread_val / df["close"].iloc[-1] if df["close"].iloc[-1] > 0 else 0
    df.loc[df.index[-1], "bid_depth_5c"] = bid_depth
    df.loc[df.index[-1], "ask_depth_5c"] = ask_depth
    df.loc[df.index[-1], "orderbook_imbalance"] = imbalance

    # Volume rolling
    df["volume_1h"] = df["volume"].rolling(12, min_periods=1).sum()     # 12 x 5min = 1h
    df["volume_24h"] = df["volume"].rolling(288, min_periods=1).sum()   # 288 x 5min = 24h

    # Price momentum
    df["momentum_5m"] = df["close"].pct_change(1)
    df["momentum_15m"] = df["close"].pct_change(3)
    df["momentum_1h"] = df["close"].pct_change(12)
    df["momentum_4h"] = df["close"].pct_change(48)

    # Volatility
    df["volatility_1h"] = df["close"].rolling(12, min_periods=3).std()

    # Time to expiry
    exp_time = pd.Timestamp(market_row.get("expiration_time", ""), tz="UTC")
    if pd.notna(exp_time):
        df["time_to_expiry_hours"] = (exp_time - df.index).total_seconds() / 3600
    else:
        df["time_to_expiry_hours"] = np.nan

    # Metadata columns
    df["ticker"] = market_row["ticker"]
    df["category"] = market_row.get("category", "")
    df["regime"] = market_row.get("regime", "")

    # Clean up
    df = df.replace([np.inf, -np.inf], np.nan)

    # Column-specific NaN handling instead of blanket fillna(0).
    # Momentum and volatility columns are NaN during the warmup period
    # (rolling windows don't have enough history). Filling with 0 tells
    # XGBoost "zero momentum" which is a real signal, not "unknown."
    # Solution: drop rows where core momentum/volatility features are NaN.

    # Volume: fillna(0) is correct — no trades = zero volume
    for col in ["volume_1h", "volume_24h", "volume"]:
        if col in df.columns:
            df[col] = df[col].fillna(0)

    # Z-score and outlier_flag: fillna(0) is fine (already handled above, but safety)
    for col in ["zscore", "outlier_flag", "data_gap"]:
        if col in df.columns:
            df[col] = df[col].fillna(0)

    # Snapshot-only columns: NaN for historical rows is correct, fill with 0
    for col in ["spread", "spread_pct", "bid_depth_5c", "ask_depth_5c", "orderbook_imbalance"]:
        if col in df.columns:
            df[col] = df[col].fillna(0)

    # Metadata columns
    for col in ["ticker", "category", "regime"]:
        if col in df.columns:
            df[col] = df[col].fillna("")

    # time_to_expiry_hours: fillna with large number (far from expiry)
    if "time_to_expiry_hours" in df.columns:
        df["time_to_expiry_hours"] = df["time_to_expiry_hours"].fillna(9999)

    # OHLC: fillna(0) is acceptable (already forward-filled above)
    for col in ["open", "high", "low", "close", "mid_price"]:
        if col in df.columns:
            df[col] = df[col].fillna(0)

    # Momentum and volatility: DROP rows where these are NaN (warmup period)
    warmup_cols = ["momentum_5m", "momentum_15m", "momentum_1h", "momentum_4h", "volatility_1h"]
    warmup_present = [c for c in warmup_cols if c in df.columns]
    if warmup_present:
        before = len(df)
        df = df.dropna(subset=warmup_present)
        dropped = before - len(df)
        if dropped > 0:
            pass  # warmup rows silently dropped

    return df


def main():
    client = KalshiClient()
    data_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")

    scored = pd.read_csv(os.path.join(data_dir, "scored_markets.csv"))
    print(f"Loaded {len(scored)} scored markets")

    all_features = []
    for i, (_, row) in enumerate(scored.iterrows()):
        ticker = row["ticker"]

        # Fetch data
        trades = fetch_all_trades(client, ticker)
        orderbook = fetch_orderbook_snapshot(client, ticker)
        ohlcv = build_price_series(trades)

        if len(ohlcv) < 3:
            print(f"  {ticker}: skipped (only {len(ohlcv)} bars)")
            continue

        features = clean_and_engineer(ohlcv, row, orderbook)
        if len(features) > 0:
            all_features.append(features)
            print(f"  {ticker}: {len(features)} bars, {len(trades)} trades")
        else:
            print(f"  {ticker}: skipped (insufficient data after cleaning)")

    if not all_features:
        print("No features generated!")
        return

    combined = pd.concat(all_features)
    combined.index.name = "timestamp"

    # Select output columns
    feature_cols = [
        "ticker", "category", "regime",
        "open", "high", "low", "close", "volume",
        "mid_price", "spread", "spread_pct",
        "bid_depth_5c", "ask_depth_5c", "orderbook_imbalance",
        "volume_1h", "volume_24h",
        "momentum_5m", "momentum_15m", "momentum_1h", "momentum_4h",
        "volatility_1h", "time_to_expiry_hours",
        "zscore", "outlier_flag", "data_gap",
    ]
    out_cols = [c for c in feature_cols if c in combined.columns]
    output = combined[out_cols]

    parquet_path = os.path.join(data_dir, "clean_features.parquet")
    output.to_parquet(parquet_path)

    # Also save CSV for inspection
    csv_path = os.path.join(data_dir, "clean_features.csv")
    output.to_csv(csv_path)

    # ── Summary ──────────────────────────────────────────────────────────
    n_markets = output["ticker"].nunique()
    print("\n" + "=" * 72)
    print("  DATA CLEANING PIPELINE RESULTS")
    print("=" * 72)
    print(f"  Markets processed:    {n_markets}")
    print(f"  Total feature rows:   {len(output):,}")
    print(f"  Time range:           {output.index.min()} -> {output.index.max()}")
    print(f"  Feature columns:      {len(out_cols)}")

    print(f"\n  Per-market row counts:")
    for ticker, grp in output.groupby("ticker"):
        span = (grp.index.max() - grp.index.min()).total_seconds() / 3600
        print(f"    {ticker:<45} {len(grp):>5} rows  ({span:.1f}h span)")

    print(f"\n  Feature statistics (last snapshot per market):")
    last = output.groupby("ticker").last()
    num_cols = ["spread", "orderbook_imbalance", "volatility_1h", "momentum_1h"]
    for col in num_cols:
        if col in last.columns:
            vals = pd.to_numeric(last[col], errors="coerce")
            print(f"    {col:<25} mean={vals.mean():>8.4f}  std={vals.std():>8.4f}")

    print(f"\n  Output:")
    print(f"    {parquet_path}")
    print(f"    {csv_path}")
    print("=" * 72)


if __name__ == "__main__":
    main()
