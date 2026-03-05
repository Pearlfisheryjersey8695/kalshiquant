from app.kalshi_client import KalshiClient

client = KalshiClient()

print("Balance:", client.get_balance())

markets = client.get_markets(limit=5)
print("Markets:", markets["markets"][0])

ticker = markets["markets"][0]["ticker"]

print("Orderbook:", client.get_orderbook(ticker))
print("Trades:", client.get_trades(ticker))