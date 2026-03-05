"""
Feature pipeline -- loads clean_features.parquet and prepares ML-ready
feature matrices per market.
"""

import os
import pandas as pd
import numpy as np


DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")

# Feature columns used by ML models
# NOTE: orderbook_imbalance, spread, mid_price EXCLUDED -- these are
# point-in-time snapshots that cannot be reconstructed historically.
# Using them would introduce look-ahead bias (single current snapshot
# broadcast across all historical bars).
FEATURE_COLS = [
    "volume_1h",
    "momentum_5m", "momentum_15m", "momentum_1h", "momentum_4h",
    "volatility_1h",
    "time_to_expiry_hours",
    "zscore",
    # Time-of-day patterns (cyclical encoding + market hours)
    "hour_sin", "hour_cos", "is_market_hours", "minutes_to_release",
    # Microstructure (live-only, 0 for historical batch)
    "vpin", "book_pressure",
]

# Lag features to generate (only time-series features, no snapshots)
LAG_COLS = ["momentum_5m", "volume_1h"]
N_LAGS = 5


def load_features(path=None):
    """Load the clean features (parquet preferred, CSV fallback)."""
    parquet_path = path or os.path.join(DATA_DIR, "clean_features.parquet")
    csv_path = os.path.join(DATA_DIR, "clean_features.csv")
    try:
        return pd.read_parquet(parquet_path)
    except Exception:
        df = pd.read_csv(csv_path, index_col=0, parse_dates=True)
        return df


def add_lag_features(df, cols=LAG_COLS, n_lags=N_LAGS):
    """Add lagged versions of key columns per ticker."""
    result = df.copy()
    for col in cols:
        if col not in result.columns:
            continue
        for lag in range(1, n_lags + 1):
            result[f"{col}_lag{lag}"] = result.groupby("ticker")[col].shift(lag)
    return result


def add_time_features(df):
    """Add hour_of_day and day_of_week from the index."""
    result = df.copy()
    if hasattr(result.index, "hour"):
        result["hour_of_day"] = result.index.hour
        result["day_of_week"] = result.index.dayofweek
    return result


def add_time_pattern_features(df):
    """Cyclical time encoding, market hours flag, economic release proximity."""
    result = df.copy()
    if hasattr(result.index, "hour"):
        hour = result.index.hour + result.index.minute / 60.0
        result["hour_sin"] = np.sin(2 * np.pi * hour / 24.0)
        result["hour_cos"] = np.cos(2 * np.pi * hour / 24.0)
        # US market hours 9:30-16:00 ET = 14:30-21:00 UTC
        utc_hour = hour
        result["is_market_hours"] = ((utc_hour >= 14.5) & (utc_hour < 21.0)).astype(float)
    else:
        result["hour_sin"] = 0.0
        result["hour_cos"] = 0.0
        result["is_market_hours"] = 0.0

    # Economic release calendar (March 2026 — hardcoded for hackathon)
    RELEASES_UTC = [
        pd.Timestamp("2026-03-06 13:30", tz="UTC"),   # NFP
        pd.Timestamp("2026-03-12 13:30", tz="UTC"),   # CPI
        pd.Timestamp("2026-03-18 18:00", tz="UTC"),   # FOMC day 1
        pd.Timestamp("2026-03-19 18:00", tz="UTC"),   # FOMC day 2
        pd.Timestamp("2026-03-26 13:30", tz="UTC"),   # GDP
    ]

    result["minutes_to_release"] = 9999.0
    if hasattr(result.index, "tz") or hasattr(result.index, "tz_localize"):
        try:
            idx = result.index
            if idx.tz is None:
                idx = idx.tz_localize("UTC")
            for i, ts in enumerate(idx):
                future = [r for r in RELEASES_UTC if r > ts]
                if future:
                    mins = (future[0] - ts).total_seconds() / 60.0
                    result.iloc[i, result.columns.get_loc("minutes_to_release")] = min(mins, 9999.0)
        except Exception:
            pass  # Non-datetime index, leave as 9999

    # Microstructure features — set to 0 for batch (no live orderbook)
    if "vpin" not in result.columns:
        result["vpin"] = 0.0
    if "book_pressure" not in result.columns:
        result["book_pressure"] = 0.0

    return result


def add_target(df, horizon=12, threshold=0.02):
    """
    Create classification target: price direction over next `horizon` bars.
    +1 = up > threshold, -1 = down > threshold, 0 = flat.
    horizon=12 at 5min bars = 1 hour.
    threshold=0.02 means 2 cents absolute (not percentage).

    Absolute thresholds are correct for prediction markets where prices
    are [0,1] probabilities: a 2-cent move is equally meaningful whether
    the price is 0.04 or 0.90.
    """
    result = df.copy()
    future_price = result.groupby("ticker")["close"].shift(-horizon)
    abs_change = future_price - result["close"]
    result["target"] = 0
    result.loc[abs_change > threshold, "target"] = 1
    result.loc[abs_change < -threshold, "target"] = -1
    result["future_return"] = abs_change
    return result


def prepare_ml_data(df=None, add_targets=True):
    """Full pipeline: load -> lag -> time -> time_patterns -> target -> drop NaN."""
    if df is None:
        df = load_features()
    df = add_lag_features(df)
    df = add_time_features(df)
    df = add_time_pattern_features(df)
    if add_targets:
        df = add_target(df)
    df = df.replace([np.inf, -np.inf], np.nan)
    return df


def get_feature_matrix(df, ticker=None):
    """Return X, y arrays for a specific ticker or all data."""
    if ticker:
        df = df[df["ticker"] == ticker]

    # Reset index to avoid duplicate-timestamp issues across tickers
    df = df.reset_index(drop=False)

    all_feature_cols = FEATURE_COLS.copy()
    # Add lag columns
    for col in LAG_COLS:
        for lag in range(1, N_LAGS + 1):
            c = f"{col}_lag{lag}"
            if c in df.columns:
                all_feature_cols.append(c)
    # Add time features
    for c in ["hour_of_day", "day_of_week"]:
        if c in df.columns:
            all_feature_cols.append(c)

    available = [c for c in all_feature_cols if c in df.columns]
    valid_mask = df[available].notna().all(axis=1)
    X = df.loc[valid_mask, available]
    y = df.loc[valid_mask, "target"] if "target" in df.columns else None

    return X, y, available
