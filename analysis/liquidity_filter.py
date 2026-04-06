"""
Phase 1.1 — Liquidity & Volume Filter
Reads data/market_universe.csv, applies 5 filters, fetches live orderbook
depth for survivors, outputs tradeable/rejected CSVs + liquidity report.
"""

import sys, os, json, time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.kalshi_client import KalshiClient
import pandas as pd
import requests as req

RETRY = 3
# ── Filter thresholds ────────────────────────────────────────────────────
MIN_VOLUME = 100
MAX_SPREAD_CENTS = 15
MIN_DEPTH_DOLLARS = 200      # total $ within 5c of mid, both sides
MIN_OPEN_INTEREST = 50
MIN_HOURS_TO_EXP = 2
MAX_HOURS_TO_EXP = 90 * 24   # 90 days


def fetch_orderbook(client, ticker, depth=20):
    """Fetch orderbook with retry, return (yes_bids, yes_asks) or None."""
    for attempt in range(RETRY):
        try:
            ob = client.get_orderbook(ticker, depth=depth)
            return ob.get("orderbook", ob)
        except req.HTTPError as e:
            if attempt < RETRY - 1 and e.response is not None and e.response.status_code in (429, 502, 503):
                time.sleep(2 ** attempt)
            else:
                return None
        except Exception:
            return None
    return None


def calc_depth(orderbook, mid_price, band_cents=5):
    """
    Calculate depth within band_cents of mid, requiring BOTH sides.
    Orderbook format: {"yes": [[price, qty], ...], "no": [[price, qty], ...]}
    Prices are in cents, qty in contracts. Each contract is $1 notional.
    No-side prices are in complementary space (100 - yes_price).
    Returns (total_depth, yes_depth, no_depth).
    """
    yes_depth = 0.0
    no_depth = 0.0

    # Yes side: compare directly against yes mid
    for price, qty in orderbook.get("yes", []):
        if abs(price - mid_price) <= band_cents:
            yes_depth += qty * (price / 100.0)

    # No side: prices are in complementary space, so compare
    # against no_mid = 100 - yes_mid
    no_mid = 100 - mid_price
    for price, qty in orderbook.get("no", []):
        if abs(price - no_mid) <= band_cents:
            no_depth += qty * (price / 100.0)

    return yes_depth + no_depth, yes_depth, no_depth


def main():
    client = KalshiClient()
    data_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
    now = datetime.now(timezone.utc)

    # ── Load universe ────────────────────────────────────────────────────
    df = pd.read_csv(os.path.join(data_dir, "market_universe.csv"))
    total_start = len(df)
    print(f"Loaded {total_start:,} markets from market_universe.csv")

    # Track rejection reasons per market
    rejections = {}  # ticker -> [reason, ...]

    def reject(mask, reason):
        for t in df.loc[mask, "ticker"]:
            rejections.setdefault(t, []).append(reason)

    # ── Filter 1: Volume ─────────────────────────────────────────────────
    low_vol = df["volume"] < MIN_VOLUME
    reject(low_vol, f"volume < {MIN_VOLUME}")
    df = df[~low_vol].copy()
    after_vol = len(df)
    print(f"[1] Volume >= {MIN_VOLUME}: {after_vol:,} remain ({total_start - after_vol:,} cut)")

    # ── Filter 2: Open Interest ──────────────────────────────────────────
    low_oi = df["open_interest"] < MIN_OPEN_INTEREST
    reject(low_oi, f"open_interest < {MIN_OPEN_INTEREST}")
    df = df[~low_oi].copy()
    after_oi = len(df)
    print(f"[2] OI >= {MIN_OPEN_INTEREST}: {after_oi:,} remain ({after_vol - after_oi:,} cut)")

    # ── Filter 3: Time to Expiry ─────────────────────────────────────────
    df["exp_dt"] = pd.to_datetime(df["expiration_time"], errors="coerce", utc=True)
    df["hours_to_exp"] = (df["exp_dt"] - now).dt.total_seconds() / 3600

    too_soon = df["hours_to_exp"] <= MIN_HOURS_TO_EXP
    too_far = df["hours_to_exp"] > MAX_HOURS_TO_EXP
    bad_time = too_soon | too_far
    reject(too_soon, f"expires within {MIN_HOURS_TO_EXP}h")
    reject(too_far, f"expires > {MAX_HOURS_TO_EXP // 24}d out")
    df = df[~bad_time].copy()
    after_time = len(df)
    print(f"[3] Expiry {MIN_HOURS_TO_EXP}h-{MAX_HOURS_TO_EXP//24}d: {after_time:,} remain ({after_oi - after_time:,} cut)")

    # ── Filter 4: Spread (snapshot) ──────────────────────────────────────
    # Markets need two-sided quotes. No quote = untradeable.
    no_quote = (df["yes_bid"] <= 0) | (df["yes_ask"] <= 0)
    wide_spread = df["spread"].fillna(999) > MAX_SPREAD_CENTS
    bad_spread = no_quote | wide_spread
    reject(no_quote & ~wide_spread, "no two-sided quote")
    reject(wide_spread & ~no_quote, f"spread > {MAX_SPREAD_CENTS}c")
    reject(no_quote & wide_spread, "no two-sided quote")
    df = df[~bad_spread].copy()
    after_spread = len(df)
    print(f"[4] Spread <= {MAX_SPREAD_CENTS}c: {after_spread:,} remain ({after_time - after_spread:,} cut)")

    # ── Filter 5: Live Orderbook Depth ───────────────────────────────────
    print(f"[5] Fetching live orderbooks for {len(df)} markets ...")
    depths = []
    for i, (_, row) in enumerate(df.iterrows()):
        ticker = row["ticker"]
        ob = fetch_orderbook(client, ticker)
        if ob is None:
            depths.append(0.0)
            continue

        mid = (row["yes_bid"] + row["yes_ask"]) / 2.0
        total_d, yes_d, no_d = calc_depth(ob, mid, band_cents=5)
        depths.append(total_d)

        if (i + 1) % 25 == 0:
            print(f"    {i+1}/{len(df)} fetched ...")

    df["depth_dollars"] = depths
    thin = df["depth_dollars"] < MIN_DEPTH_DOLLARS
    reject(thin, f"orderbook depth < ${MIN_DEPTH_DOLLARS}")
    passed = df[~thin].copy()
    failed_depth = df[thin].copy()
    print(f"  Depth >= ${MIN_DEPTH_DOLLARS}: {len(passed):,} remain ({len(failed_depth):,} cut)")

    # ── Event deduplication: max 3 markets per event ────────────────
    MAX_PER_EVENT = 3
    if "event_ticker" in passed.columns:
        deduped = []
        for event, grp in passed.groupby("event_ticker"):
            if len(grp) > MAX_PER_EVENT:
                deduped.append(grp.nlargest(MAX_PER_EVENT, "volume"))
            else:
                deduped.append(grp)
        if deduped:
            passed = pd.concat(deduped)
        print(f"  Event dedup (max {MAX_PER_EVENT}/event): {len(passed)} remain")

    # ── Outputs ──────────────────────────────────────────────────────────
    # 1. Tradeable markets
    passed_sorted = passed.sort_values("volume", ascending=False)
    passed_path = os.path.join(data_dir, "tradeable_markets.csv")
    passed_sorted.to_csv(passed_path, index=False)

    # 2. Rejected markets — combine all rejected tickers
    all_tickers = pd.read_csv(os.path.join(data_dir, "market_universe.csv"))["ticker"]
    passed_set = set(passed["ticker"])
    rej_rows = []
    for t in all_tickers:
        if t not in passed_set:
            reasons = rejections.get(t, ["orderbook depth (below threshold)"])
            rej_rows.append({"ticker": t, "rejection_reasons": "; ".join(reasons)})
    rej_df = pd.DataFrame(rej_rows)
    rej_path = os.path.join(data_dir, "rejected_markets.csv")
    rej_df.to_csv(rej_path, index=False)

    # 3. Liquidity report
    report = {
        "timestamp": now.isoformat(),
        "universe_size": total_start,
        "filter_pipeline": [
            {"filter": f"volume >= {MIN_VOLUME}", "remaining": after_vol, "cut": total_start - after_vol},
            {"filter": f"open_interest >= {MIN_OPEN_INTEREST}", "remaining": after_oi, "cut": after_vol - after_oi},
            {"filter": f"expiry {MIN_HOURS_TO_EXP}h-{MAX_HOURS_TO_EXP//24}d", "remaining": after_time, "cut": after_oi - after_time},
            {"filter": f"spread <= {MAX_SPREAD_CENTS}c", "remaining": after_spread, "cut": after_time - after_spread},
            {"filter": f"depth >= ${MIN_DEPTH_DOLLARS}", "remaining": len(passed), "cut": len(failed_depth)},
        ],
        "tradeable_count": len(passed),
        "rejection_rate_pct": round((1 - len(passed) / total_start) * 100, 2),
        "tradeable_stats": {
            "mean_volume": round(passed["volume"].mean(), 0) if len(passed) > 0 else 0,
            "median_volume": round(passed["volume"].median(), 0) if len(passed) > 0 else 0,
            "mean_spread": round(passed["spread"].mean(), 1) if len(passed) > 0 else 0,
            "mean_depth_dollars": round(passed["depth_dollars"].mean(), 0) if len(passed) > 0 else 0,
        },
    }
    report_path = os.path.join(data_dir, "liquidity_report.json")
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2)

    # ── Summary ──────────────────────────────────────────────────────────
    print("\n" + "=" * 72)
    print("  LIQUIDITY FILTER RESULTS")
    print("=" * 72)
    print(f"  {len(passed):,} of {total_start:,} markets pass  ({report['rejection_rate_pct']:.1f}% rejection rate)")
    print(f"\n  Filter cascade:")
    for step in report["filter_pipeline"]:
        print(f"    {step['filter']:<35} -> {step['remaining']:>6,} remain  (-{step['cut']:,})")

    if len(passed) > 0:
        print(f"\n  Tradeable market stats:")
        print(f"    Mean volume:     {passed['volume'].mean():>12,.0f}")
        print(f"    Median volume:   {passed['volume'].median():>12,.0f}")
        print(f"    Mean spread:     {passed['spread'].mean():>12.1f}c")
        print(f"    Mean depth:      ${passed['depth_dollars'].mean():>11,.0f}")

        # Category breakdown
        print(f"\n  By category:")
        for cat, grp in passed.groupby("category"):
            label = cat if cat else "(MVE/unmapped)"
            print(f"    {label:<25} {len(grp):>4} markets   avg_vol={grp['volume'].mean():>10,.0f}")

        # Top 20
        print(f"\n  Top 20 tradeable by volume:")
        print(f"  {'Ticker':<50} {'Cat':<12} {'Vol':>9} {'OI':>8} {'Sprd':>5} {'Depth$':>8}")
        print("  " + "-" * 96)
        for _, r in passed_sorted.head(20).iterrows():
            cat = (r["category"] or "-")[:11]
            print(f"  {r['ticker']:<50} {cat:<12} {r['volume']:>9,} {r['open_interest']:>8,} {r['spread']:>5.0f} {r['depth_dollars']:>8,.0f}")

    print(f"\n  Files:")
    print(f"    {passed_path}")
    print(f"    {rej_path}")
    print(f"    {report_path}")
    print("=" * 72)


if __name__ == "__main__":
    main()
