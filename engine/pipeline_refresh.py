"""
Pipeline auto-refresh: re-fetches market universe and re-runs
liquidity filter + statistical quality on a schedule.

Called from the scheduler's refit loop (every 1h) or on-demand via API.
Light mode (every 30min): re-fetch market universe + liquidity filter only.
Full mode (every 4h): full statistical quality re-scoring.
"""

import logging
import os
import sys
import time
import json
import pandas as pd
from datetime import datetime, timezone

logger = logging.getLogger("kalshi.pipeline")

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(PROJECT_ROOT, "data")


def refresh_market_universe(kalshi_client) -> int:
    """Fetch all open markets from Kalshi and save to market_universe.csv.
    Returns number of markets fetched.
    """
    logger.info("Refreshing market universe from Kalshi API...")
    try:
        markets = kalshi_client.get_all_markets(limit=200)
    except Exception as e:
        logger.error("Failed to fetch markets: %s", e)
        return 0

    if not markets:
        logger.warning("No markets returned from API")
        return 0

    rows = []
    for m in markets:
        # Kalshi API v2 uses _dollars suffix (decimal strings) or _fp suffix
        # Convert to cents (integer) for compatibility with existing pipeline
        def to_cents(val):
            """Convert dollar string/float to cents integer."""
            if isinstance(val, str):
                try:
                    return int(float(val) * 100)
                except (ValueError, TypeError):
                    return 0
            if isinstance(val, (int, float)):
                return int(val * 100) if val < 10 else int(val)  # already cents if > 10
            return 0

        def to_int(val):
            """Convert fp/string to integer."""
            if isinstance(val, str):
                try:
                    return int(float(val))
                except (ValueError, TypeError):
                    return 0
            return int(val) if val else 0

        # Try both old (cents) and new (dollars) field names
        yes_bid = to_cents(m.get("yes_bid_dollars", m.get("yes_bid", 0)))
        yes_ask = to_cents(m.get("yes_ask_dollars", m.get("yes_ask", 0)))
        no_bid = to_cents(m.get("no_bid_dollars", m.get("no_bid", 0)))
        no_ask = to_cents(m.get("no_ask_dollars", m.get("no_ask", 0)))
        volume = to_int(m.get("volume_fp", m.get("volume", 0)))
        volume_24h = to_int(m.get("volume_24h_fp", m.get("volume_24h", 0)))
        open_interest = to_int(m.get("open_interest_fp", m.get("open_interest", 0)))
        last_price = to_cents(m.get("last_price_dollars", m.get("last_price", 0)))
        prev_price = to_cents(m.get("previous_price_dollars", m.get("previous_price", 0)))

        spread = (yes_ask - yes_bid) if yes_ask > 0 and yes_bid > 0 else 999

        # Category might be on the event, not the market
        # Use event_ticker prefix as a rough category proxy
        ticker = m.get("ticker", "")
        category = m.get("category", "")
        if not category:
            # Infer from ticker prefix
            if "BTC" in ticker or "ETH" in ticker or "CRYPTO" in ticker:
                category = "Crypto"
            elif "FED" in ticker or "CPI" in ticker or "GDP" in ticker or "AAAG" in ticker:
                category = "Economics"
            elif "INX" in ticker or "SPY" in ticker or "NDX" in ticker:
                category = "Financials"
            elif "NBA" in ticker or "NFL" in ticker or "MLB" in ticker or "ATP" in ticker or "SPORT" in ticker:
                category = "Sports"
            elif "ELECT" in ticker or "PRES" in ticker or "GOV" in ticker:
                category = "Elections"
            else:
                category = "Other"

        rows.append({
            "ticker": ticker,
            "title": m.get("title", m.get("yes_sub_title", "")),
            "event_ticker": m.get("event_ticker", ""),
            "series_ticker": m.get("series_ticker", m.get("mve_collection_ticker", "")),
            "category": category,
            "status": m.get("status", ""),
            "market_type": m.get("market_type", ""),
            "yes_bid": yes_bid,
            "yes_ask": yes_ask,
            "no_bid": no_bid,
            "no_ask": no_ask,
            "spread": spread,
            "last_price": last_price,
            "previous_price": prev_price,
            "volume": volume,
            "volume_24h": volume_24h,
            "open_interest": open_interest,
            "liquidity": to_int(m.get("liquidity_dollars", 0)),
            "close_time": m.get("close_time", ""),
            "expiration_time": m.get("expiration_time", ""),
            "expected_expiration_time": m.get("expected_expiration_time", ""),
            "open_time": m.get("open_time", ""),
        })

    df = pd.DataFrame(rows)
    # Keep active markets (API v2 uses "active", older used "open")
    if "status" in df.columns:
        df = df[df["status"].isin(["open", "active"])]

    path = os.path.join(DATA_DIR, "market_universe.csv")
    os.makedirs(DATA_DIR, exist_ok=True)
    df.to_csv(path, index=False)
    logger.info("Saved %d active markets to %s", len(df), path)
    return len(df)


def refresh_liquidity_filter(kalshi_client) -> int:
    """Run liquidity filter on current market_universe.csv.
    Filters inline using the fresh data — doesn't need orderbook fetches.
    Returns number of tradeable markets.
    """
    from datetime import timedelta

    path = os.path.join(DATA_DIR, "market_universe.csv")
    if not os.path.exists(path):
        return 0

    df = pd.read_csv(path)
    n_start = len(df)
    now = datetime.now(timezone.utc)

    # Filter 1: Volume >= 50 (lowered from 100 — Kalshi is illiquid)
    df = df[df["volume"] >= 50].copy()
    logger.info("Volume filter: %d -> %d", n_start, len(df))

    # Filter 2: Open interest >= 20
    df = df[df["open_interest"] >= 20].copy()
    logger.info("OI filter: -> %d", len(df))

    # Filter 3: Expiry 2h to 90 days
    df["exp_dt"] = pd.to_datetime(df["expiration_time"], errors="coerce", utc=True)
    df["hours_to_exp"] = (df["exp_dt"] - now).dt.total_seconds() / 3600
    df = df[(df["hours_to_exp"] > 2) & (df["hours_to_exp"] < 90 * 24)].copy()
    logger.info("Expiry filter: -> %d", len(df))

    # Filter 4: Activity filter — must have a last trade price OR two-sided quotes
    # Many Kalshi markets have no resting quotes but trade actively
    has_quotes = (df["spread"] <= 15) & (df["spread"] > 0)
    has_activity = df["last_price"] > 0
    df = df[has_quotes | has_activity].copy()
    # Compute spread from last_price when no quotes available
    df.loc[df["spread"] >= 999, "spread"] = 5  # default 5c for active markets without resting quotes
    logger.info("Activity filter: -> %d", len(df))

    # Filter 5: Event dedup — max 3 per event
    if "event_ticker" in df.columns and len(df) > 0:
        deduped = []
        for _, grp in df.groupby("event_ticker"):
            if len(grp) > 3:
                deduped.append(grp.nlargest(3, "volume"))
            else:
                deduped.append(grp)
        df = pd.concat(deduped) if deduped else df
        logger.info("Event dedup: -> %d", len(df))

    # Estimate depth from liquidity field (no live orderbook fetch needed)
    if "liquidity" in df.columns:
        df["depth_dollars"] = df["liquidity"]
    else:
        df["depth_dollars"] = 0

    # ── Merge with existing scored markets to preserve known-good single-event markets ──
    existing_scored_path = os.path.join(DATA_DIR, "scored_markets.csv")
    if os.path.exists(existing_scored_path):
        try:
            existing = pd.read_csv(existing_scored_path)
            existing_tickers = set(existing["ticker"])
            new_tickers = set(df["ticker"])
            # Keep existing markets that aren't in the new data (they may have expired)
            # but DO keep existing markets that are still valid single-event markets
            existing_good = existing[
                ~existing["ticker"].str.startswith("KXMVE")  # not MVE parlays
            ]
            if len(existing_good) > 0:
                # Only add back if not already in new data
                to_add = existing_good[~existing_good["ticker"].isin(new_tickers)]
                if len(to_add) > 0:
                    # Ensure column compatibility
                    for col in df.columns:
                        if col not in to_add.columns:
                            to_add[col] = 0
                    df = pd.concat([df, to_add[df.columns]], ignore_index=True)
                    logger.info("Preserved %d existing single-event markets", len(to_add))
        except Exception as e:
            logger.debug("Could not merge existing scored markets: %s", e)

    # Save
    df = df.sort_values("volume", ascending=False)
    out_path = os.path.join(DATA_DIR, "tradeable_markets.csv")
    df.to_csv(out_path, index=False)
    logger.info("Saved %d tradeable markets", len(df))
    return len(df)


def refresh_statistical_quality(kalshi_client) -> int:
    """Score tradeable markets using simplified statistical tests.
    Inline version — doesn't fetch trade history (too slow for refresh).
    Uses available CSV data for scoring.
    Returns number of scored markets.
    """
    import numpy as np

    path = os.path.join(DATA_DIR, "tradeable_markets.csv")
    if not os.path.exists(path):
        return 0

    df = pd.read_csv(path)
    if df.empty:
        return 0

    results = []
    for _, row in df.iterrows():
        vol = row.get("volume", 0)
        spread = row.get("spread", 10)
        depth = row.get("depth_dollars", 0)
        oi = row.get("open_interest", 0)

        # Simplified scoring without fetching trade history
        vol_sc = min(vol / 50000 * 100, 100)
        spread_sc = max(0, 100 - spread * 7)  # tighter spread = better
        depth_sc = min(depth / 5000 * 100, 100) if depth > 0 else 50  # default 50 if no depth data
        oi_sc = min(oi / 1000 * 100, 100)

        # Check if this market has an external model
        has_model = False
        try:
            from data.external_feeds import feed_manager
            ext = feed_manager.get_probability_for_ticker(row["ticker"], 0.5, 168)
            has_model = ext is not None and not ext.get("stale", True)
        except Exception:
            pass

        # Combined score: spread + volume + OI
        total_score = 0.25 * spread_sc + 0.25 * vol_sc + 0.25 * oi_sc + 0.25 * depth_sc

        # Boost markets with external models — significant advantage
        if has_model:
            total_score += 25

        # Infer regime from price level
        yes_bid = row.get("yes_bid", 50)
        yes_ask = row.get("yes_ask", 50)
        mid = (yes_bid + yes_ask) / 2.0 if yes_bid > 0 and yes_ask > 0 else 50
        mid_prob = mid / 100.0

        hours_left = row.get("hours_to_exp", 999)
        if hours_left < 48:
            regime = "CONVERGENCE"
        elif mid_prob < 0.15 or mid_prob > 0.85:
            regime = "MEAN_REVERTING"
        elif vol > 10000:
            regime = "TRENDING"
        else:
            regime = "MEAN_REVERTING"

        results.append({
            "ticker": row["ticker"],
            "title": row.get("title", ""),
            "category": row.get("category", ""),
            "volume": vol,
            "open_interest": oi,
            "spread": spread,
            "depth_dollars": depth,
            "n_trades": 0,
            "timespan_hours": 0,
            "variance_score": 50,
            "adf_score": 50,
            "regime": regime,
            "hurst_H": 0.5,
            "hurst_score": 50,
            "autocorr_score": 50,
            "volume_score": round(vol_sc, 1),
            "spread_score": round(spread_sc, 1),
            "depth_score": round(depth_sc, 1),
            "tradability_score": round(total_score, 1),
            "has_external_model": has_model,
        })

    res_df = pd.DataFrame(results).sort_values("tradability_score", ascending=False)

    # Filter to score >= 25 (lenient for live refresh)
    scored = res_df[res_df["tradability_score"] >= 25].copy()
    scored_path = os.path.join(DATA_DIR, "scored_markets.csv")
    scored.to_csv(scored_path, index=False)
    logger.info("Saved %d scored markets (of %d tradeable)", len(scored), len(res_df))
    return len(scored)


def light_refresh(kalshi_client) -> dict:
    """Quick refresh: re-fetch universe + liquidity filter only (~2 min).
    Suitable for every 30 minutes. Skips universe fetch if data is fresh.
    """
    start = time.time()
    # Skip universe fetch if file is < 20 min old
    universe_path = os.path.join(DATA_DIR, "market_universe.csv")
    universe_fresh = False
    if os.path.exists(universe_path):
        age = time.time() - os.path.getmtime(universe_path)
        if age < 1200:  # 20 min
            n_universe = len(pd.read_csv(universe_path))
            universe_fresh = True
            logger.info("Universe file is fresh (%ds old), skipping fetch", int(age))
        else:
            n_universe = refresh_market_universe(kalshi_client)
    else:
        n_universe = refresh_market_universe(kalshi_client)
    n_tradeable = refresh_liquidity_filter(kalshi_client) if n_universe > 0 else 0
    elapsed = time.time() - start

    result = {
        "mode": "light",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "universe_size": n_universe,
        "tradeable_count": n_tradeable,
        "elapsed_seconds": round(elapsed, 1),
    }
    logger.info("Light refresh complete: %d universe -> %d tradeable (%.1fs)",
                n_universe, n_tradeable, elapsed)
    return result


def full_refresh(kalshi_client) -> dict:
    """Full refresh: universe + liquidity + statistical scoring (~10 min).
    Suitable for every 4 hours.
    """
    start = time.time()
    n_universe = refresh_market_universe(kalshi_client)
    n_tradeable = refresh_liquidity_filter(kalshi_client) if n_universe > 0 else 0
    n_scored = refresh_statistical_quality(kalshi_client) if n_tradeable > 0 else 0
    elapsed = time.time() - start

    result = {
        "mode": "full",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "universe_size": n_universe,
        "tradeable_count": n_tradeable,
        "scored_count": n_scored,
        "elapsed_seconds": round(elapsed, 1),
    }
    logger.info("Full refresh complete: %d universe -> %d tradeable -> %d scored (%.1fs)",
                n_universe, n_tradeable, n_scored, elapsed)

    # Save refresh metadata
    meta_path = os.path.join(DATA_DIR, "last_refresh.json")
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(meta_path, "w") as f:
        json.dump(result, f, indent=2)

    return result


def get_last_refresh() -> dict:
    """Get metadata about the last pipeline refresh."""
    meta_path = os.path.join(DATA_DIR, "last_refresh.json")
    try:
        with open(meta_path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        # Check file timestamps as fallback
        scored_path = os.path.join(DATA_DIR, "scored_markets.csv")
        if os.path.exists(scored_path):
            mtime = os.path.getmtime(scored_path)
            return {
                "mode": "unknown",
                "timestamp": datetime.fromtimestamp(mtime, timezone.utc).isoformat(),
                "scored_count": len(pd.read_csv(scored_path)),
            }
        return {"mode": "never", "timestamp": None}
