from app.kalshi_client import KalshiClient

client = KalshiClient()

MACRO_KEYWORDS = [
    "CPI", "INFLATION", "FED", "FOMC", "INTEREST", "RATE",
    "GDP", "UNEMPLOY", "JOBS", "PAYROLL", "NFP",
    "RECESSION", "TREASURY", "YIELD", "OIL", "WTI", "BRENT",
    "DXY", "DOLLAR", "EUR", "JPY", "GOLD", "S&P", "NASDAQ"
]

def looks_macro(s: dict) -> bool:
    text = f"{s.get('title','')} {s.get('ticker','')} {s.get('category','')}".upper()
    return any(k in text for k in MACRO_KEYWORDS)

# 1) fetch series (first page)
resp = client.get_series(limit=200)
series = resp.get("series", [])
macro_series = [s for s in series if looks_macro(s)]

print("Total series:", len(series))
print("Macro-like series found:", len(macro_series))
for s in macro_series[:25]:
    print("SERIES:", s.get("ticker"), "-", s.get("title"))

# 2) For each macro series, pull markets and score them
def market_score(m: dict) -> tuple:
    # Prefer: quoted markets, then volume_24h, then open_interest
    quoted = int((m.get("yes_bid", 0) > 0) or (m.get("yes_ask", 0) > 0) or (m.get("no_bid", 0) > 0) or (m.get("no_ask", 0) > 0))
    return (quoted, m.get("volume_24h", 0), m.get("open_interest", 0))

all_markets = []

for s in macro_series[:10]:  # scan first 10 macro series quickly
    st = s.get("ticker")
    if not st:
        continue
    mresp = client.get_markets_in_series(st, limit=200)
    ms = mresp.get("markets", [])
    for m in ms:
        m["_series_ticker"] = st
        all_markets.append(m)

print("\nMarkets pulled from macro series:", len(all_markets))

alive = sorted(all_markets, key=market_score, reverse=True)

print("\nTop 20 macro markets by (quoted, volume_24h, open_interest):")
for m in alive[:20]:
    print(
        m["ticker"],
        "| series:", m.get("_series_ticker"),
        "| vol24h:", m.get("volume_24h", 0),
        "| oi:", m.get("open_interest", 0),
        "| ybid/yask:", m.get("yes_bid", 0), "/", m.get("yes_ask", 0),
        "| nbid/nask:", m.get("no_bid", 0), "/", m.get("no_ask", 0),
        "| title:", (m.get("title","")[:70] + ("..." if len(m.get("title","")) > 70 else "")),
    )