from app.kalshi_client import KalshiClient

client = KalshiClient()

resp = client.get_markets(limit=200)
markets = resp.get("markets", [])

# Filter: not provisional + has 24h volume or open interest
liquid = [
    m for m in markets
    if (not m.get("is_provisional", False))
    and (m.get("volume_24h", 0) > 0 or m.get("open_interest", 0) > 0)
]

print("Total markets:", len(markets))
print("Liquid candidates:", len(liquid))

# Show top by 24h volume
liquid_sorted = sorted(liquid, key=lambda x: x.get("volume_24h", 0), reverse=True)

for m in liquid_sorted[:10]:
    print(m["ticker"], "vol24h=", m.get("volume_24h"), "oi=", m.get("open_interest"), "bid=", m.get("yes_bid"), "ask=", m.get("yes_ask"))