"""
Live feature pipeline: compute ML features from WebSocket data.

Replaces the batch clean_features.csv dependency for signal generation.
For each tracked ticker, converts the MarketStateStore price history deque
into a feature DataFrame matching the exact schema that models expect.

Features computed:
  - momentum_5m/15m/1h/4h: pct_change over N bars
  - volatility_1h: rolling std of close
  - volume_1h/24h: rolling sum of volume
  - zscore: logit-space z-score (rolling 20-bar mean/std)
  - vpin: from OrderbookStore (LIVE)
  - book_pressure: from OrderbookStore (LIVE)
  - orderbook_imbalance: from OrderbookStore (LIVE)
  - hour_sin/cos, is_market_hours, minutes_to_release: time features
  - time_to_expiry_hours: from market metadata
  - lag features: momentum_5m_lag1..5, volume_1h_lag1..5
"""

import logging
import math
import os
import threading
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from engine.market_state import MarketStateStore
from engine.orderbook import OrderbookStore
from engine.feed import FeedLog

logger = logging.getLogger("kalshi.live_features")

# Minimum snapshots needed for rolling calculations
# 2 minimum to start faster; zscore uses min(20, available)
MIN_SNAPSHOTS = 2

# CSV baseline cache (loaded once), protected by lock for thread safety
_csv_history_cache: dict[str, list[dict]] | None = None
_csv_cache_lock = threading.Lock()


def _load_csv_baseline() -> dict[str, list[dict]]:
    """Load clean_features.csv once and cache, returning per-ticker history."""
    global _csv_history_cache
    with _csv_cache_lock:
        if _csv_history_cache is not None:
            return _csv_history_cache

        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        csv_path = os.path.join(project_root, "data", "clean_features.csv")

        _csv_history_cache = {}
        try:
            df = pd.read_csv(csv_path, parse_dates=["timestamp"])
            for ticker, grp in df.groupby("ticker"):
                grp = grp.sort_values("timestamp")
                points = []
                for _, row in grp.iterrows():
                    points.append({
                        "ts": row["timestamp"].isoformat(),
                        "price": float(row["close"]),
                        "yes_bid": float(row.get("yes_bid", 0) if "yes_bid" in row else 0),
                        "yes_ask": float(row.get("yes_ask", 0) if "yes_ask" in row else 0),
                        "volume": int(row.get("volume", 0)),
                    })
                _csv_history_cache[str(ticker)] = points[-200:]  # last 200 bars
            logger.info("CSV baseline loaded: %d tickers", len(_csv_history_cache))
        except Exception as e:
            logger.warning("Could not load CSV baseline: %s", e)

        return _csv_history_cache

# Economic release calendar (March 2026 — same as features.py)
RELEASES_UTC = [
    pd.Timestamp("2026-03-06 13:30", tz="UTC"),   # NFP
    pd.Timestamp("2026-03-12 13:30", tz="UTC"),   # CPI
    pd.Timestamp("2026-03-18 18:00", tz="UTC"),   # FOMC day 1
    pd.Timestamp("2026-03-19 18:00", tz="UTC"),   # FOMC day 2
    pd.Timestamp("2026-03-26 13:30", tz="UTC"),   # GDP
]


def compute_live_features(
    state: MarketStateStore,
    orderbooks: OrderbookStore,
    feed: FeedLog,
) -> pd.DataFrame:
    """
    Build feature DataFrame from live WebSocket data for all tracked tickers.

    Returns a DataFrame with one row per ticker (the latest snapshot),
    matching the exact schema that models/features.py produces.
    Tickers with insufficient history (< MIN_SNAPSHOTS) are skipped.
    """
    now = datetime.now(timezone.utc)
    all_rows = []
    skipped = []

    for ticker in state.tracked_tickers():
        market = state.get_market(ticker)
        if not market:
            continue

        # Get price history: WS snapshots + CSV baseline fallback
        ws_hist = state.get_history(ticker, limit=2000)
        if len(ws_hist) < MIN_SNAPSHOTS:
            # Merge CSV baseline with WS data
            csv_baseline = _load_csv_baseline()
            csv_hist = csv_baseline.get(ticker, [])
            hist = csv_hist + ws_hist  # CSV first, then WS (newer)
        else:
            hist = ws_hist

        if len(hist) < MIN_SNAPSHOTS:
            skipped.append(ticker)
            continue

        try:
            row = _compute_ticker_features(
                ticker, hist, market, orderbooks, state, now,
            )
            if row is not None:
                all_rows.append(row)
        except Exception as e:
            logger.warning("Live features failed for %s: %s", ticker, e)

    if skipped:
        logger.debug(
            "Skipped %d/%d tickers (< %d snapshots): %s",
            len(skipped), len(state.tracked_tickers()), MIN_SNAPSHOTS,
            skipped[:5],
        )

    if not all_rows:
        logger.warning("No tickers had enough history for live features")
        return pd.DataFrame()

    df = pd.DataFrame(all_rows)
    # Set DatetimeIndex matching batch schema
    df["timestamp"] = pd.Timestamp(now)
    df = df.set_index("timestamp")

    logger.info(
        "Live features: %d/%d tickers scored (%d skipped)",
        len(df), len(state.tracked_tickers()), len(skipped),
    )
    return df


def _compute_ticker_features(
    ticker: str,
    hist: list[dict],
    market: dict,
    orderbooks: OrderbookStore,
    state: MarketStateStore,
    now: datetime,
) -> dict | None:
    """Compute all features for a single ticker from its price history."""

    # Build DataFrame from history snapshots
    records = []
    for h in hist:
        records.append({
            "ts": pd.Timestamp(h["ts"]),
            "close": float(h["price"]),
            "yes_bid": float(h.get("yes_bid", 0)),
            "yes_ask": float(h.get("yes_ask", 0)),
            "volume": int(h.get("volume", 0)),
        })

    df = pd.DataFrame(records).sort_values("ts").reset_index(drop=True)
    n = len(df)

    if n < MIN_SNAPSHOTS:
        return None

    close = df["close"].values
    volume = df["volume"].values
    last_close = close[-1]

    if last_close <= 0:
        return None

    # ── Estimate bar interval to compute correct lookback windows ──────
    # History comes at ~30s intervals from WS ticker messages, not 5min bars.
    # We estimate the actual interval and scale our rolling windows accordingly.
    if n >= 2:
        ts_vals = df["ts"].values
        total_span_seconds = (ts_vals[-1] - ts_vals[0]) / np.timedelta64(1, "s")
        avg_interval_seconds = total_span_seconds / (n - 1) if n > 1 else 30
    else:
        avg_interval_seconds = 30

    # Bars per time period (clamped to at least 1)
    def bars_for_minutes(minutes):
        return max(1, int(round(minutes * 60 / max(avg_interval_seconds, 1))))

    bars_5m = bars_for_minutes(5)
    bars_15m = bars_for_minutes(15)
    bars_1h = bars_for_minutes(60)
    bars_4h = bars_for_minutes(240)
    bars_24h = bars_for_minutes(1440)

    # ── Momentum: logit-space returns over N bars ─────────────────────
    # Using logit-space returns instead of pct_change on bounded [0,1] prices.
    # pct_change is asymmetric on bounded prices (e.g. 0.90->0.95 = +5.6%,
    # but 0.10->0.05 = -50%). Logit-space returns are symmetric around 0.50.
    def _safe_logit(p):
        """Clamp p to (0.01, 0.99) and compute log(p/(1-p))."""
        p = max(0.01, min(0.99, p))
        return math.log(p / (1 - p))

    def logit_momentum(arr, period):
        if period >= len(arr):
            return 0.0
        old = arr[-(period + 1)]
        new = arr[-1]
        if old <= 0:
            return 0.0
        return _safe_logit(new) - _safe_logit(old)

    momentum_5m = logit_momentum(close, bars_5m)
    momentum_15m = logit_momentum(close, bars_15m)
    momentum_1h = logit_momentum(close, bars_1h)
    momentum_4h = logit_momentum(close, bars_4h)

    # ── Volatility: rolling std of close over 1h window ──────────────
    window_1h = min(bars_1h, n)
    volatility_1h = float(np.std(close[-window_1h:])) if window_1h >= 3 else 0.0

    # ── Volume: rolling sum ──────────────────────────────────────────
    # Volume in history is cumulative market volume, not per-bar incremental.
    # Compute incremental per-bar volume from differences.
    if n >= 2:
        vol_diff = np.diff(volume)
        vol_diff = np.maximum(vol_diff, 0)  # volume can't decrease
        vol_incremental = np.concatenate([[volume[0]], vol_diff])
    else:
        vol_incremental = volume.copy()

    volume_1h = float(np.sum(vol_incremental[-min(bars_1h, n):]))
    volume_24h = float(np.sum(vol_incremental[-min(bars_24h, n):]))

    # ── Logit Z-score ─────────────────────────────────────────────────
    close_clipped = np.clip(close, 0.01, 0.99)
    logit_close = np.log(close_clipped / (1 - close_clipped))

    zscore_window = min(20, n)
    if zscore_window >= 3:
        logit_recent = logit_close[-zscore_window:]
        logit_mean = float(np.mean(logit_recent))
        logit_std = float(np.std(logit_recent))
        if logit_std > 0:
            zscore = (logit_close[-1] - logit_mean) / logit_std
        else:
            zscore = 0.0
    else:
        zscore = 0.0

    # ── Orderbook features (LIVE) ─────────────────────────────────────
    ob = orderbooks.get(ticker)
    if ob and ob.has_data:
        orderbook_imbalance = ob.get_imbalance()
        book_pressure = ob.get_book_pressure()
        # VPIN from recent trades
        trades = state.get_recent_trades(ticker)
        vpin = ob.get_vpin(trades)
    else:
        orderbook_imbalance = 0.0
        book_pressure = 0.0
        vpin = 0.0

    # ── Spread ────────────────────────────────────────────────────────
    yes_bid = df["yes_bid"].iloc[-1]
    yes_ask = df["yes_ask"].iloc[-1]
    spread = yes_ask - yes_bid if (yes_ask > 0 and yes_bid > 0) else 0.0
    spread_pct = spread / last_close if last_close > 0 else 0.0

    # ── Time features ─────────────────────────────────────────────────
    hour = now.hour + now.minute / 60.0
    hour_sin = float(np.sin(2 * np.pi * hour / 24.0))
    hour_cos = float(np.cos(2 * np.pi * hour / 24.0))
    is_market_hours = 1.0 if 14.5 <= hour < 21.0 else 0.0  # 9:30-16:00 ET = 14:30-21:00 UTC

    # Minutes to next economic release
    now_ts = pd.Timestamp(now).tz_localize("UTC") if pd.Timestamp(now).tzinfo is None else pd.Timestamp(now)
    future_releases = [r for r in RELEASES_UTC if r > now_ts]
    if future_releases:
        minutes_to_release = min((future_releases[0] - now_ts).total_seconds() / 60.0, 9999.0)
    else:
        minutes_to_release = 9999.0

    # ── Time to expiry ────────────────────────────────────────────────
    exp_str = market.get("expiration_time", "")
    if exp_str:
        try:
            exp_time = pd.Timestamp(exp_str)
            if exp_time.tzinfo is None:
                exp_time = exp_time.tz_localize("UTC")
            time_to_expiry_hours = max((exp_time - now_ts).total_seconds() / 3600, 0)
        except Exception:
            time_to_expiry_hours = 9999.0
    else:
        time_to_expiry_hours = 9999.0

    # ── Lag features ──────────────────────────────────────────────────
    # momentum_5m lags: logit-space return at t-1, t-2, ..., t-5 bars
    def lagged_logit_momentum(arr, period, lag_offset):
        """Logit-space momentum evaluated lag_offset bars before the end."""
        idx_new = len(arr) - 1 - lag_offset
        idx_old = idx_new - period
        if idx_old < 0 or idx_new < 0:
            return 0.0
        old_val = arr[idx_old]
        if old_val <= 0:
            return 0.0
        return _safe_logit(arr[idx_new]) - _safe_logit(old_val)

    momentum_5m_lags = {}
    volume_1h_lags = {}
    for lag in range(1, 6):
        offset = lag * bars_5m  # each lag is ~5min back
        momentum_5m_lags[f"momentum_5m_lag{lag}"] = lagged_logit_momentum(close, bars_5m, offset)

        # Volume lag: rolling sum ending lag*bars_5m bars ago
        vol_end = max(0, n - offset)
        vol_start = max(0, vol_end - bars_1h)
        if vol_end > vol_start:
            volume_1h_lags[f"volume_1h_lag{lag}"] = float(np.sum(vol_incremental[vol_start:vol_end]))
        else:
            volume_1h_lags[f"volume_1h_lag{lag}"] = 0.0

    # ── Build output row ─────────────────────────────────────────────
    row = {
        # Metadata
        "ticker": ticker,
        "category": market.get("category", ""),
        "regime": "",  # will be set by regime detector
        # OHLCV (last bar)
        "open": float(close[-min(bars_5m, n)]) if n > 0 else last_close,
        "high": float(np.max(close[-min(bars_5m, n):])),
        "low": float(np.min(close[-min(bars_5m, n):])),
        "close": last_close,
        "volume": int(volume[-1]),
        # Derived
        "mid_price": last_close,
        "spread": round(spread, 6),
        "spread_pct": round(spread_pct, 6),
        "bid_depth_5c": 0,  # not stored per-bar in live mode
        "ask_depth_5c": 0,
        "orderbook_imbalance": round(orderbook_imbalance, 4),
        # Rolling
        "volume_1h": round(volume_1h, 1),
        "volume_24h": round(volume_24h, 1),
        "momentum_5m": round(momentum_5m, 6),
        "momentum_15m": round(momentum_15m, 6),
        "momentum_1h": round(momentum_1h, 6),
        "momentum_4h": round(momentum_4h, 6),
        "volatility_1h": round(volatility_1h, 6),
        "time_to_expiry_hours": round(time_to_expiry_hours, 2),
        "zscore": round(zscore, 4),
        "outlier_flag": 0,
        "data_gap": 0,
        # Time patterns
        "hour_sin": round(hour_sin, 4),
        "hour_cos": round(hour_cos, 4),
        "is_market_hours": is_market_hours,
        "minutes_to_release": round(minutes_to_release, 1),
        # Microstructure (LIVE — non-zero now!)
        "vpin": round(vpin, 4),
        "book_pressure": round(book_pressure, 4),
        # Time features (for prepare_ml_data compatibility)
        "hour_of_day": now.hour,
        "day_of_week": now.weekday(),
    }
    # Add lag features
    row.update(momentum_5m_lags)
    row.update(volume_1h_lags)

    return row
