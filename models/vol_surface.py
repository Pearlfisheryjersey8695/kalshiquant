"""
Implied probability distribution from event series strike prices.
Groups markets by event prefix, extracts strikes, computes:
1. Market-implied probability at each strike
2. Implied PDF (density between strikes)
3. Theoretical normal distribution for comparison
4. Mispriced strikes where market deviates from theoretical
"""

import numpy as np
from scipy.stats import norm
from collections import defaultdict


def compute_vol_surface(markets: list[dict]) -> dict[str, dict]:
    """Compute implied distributions for all event series.
    Returns {prefix: surface_data}.
    """
    # Group by series prefix (everything before last hyphen-separated strike)
    series: dict[str, list[dict]] = defaultdict(list)
    for m in markets:
        ticker = m["ticker"]
        parts = ticker.rsplit("-", 1)
        if len(parts) != 2:
            continue
        prefix, strike_str = parts[0], parts[1]
        # Try to parse strike (handle "T3.50" format for Fed, plain numbers for others)
        strike_str_clean = strike_str.lstrip("T")
        try:
            strike = float(strike_str_clean)
        except ValueError:
            continue
        # Price can come from different column names
        price = m.get("price", 0)
        if not price:
            bid = m.get("yes_bid", 0) or 0
            ask = m.get("yes_ask", 0) or 0
            price = (bid + ask) / 2 / 100 if (bid + ask) > 2 else (bid + ask) / 200
        series[prefix].append({
            "ticker": ticker,
            "strike": strike,
            "price": price if price <= 1.0 else price / 100,  # normalize to [0, 1]
            "yes_bid": m.get("yes_bid", 0),
            "yes_ask": m.get("yes_ask", 0),
        })

    result = {}
    for prefix, strikes_data in series.items():
        if len(strikes_data) < 3:
            continue

        strikes_data.sort(key=lambda s: s["strike"])
        strikes = [s["strike"] for s in strikes_data]
        implied_probs = [s["price"] for s in strikes_data]

        # Detect direction: if prices decrease with strike, it's exceedance P(X > s)
        # If prices increase with strike, it's CDF P(X < s)
        if len(strikes) >= 2:
            is_exceedance = implied_probs[0] > implied_probs[-1]
        else:
            is_exceedance = "MAX" in prefix.upper() or "FED" in prefix.upper()

        # Compute implied PDF: derivative of CDF
        implied_pdf = []
        for i in range(len(strikes) - 1):
            ds = strikes[i + 1] - strikes[i]
            if ds <= 0:
                continue
            if is_exceedance:
                dp = implied_probs[i] - implied_probs[i + 1]
            else:
                dp = implied_probs[i + 1] - implied_probs[i]
            density = max(dp / ds, 0)
            implied_pdf.append({
                "strike_low": strikes[i],
                "strike_high": strikes[i + 1],
                "strike_mid": round((strikes[i] + strikes[i + 1]) / 2, 4),
                "density": round(density, 8),
                "probability": round(max(dp, 0), 4),
            })

        # Fit theoretical normal distribution
        mean_est = _estimate_mean(strikes, implied_probs) if is_exceedance else np.mean(strikes)
        std_est = _estimate_std(strikes, implied_probs, mean_est) if is_exceedance else (max(strikes) - min(strikes)) / 4

        # Theoretical probabilities at each strike
        theoretical_probs = []
        for s in strikes:
            if is_exceedance:
                theo = 1.0 - norm.cdf(s, loc=mean_est, scale=max(std_est, 1e-6))
            else:
                theo = norm.cdf(s, loc=mean_est, scale=max(std_est, 1e-6))
            theoretical_probs.append(round(float(theo), 4))

        # Mispricings: |market - theoretical| > 5c
        mispricings = []
        for i, sd in enumerate(strikes_data):
            diff = implied_probs[i] - theoretical_probs[i]
            if abs(diff) > 0.05:
                mispricings.append({
                    "ticker": sd["ticker"],
                    "strike": strikes[i],
                    "market_prob": round(implied_probs[i], 4),
                    "theoretical_prob": theoretical_probs[i],
                    "mispricing": round(diff, 4),
                    "direction": "OVERPRICED" if diff > 0 else "UNDERPRICED",
                })

        result[prefix] = {
            "prefix": prefix,
            "strikes": [
                {
                    "ticker": s["ticker"],
                    "strike": s["strike"],
                    "market_prob": round(s["price"], 4),
                    "theoretical_prob": theoretical_probs[i],
                }
                for i, s in enumerate(strikes_data)
            ],
            "implied_pdf": implied_pdf,
            "theoretical_mean": round(float(mean_est), 2),
            "theoretical_std": round(float(std_est), 2),
            "mispricings": mispricings,
            "n_strikes": len(strikes),
        }

    return result


def _estimate_mean(strikes, probs):
    """Estimate mean from exceedance probs. Strike where P(X>s) ~ 0.5."""
    for i in range(len(strikes) - 1):
        if probs[i] >= 0.5 >= probs[i + 1]:
            frac = (probs[i] - 0.5) / (probs[i] - probs[i + 1] + 1e-9)
            return strikes[i] + frac * (strikes[i + 1] - strikes[i])
    total_p = sum(probs)
    if total_p > 0:
        return sum(s * p for s, p in zip(strikes, probs)) / total_p
    return np.mean(strikes)


def _estimate_std(strikes, probs, mean):
    """Estimate std from slope of exceedance curve at the mean."""
    for i in range(len(strikes) - 1):
        if strikes[i] <= mean <= strikes[i + 1]:
            ds = strikes[i + 1] - strikes[i]
            dp = abs(probs[i] - probs[i + 1])
            if dp > 0 and ds > 0:
                return 0.3989 * ds / dp
    return (max(strikes) - min(strikes)) / 4


def get_vol_surface_for_event(markets: list[dict], event_prefix: str) -> dict | None:
    """Get vol surface for a specific event prefix."""
    surfaces = compute_vol_surface(markets)
    # Exact match first
    if event_prefix in surfaces:
        return surfaces[event_prefix]
    # Partial match
    for prefix, data in surfaces.items():
        if event_prefix in prefix or prefix in event_prefix:
            return data
    return None
