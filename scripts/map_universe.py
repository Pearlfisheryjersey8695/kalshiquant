"""
Phase 0.3 — Map the Data Universe
Two-pass strategy:
  1) Paginated scan of /markets (captures recent/active, drops provisional)
  2) Targeted series-based pull for high-value categories missed by scan
Enriches with event-level category/series_ticker, saves data/market_universe.csv.
"""

import sys, os, time
import requests as req

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.kalshi_client import KalshiClient
import pandas as pd

MAX_SCAN_PAGES = 500
RETRY = 3

# Hand-picked high-value series to supplement (macro, crypto, financials)
TARGET_SERIES = [
    # Economics
    "KXCPI", "KXFED", "KXGDP", "KXNFP", "KXUE", "KX3MTBILL",
    "KXPCE", "KXPPI", "KXRETAILSALES", "KXISMMAN", "KXISMSVC",
    "KXAAAGASM", "KXSPRLVL",
    # Crypto
    "KXBTC", "KXBTCD", "KXETH", "KXETHD", "KXBTC15M", "KXETH15M",
    "KXBTCMAXY", "KXBTCMINY", "KXBTCMAXMON", "KXBTCMINMON",
    "KXETHMAXY", "KXETHMINY",
    "KXBTCETHATH", "KXBTCVSGOLD", "KXSOL", "KXDOGE",
    # Financials / Indices
    "KXINX", "KXINXMAXY", "KXINXMINY", "KXINXPOS",
    "KXEURUSD", "KXSP500",
    # Rates
    "KXDEELRIP",
]


def api_get_retry(client, path, params=None):
    for attempt in range(RETRY):
        try:
            return client.get(path, params=params)
        except req.HTTPError as e:
            if attempt < RETRY - 1 and e.response is not None and e.response.status_code in (429, 502, 503):
                time.sleep(2 ** attempt)
            else:
                raise


def paginate_all(client, path, key, params, keep_fn=None, max_pages=200, label=""):
    params = dict(params)
    kept, scanned, page = [], 0, 0
    while page < max_pages:
        resp = api_get_retry(client, path, params)
        batch = resp.get(key, [])
        scanned += len(batch)
        if keep_fn:
            kept.extend(r for r in batch if keep_fn(r))
        else:
            kept.extend(batch)
        page += 1
        if page % 50 == 0:
            print(f"    pg {page}: scanned {scanned:,}, kept {len(kept):,} {label}")
        cursor = resp.get("cursor")
        if not cursor or not batch:
            break
        params["cursor"] = cursor
    return kept, scanned


def main():
    client = KalshiClient()
    data_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
    os.makedirs(data_dir, exist_ok=True)

    # ━━ Step 1: Pull events (capped for category mapping) ━━━━━━━━━━━━━━
    print("[1/4] Pulling events for category mapping ...")
    t0 = time.perf_counter()
    events, _ = paginate_all(client, "/trade-api/v2/events", "events",
                             {"limit": 200}, max_pages=100, label="events")
    print(f"  {len(events):,} events in {time.perf_counter()-t0:.1f}s")

    event_map = {}
    for ev in events:
        et = ev["event_ticker"]
        cat = ev.get("category", "")
        st = ev.get("series_ticker", "")
        event_map[et] = {"category": cat, "series_ticker": st}

    # ━━ Step 2: Paginated broad scan ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    print("[2/4] Broad market scan (dropping provisional inline) ...")
    t0 = time.perf_counter()
    scan_markets, total_scanned = paginate_all(
        client, "/trade-api/v2/markets", "markets", {"limit": 200},
        keep_fn=lambda m: not m.get("is_provisional", False),
        max_pages=MAX_SCAN_PAGES, label="real markets"
    )
    print(f"  Scanned {total_scanned:,}, kept {len(scan_markets):,} in {time.perf_counter()-t0:.1f}s")

    seen_tickers = {m["ticker"] for m in scan_markets}

    # ━━ Step 3: Targeted series-based supplement ━━━━━━━━━━━━━━━━━━━━━━━━
    print(f"[3/4] Supplementing with {len(TARGET_SERIES)} hand-picked series ...")
    supplement = []
    for i, st in enumerate(TARGET_SERIES):
        params = {"limit": 200, "series_ticker": st}
        added = 0
        pages = 0
        while pages < 10:  # cap at 2000 markets per series
            resp = api_get_retry(client, "/trade-api/v2/markets", params)
            for m in resp.get("markets", []):
                if m["ticker"] not in seen_tickers and not m.get("is_provisional", False):
                    supplement.append(m)
                    seen_tickers.add(m["ticker"])
                    added += 1
            cursor = resp.get("cursor")
            pages += 1
            if not cursor:
                break
            params["cursor"] = cursor
        if added > 0:
            print(f"  {st:<25} +{added} markets")
    print(f"  Supplement total: +{len(supplement):,} new markets")

    all_markets = scan_markets + supplement
    print(f"  Total: {len(all_markets):,} ({len(scan_markets):,} from scan + {len(supplement):,} supplemented)")

    # ━━ Step 4: Build DataFrame + save ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    print("[4/4] Building CSV ...")
    rows = []
    for m in all_markets:
        et = m.get("event_ticker", "")
        ev_info = event_map.get(et, {})
        yes_bid = m.get("yes_bid", 0) or 0
        yes_ask = m.get("yes_ask", 0) or 0
        spread = (yes_ask - yes_bid) if (yes_ask and yes_bid) else None

        rows.append({
            "ticker": m.get("ticker", ""),
            "title": (m.get("title", "") or "")[:120],
            "event_ticker": et,
            "series_ticker": ev_info.get("series_ticker", ""),
            "category": ev_info.get("category", ""),
            "status": m.get("status", ""),
            "market_type": m.get("market_type", ""),
            "yes_bid": yes_bid,
            "yes_ask": yes_ask,
            "no_bid": m.get("no_bid", 0) or 0,
            "no_ask": m.get("no_ask", 0) or 0,
            "spread": spread,
            "last_price": m.get("last_price", 0),
            "previous_price": m.get("previous_price", 0),
            "volume": m.get("volume", 0),
            "volume_24h": m.get("volume_24h", 0),
            "open_interest": m.get("open_interest", 0),
            "liquidity": m.get("liquidity", 0),
            "close_time": m.get("close_time", ""),
            "expiration_time": m.get("expiration_time", ""),
            "expected_expiration_time": m.get("expected_expiration_time", ""),
            "open_time": m.get("open_time", ""),
        })

    df = pd.DataFrame(rows)
    csv_path = os.path.join(data_dir, "market_universe.csv")
    df.to_csv(csv_path, index=False)

    # ━━ Summary ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    has_vol = df[df["volume"] > 0]
    has_oi = df[df["open_interest"] > 0]
    quoted = df[(df["yes_bid"] > 0) & (df["yes_ask"] > 0)]
    mve = df[df["ticker"].str.startswith("KXMVE")]
    std = df[~df["ticker"].str.startswith("KXMVE")]

    print("\n" + "=" * 72)
    print("  MARKET UNIVERSE SUMMARY")
    print("=" * 72)
    print(f"  Total real markets:       {len(df):>8,}")
    print(f"    MVE combos:             {len(mve):>8,}")
    print(f"    Standard:               {len(std):>8,}")
    print(f"  With lifetime volume:     {len(has_vol):>8,}")
    print(f"  With open interest:       {len(has_oi):>8,}")
    print(f"  Two-sided quotes:         {len(quoted):>8,}")

    print("\n  Markets by category:")
    for cat, cnt in df["category"].value_counts().head(20).items():
        vol_cnt = len(df[(df["category"] == cat) & (df["volume"] > 0)])
        q_cnt = len(df[(df["category"] == cat) & (df["yes_bid"] > 0) & (df["yes_ask"] > 0)])
        label = cat if cat else "(unmapped MVE)"
        print(f"    {label:<30} {cnt:>6,}  vol>0: {vol_cnt:>5,}  quoted: {q_cnt:>5,}")

    if len(has_vol) > 0:
        print(f"\n  Volume stats (n={len(has_vol):,}):")
        print(f"    Mean: {has_vol['volume'].mean():>10,.0f}  Median: {has_vol['volume'].median():>10,.0f}  Max: {has_vol['volume'].max():>10,}")

    if len(quoted) > 0:
        qs = df[df["spread"].notna() & (df["spread"] >= 0)]
        print(f"\n  Spread (n={len(qs):,} quoted):  Mean: {qs['spread'].mean():.1f}c  Median: {qs['spread'].median():.1f}c")

    # Top by volume per category
    for cat in ["Economics", "Financials", "Crypto", "Politics", "Elections", "Sports"]:
        cat_df = df[df["category"] == cat].nlargest(5, "volume")
        if len(cat_df) == 0:
            continue
        print(f"\n  Top 5 {cat}:")
        for _, r in cat_df.iterrows():
            sp = f"{r['spread']:.0f}" if pd.notna(r["spread"]) else "-"
            print(f"    {r['ticker']:<50} vol={r['volume']:>7,} oi={r['open_interest']:>7,} bid={r['yes_bid']:>3} ask={r['yes_ask']:>3}")

    print("\n" + "=" * 72)
    print(f"  Saved: {csv_path}  ({len(df):,} rows)")


if __name__ == "__main__":
    main()
