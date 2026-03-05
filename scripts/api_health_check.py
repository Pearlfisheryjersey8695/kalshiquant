"""
Phase 0.2 — API Health Check
Hits every relevant Kalshi endpoint, logs status/timing/sample data,
saves full audit to data/api_audit.json, prints summary table.
"""

import sys, os, json, time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.kalshi_client import KalshiClient
import requests


def probe(client: KalshiClient, label: str, call, *args, **kwargs) -> dict:
    """Run an API call, capture timing/status/shape."""
    result = {
        "endpoint": label,
        "status": None,
        "response_time_ms": None,
        "record_count": None,
        "sample_keys": None,
        "sample": None,
        "error": None,
    }
    t0 = time.perf_counter()
    try:
        data = call(*args, **kwargs)
        elapsed = (time.perf_counter() - t0) * 1000
        result["status"] = 200
        result["response_time_ms"] = round(elapsed, 1)

        # figure out the record count and sample
        if isinstance(data, dict):
            result["sample_keys"] = list(data.keys())
            # find the list-valued key (markets, series, trades, etc.)
            list_key = next((k for k, v in data.items() if isinstance(v, list)), None)
            if list_key:
                records = data[list_key]
                result["record_count"] = len(records)
                if records:
                    result["sample"] = records[0]
            else:
                result["record_count"] = 1
                result["sample"] = data
        elif isinstance(data, list):
            result["record_count"] = len(data)
            if data:
                result["sample"] = data[0]
    except requests.HTTPError as e:
        elapsed = (time.perf_counter() - t0) * 1000
        result["status"] = e.response.status_code if e.response is not None else 0
        result["response_time_ms"] = round(elapsed, 1)
        result["error"] = str(e)
    except Exception as e:
        elapsed = (time.perf_counter() - t0) * 1000
        result["response_time_ms"] = round(elapsed, 1)
        result["error"] = str(e)

    return result


def main():
    client = KalshiClient()

    # -- grab a sample ticker for orderbook/trades probes --
    boot = client.get_markets(limit=5)
    sample_ticker = boot["markets"][0]["ticker"] if boot.get("markets") else None

    probes = [
        ("GET /markets (list)",               lambda: client.get_markets(limit=100)),
        ("GET /markets?status=open",          lambda: client.get("/trade-api/v2/markets", params={"limit": 100, "status": "open"})),
        ("GET /events",                       lambda: client.get("/trade-api/v2/events", params={"limit": 100})),
        ("GET /series",                       lambda: client.get_series(limit=100)),
        ("GET /markets/{ticker}/orderbook",   lambda: client.get_orderbook(sample_ticker) if sample_ticker else (_ for _ in ()).throw(ValueError("no ticker"))),
        ("GET /markets/trades?ticker=...",    lambda: client.get_trades(sample_ticker) if sample_ticker else (_ for _ in ()).throw(ValueError("no ticker"))),
        ("GET /portfolio/balance",            lambda: client.get_balance()),
        ("GET /portfolio/positions",          lambda: client.get_positions()),
        ("GET /portfolio/orders",             lambda: client.get_orders()),
        ("GET /market/{ticker} (single)",     lambda: client.get_market(sample_ticker) if sample_ticker else (_ for _ in ()).throw(ValueError("no ticker"))),
    ]

    results = []
    for label, call in probes:
        print(f"  probing {label} ...", end=" ", flush=True)
        r = probe(client, label, call)
        tag = f"{r['status']}  {r['response_time_ms']}ms" if r["status"] else f"ERR  {r['error'][:60]}"
        print(tag)
        results.append(r)

    # -- save full audit --
    audit = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "sample_ticker": sample_ticker,
        "probes": results,
    }
    # make sample JSON-serializable (truncate large nested objects)
    def truncate(obj, depth=0):
        if depth > 2:
            return "..."
        if isinstance(obj, dict):
            return {k: truncate(v, depth + 1) for k, v in list(obj.items())[:20]}
        if isinstance(obj, list):
            return [truncate(v, depth + 1) for v in obj[:3]] + (["..."] if len(obj) > 3 else [])
        return obj

    audit_safe = json.loads(json.dumps(audit, default=str))
    for p in audit_safe["probes"]:
        if p.get("sample"):
            p["sample"] = truncate(p["sample"])

    data_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
    os.makedirs(data_dir, exist_ok=True)
    out_path = os.path.join(data_dir, "api_audit.json")
    with open(out_path, "w") as f:
        json.dump(audit_safe, f, indent=2)

    # -- summary table --
    print("\n" + "=" * 72)
    print(f"  KALSHI API HEALTH CHECK — {audit['timestamp']}")
    print(f"  Sample ticker: {sample_ticker}")
    print("=" * 72)
    print(f"  {'Endpoint':<40} {'Status':>6} {'Time ms':>8} {'Records':>8}")
    print("-" * 72)
    for r in results:
        status = str(r["status"]) if r["status"] else "FAIL"
        ms = f"{r['response_time_ms']:.0f}" if r["response_time_ms"] else "-"
        count = str(r["record_count"]) if r["record_count"] is not None else "-"
        print(f"  {r['endpoint']:<40} {status:>6} {ms:>8} {count:>8}")
    print("=" * 72)

    ok = sum(1 for r in results if r["status"] == 200)
    print(f"\n  {ok}/{len(results)} endpoints returned 200")
    if ok < len(results):
        for r in results:
            if r["status"] != 200:
                print(f"  FAILED: {r['endpoint']} — {r.get('error', 'status ' + str(r.get('status')))}")

    print(f"\n  Full audit saved to {out_path}")


if __name__ == "__main__":
    main()
