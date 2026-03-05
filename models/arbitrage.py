"""
Cross-event arbitrage scanner.
Detects structural mispricings in event series where mathematical
constraints must hold:
  1. Monotonicity: P(X > a) >= P(X > b) when a < b (for "max/above" events)
  2. Bracket: implied bracket probabilities must be non-negative
"""

import re
from collections import defaultdict


def _extract_strike(ticker: str):
    """Extract numeric strike from ticker suffix. Returns (prefix, strike) or None."""
    parts = ticker.rsplit("-", 1)
    if len(parts) != 2:
        return None
    try:
        strike = float(parts[1])
        return parts[0], strike
    except ValueError:
        return None


def scan_arbitrage(markets: list[dict]) -> list[dict]:
    """
    Group markets by event series prefix, extract strikes, check constraints.
    Returns list of arbitrage opportunities sorted by edge descending.
    """
    # Group by event prefix
    series = defaultdict(list)
    for m in markets:
        ticker = m.get("ticker", "")
        parsed = _extract_strike(ticker)
        if parsed is None:
            continue
        prefix, strike = parsed
        series[prefix].append({
            "ticker": ticker,
            "strike": strike,
            "price": m.get("price", 0),
            "yes_bid": m.get("yes_bid", 0),
            "yes_ask": m.get("yes_ask", 0),
        })

    opportunities = []
    for prefix, strikes in series.items():
        if len(strikes) < 2:
            continue

        strikes.sort(key=lambda s: s["strike"])

        # Determine event direction:
        # "MAX"/"ABOVE" events: higher strike = lower probability
        # "MIN"/"BELOW" events: higher strike = higher probability
        upper = prefix.upper()
        is_max = "MAX" in upper or "ABOVE" in upper
        is_min = "MIN" in upper or "BELOW" in upper

        for i in range(len(strikes) - 1):
            lower = strikes[i]
            upper_strike = strikes[i + 1]

            if is_max:
                # P(X > low_strike) >= P(X > high_strike)
                # So lower strike should have higher price
                if lower["price"] < upper_strike["price"] - 0.02:
                    edge = upper_strike["price"] - lower["price"]
                    opportunities.append({
                        "type": "MONOTONICITY",
                        "prefix": prefix,
                        "buy_ticker": lower["ticker"],
                        "sell_ticker": upper_strike["ticker"],
                        "buy_price": round(lower["price"], 4),
                        "sell_price": round(upper_strike["price"], 4),
                        "edge": round(edge, 4),
                        "description": (
                            f"Buy {lower['ticker']} at {lower['price']:.2f}, "
                            f"sell {upper_strike['ticker']} at {upper_strike['price']:.2f}. "
                            f"Monotonicity violation: {edge*100:.1f}c edge."
                        ),
                    })
            elif is_min:
                # P(X < high_strike) >= P(X < low_strike)
                # So higher strike should have higher price
                if upper_strike["price"] < lower["price"] - 0.02:
                    edge = lower["price"] - upper_strike["price"]
                    opportunities.append({
                        "type": "MONOTONICITY",
                        "prefix": prefix,
                        "buy_ticker": upper_strike["ticker"],
                        "sell_ticker": lower["ticker"],
                        "buy_price": round(upper_strike["price"], 4),
                        "sell_price": round(lower["price"], 4),
                        "edge": round(edge, 4),
                        "description": (
                            f"Monotonicity violation: {edge*100:.1f}c edge."
                        ),
                    })

            # Bracket check: implied bracket probability should be non-negative
            implied_bracket = abs(lower["price"] - upper_strike["price"])
            if implied_bracket < -0.01:
                opportunities.append({
                    "type": "BRACKET",
                    "prefix": prefix,
                    "buy_ticker": lower["ticker"],
                    "sell_ticker": upper_strike["ticker"],
                    "buy_price": round(lower["price"], 4),
                    "sell_price": round(upper_strike["price"], 4),
                    "edge": round(abs(implied_bracket), 4),
                    "description": "Negative bracket probability implied.",
                })

        # Check sum constraint: for adjacent strikes covering full range,
        # sum of bracket probabilities should approximately equal 1
        if len(strikes) >= 3:
            total_prob = sum(s["price"] for s in strikes)
            # Not a strict arb but useful signal if sum is very wrong
            if total_prob > 1.5 or total_prob < 0.3:
                opportunities.append({
                    "type": "SUM_VIOLATION",
                    "prefix": prefix,
                    "buy_ticker": strikes[0]["ticker"],
                    "sell_ticker": strikes[-1]["ticker"],
                    "buy_price": round(strikes[0]["price"], 4),
                    "sell_price": round(strikes[-1]["price"], 4),
                    "edge": round(abs(total_prob - 1.0) * 0.1, 4),
                    "description": (
                        f"Sum of {len(strikes)} strike prices = {total_prob:.2f} "
                        f"(expected ~1.0). Possible mispricing across series."
                    ),
                })

    opportunities.sort(key=lambda o: o["edge"], reverse=True)
    return opportunities
