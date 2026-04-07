"""
Sentiment pipeline: event likelihood analysis + contrarian signals + cross-market
correlation + keyword analysis for prediction market trading.

For prediction markets, "sentiment" means: how likely is this event to occur?
The title IS the prediction. We analyze:
1. Title structure -- what event does this predict?
2. Price level -- extreme prices (< 0.10 or > 0.90) imply strong consensus
3. Category-specific priors -- economic indicators have base rates
4. Contrarian signal -- strong consensus often means the easy money is gone
5. Cross-market correlation -- sibling markets reveal directional skew

Cached per-market (1h TTL) to avoid excessive API calls.
"""

import json
import logging
import os
import re
import time

logger = logging.getLogger("kalshi.sentiment")

# Keyword sentiment patterns (kept as a lightweight fallback signal)
BULLISH_KEYWORDS = [
    "above", "higher", "exceed", "over", "surge", "rally", "gain",
    "increase", "rise", "up", "bull", "positive", "growth",
]
BEARISH_KEYWORDS = [
    "below", "lower", "under", "decline", "drop", "fall", "down",
    "bear", "negative", "recession", "crash", "decrease",
]


# ── Event Likelihood Analysis ──────────────────────────────────────────

def _analyze_event_likelihood(title: str, price: float) -> tuple[float, str]:
    """Analyze how likely the event described in the title is.

    For prediction markets the title describes the event. We parse it
    to extract numeric targets and compare against LIVE external data
    to produce a probability estimate independent of the market price.

    Returns (probability_estimate, reasoning).
    """
    from data.external_feeds import feed_manager
    title_lower = title.lower()

    # ── Fed rate decisions ──────────────────────────────────────────
    if "fed" in title_lower and ("rate" in title_lower or "fund" in title_lower):
        rate_match = re.search(r'(\d+\.?\d*)\s*%', title_lower)
        if not rate_match:
            # Try bare number after "t" prefix (e.g. T3.75)
            rate_match = re.search(r't(\d+\.?\d*)', title_lower)
        if rate_match:
            target_rate = float(rate_match.group(1))
            fed_data = feed_manager.fed.fetch()
            if not fed_data:
                # Skip rather than use stale fallback — better no signal than wrong signal
                return (price, "Fed feed unavailable — no signal")
            current_fed = fed_data["target_rate_mid"]
            distance = abs(target_rate - current_fed)

            is_above = any(kw in title_lower for kw in ("above", "over", "exceed", "higher"))
            is_below = any(kw in title_lower for kw in ("below", "under", "lower"))

            if is_above:
                # "rate above X%" -- higher target = less likely
                if target_rate <= current_fed - 0.25:
                    return (0.92, f"Rate already above {target_rate}% (current ~{current_fed}%)")
                elif distance < 0.25:
                    return (0.70, f"Near current rate ({current_fed}%), likely above {target_rate}%")
                elif distance < 0.50:
                    return (0.45, f"One move from current ({current_fed}%)")
                elif distance < 1.0:
                    return (0.20, f"Multiple moves needed from {current_fed}%")
                else:
                    return (0.05, f"Far from current rate ({current_fed}%)")
            elif is_below:
                if target_rate >= current_fed + 0.25:
                    return (0.92, f"Rate already below {target_rate}% (current ~{current_fed}%)")
                elif distance < 0.25:
                    return (0.70, f"Near current rate ({current_fed}%), likely below {target_rate}%")
                elif distance < 0.50:
                    return (0.45, f"One move from current ({current_fed}%)")
                elif distance < 1.0:
                    return (0.20, f"Multiple moves needed from {current_fed}%")
                else:
                    return (0.05, f"Far from current rate ({current_fed}%)")
            else:
                # Generic fed market -- use distance as proxy
                if distance < 0.25:
                    return (0.85, f"Near current rate ({current_fed}%), high probability")
                elif distance < 0.50:
                    return (0.50, f"One cut/hike from current ({current_fed}%)")
                elif distance < 1.0:
                    return (0.20, f"Multiple moves from current ({current_fed}%)")
                else:
                    return (0.05, f"Far from current rate ({current_fed}%)")

    # ── BTC price targets ───────────────────────────────────────────
    if "btc" in title_lower or "bitcoin" in title_lower:
        price_match = re.search(r'(\d{4,})', title_lower.replace(',', ''))
        if price_match:
            target = int(price_match.group(1))
            crypto_data = feed_manager.crypto.fetch()
            if not crypto_data or crypto_data.get("btc_price", 0) <= 0:
                return (price, "BTC feed unavailable — no signal")
            current_btc = crypto_data["btc_price"]

            if "max" in title_lower or "above" in title_lower or "over" in title_lower:
                ratio = target / current_btc if current_btc > 0 else 1
                if ratio < 0.9:
                    return (0.90, f"BTC ${current_btc:,.0f} already above ${target:,}")
                elif ratio < 1.05:
                    return (0.55, f"BTC ${current_btc:,.0f} near target ${target:,}")
                elif ratio < 1.15:
                    return (0.30, f"BTC needs +{(ratio-1)*100:.0f}% to ${target:,}")
                else:
                    return (0.10, f"BTC needs +{(ratio-1)*100:.0f}% -- unlikely this month")
            elif "min" in title_lower or "below" in title_lower or "under" in title_lower:
                ratio = target / current_btc if current_btc > 0 else 1
                if ratio > 1.1:
                    return (0.90, f"BTC ${current_btc:,.0f} already below ${target:,}")
                elif ratio > 0.95:
                    return (0.55, f"BTC ${current_btc:,.0f} near target ${target:,}")
                else:
                    return (0.25, f"BTC needs -{(1-ratio)*100:.0f}% to ${target:,}")

    # ── Gas prices ──────────────────────────────────────────────────
    if "gas" in title_lower or "aaag" in title_lower:
        gas_match = re.search(r'\$?(\d+\.?\d*)', title_lower)
        if gas_match:
            target_gas = float(gas_match.group(1))
            gas_data = feed_manager.gas.fetch()
            if not gas_data or gas_data.get("current_price", 0) <= 0:
                return (price, "Gas feed unavailable — no signal")
            current_gas = gas_data["current_price"]
            diff = target_gas - current_gas

            is_above = any(kw in title_lower for kw in ("above", "over", "exceed", "higher"))
            is_below = any(kw in title_lower for kw in ("below", "under", "lower"))

            if abs(diff) < 0.10:
                return (0.50, f"Gas near target ${target_gas:.2f} (current ~${current_gas:.2f})")
            elif is_above and diff > 0.30:
                return (0.15, f"Gas needs +${diff:.2f} -- significant increase needed")
            elif is_above and diff < -0.10:
                return (0.88, f"Gas already above ${target_gas:.2f}")
            elif is_below and diff < -0.30:
                return (0.15, f"Gas needs -${abs(diff):.2f} -- significant decrease needed")
            elif is_below and diff > 0.10:
                return (0.88, f"Gas already below ${target_gas:.2f}")
            elif diff > 0.30:
                return (0.15, f"Gas needs +${diff:.2f} -- significant increase needed")
            elif diff < -0.30:
                return (0.15, f"Gas needs -${abs(diff):.2f} -- significant decrease needed")

    # ── S&P 500 / stock index targets ───────────────────────────────
    if "s&p" in title_lower or "sp500" in title_lower or "spx" in title_lower:
        idx_match = re.search(r'(\d{4,})', title_lower.replace(',', ''))
        if idx_match:
            target_idx = int(idx_match.group(1))
            current_spx = 5700  # approximate late March 2026
            ratio = target_idx / current_spx
            if "above" in title_lower or "over" in title_lower:
                if ratio < 0.95:
                    return (0.88, f"S&P already above {target_idx}")
                elif ratio < 1.03:
                    return (0.50, f"S&P near target {target_idx}")
                elif ratio < 1.08:
                    return (0.25, f"S&P needs +{(ratio - 1) * 100:.0f}%")
                else:
                    return (0.08, f"S&P needs +{(ratio - 1) * 100:.0f}% -- unlikely short-term")

    # ── Inflation / CPI ─────────────────────────────────────────────
    if "cpi" in title_lower or "inflation" in title_lower:
        cpi_match = re.search(r'(\d+\.?\d*)\s*%', title_lower)
        if cpi_match:
            target_cpi = float(cpi_match.group(1))
            current_cpi = 2.8  # approximate
            diff = target_cpi - current_cpi
            if abs(diff) < 0.3:
                return (0.55, f"CPI near target {target_cpi}% (current ~{current_cpi}%)")
            elif diff > 0.5:
                return (0.20, f"CPI would need to rise to {target_cpi}% from {current_cpi}%")
            elif diff < -0.5:
                return (0.20, f"CPI would need to fall to {target_cpi}% from {current_cpi}%")

    # ── Default: use market price as consensus estimate ─────────────
    return (price, f"Using market price {price:.0%} as consensus")


# ── Contrarian Signal ──────────────────────────────────────────────────

def _contrarian_edge(price: float) -> float:
    """When price is extreme (> 0.90 or < 0.10), the consensus is strong
    but the payout for being right is tiny. Contrarian edge increases
    as price moves to extremes because the few cents of edge are
    worth more in expected value if consensus is wrong.
    """
    if price > 0.92:
        return -(price - 0.92) * 2  # slight bearish contrarian
    elif price < 0.08:
        return (0.08 - price) * 2  # slight bullish contrarian
    return 0.0


# ── Cross-Market Correlation Signal ────────────────────────────────────

def _cross_market_signal(ticker: str, price: float, all_markets: list = None) -> float:
    """If sibling markets (same event, different strikes) show a skew,
    use it as a directional signal.
    """
    if not all_markets:
        return 0.0

    # Find sibling markets (same event prefix)
    prefix = ticker.rsplit("-", 1)[0] if "-" in ticker else ticker
    siblings = [
        m for m in all_markets
        if m.get("ticker", "").startswith(prefix) and m["ticker"] != ticker
    ]

    if not siblings:
        return 0.0

    # Check if siblings imply a direction
    sibling_prices = [
        m.get("price", 0.5) for m in siblings if m.get("price", 0) > 0
    ]
    if not sibling_prices:
        return 0.0

    avg_sibling = sum(sibling_prices) / len(sibling_prices)
    # If this market is priced very differently from its siblings, there may be an edge
    divergence = price - avg_sibling
    return round(divergence * 0.1, 4)  # small signal from divergence


# ── Keyword Sentiment (lightweight fallback) ──────────────────────────

def get_keyword_sentiment(title: str) -> float:
    """Return a keyword sentiment score in [-1, 1].

    This is intentionally simple -- the event likelihood analysis above
    is the primary signal. Keywords are a weak tiebreaker.
    """
    title_lower = title.lower()
    bull_count = sum(1 for kw in BULLISH_KEYWORDS if kw in title_lower)
    bear_count = sum(1 for kw in BEARISH_KEYWORDS if kw in title_lower)
    total_kw = bull_count + bear_count
    if total_kw > 0:
        return (bull_count - bear_count) / total_kw  # [-1, 1]
    return 0.0


# ── Category Prior ─────────────────────────────────────────────────────

def _category_prior(category: str, price: float) -> float:
    """Return a small directional prior based on category behavior patterns."""
    cat = (category or "").lower()
    # Financial markets tend to mean-revert near extremes
    if price > 0.85 or price < 0.15:
        return -0.05 * (1 if price > 0.5 else -1)  # push toward 0.50
    return 0.0


# ── Economic Consensus Data (hardcoded for hackathon, Mar 2026) ────────

ECONOMIC_CONSENSUS = {
    "KXFED": {
        "indicator": "Federal Funds Rate",
        "current_rate": "3.25-3.50%",
        "next_meeting": "2026-05-06",
        "cut_25bp_prob": 0.45,
        "hold_prob": 0.50,
        "hike_prob": 0.05,
        "strike_probs": {
            "T2.75": 0.99,
            "T3.00": 0.97,
            "T3.25": 0.90,
            "T3.50": 0.55,
            "T3.75": 0.20,
            "T4.00": 0.05,
        },
    },
    "KXAAAGASM": {
        "indicator": "Average Gas Prices",
        "current_avg": "$3.30/gallon",
        "consensus_range": [3.10, 3.45],
        "strike_probs": {
            "2.90": 0.92,
            "3.00": 0.82,
            "3.10": 0.65,
            "3.30": 0.45,
            "3.50": 0.20,
            "3.70": 0.05,
        },
    },
    "KXBTCMAXMON": {
        "indicator": "Bitcoin Price (Max)",
        "current_price": "$82,000",
        "consensus_range": [75000, 95000],
        "strike_probs": {
            "7000000": 0.92,
            "7250000": 0.85,
            "7500000": 0.75,
            "7750000": 0.62,
            "8000000": 0.50,
            "8250000": 0.38,
            "8500000": 0.25,
        },
    },
    "KXBTCMINMON": {
        "indicator": "Bitcoin Price (Min)",
        "current_price": "$82,000",
        "strike_probs": {
            "5500000": 0.08,
            "5750000": 0.12,
            "6000000": 0.18,
            "6250000": 0.28,
            "6500000": 0.40,
        },
    },
}


# ── Main Sentiment Function ───────────────────────────────────────────

def get_sentiment(
    ticker: str, title: str, category: str, market_price: float,
    all_markets: list = None,
) -> dict:
    """Compute sentiment for a prediction market.

    For prediction markets, "sentiment" means: how likely is this event?
    We analyze:
    1. Title structure -- what event does this predict?
    2. Price level -- extreme prices (< 0.10 or > 0.90) imply strong consensus
    3. Category-specific priors -- economic indicators have base rates
    4. Contrarian signal -- strong consensus often means the easy money is gone
    5. Cross-market correlation -- sibling markets reveal directional skew

    Returns a dict with consensus_prob, consensus_edge, sentiment_edge,
    ai_prob, ai_edge, reasoning, contrarian_edge, cross_market_edge, source.
    """
    # Also check economic consensus for known tickers
    econ_consensus = _get_economic_consensus_edge(ticker, market_price)

    # Event likelihood from title analysis
    event_prob, reasoning = _analyze_event_likelihood(title, market_price)

    # If we have a strong economic consensus match, blend it in
    if econ_consensus.get("consensus_prob", 0) > 0:
        # Economic consensus is curated data -- weight it heavily
        event_prob = 0.6 * econ_consensus["consensus_prob"] + 0.4 * event_prob
        reasoning = f"{econ_consensus.get('source', '')}: {reasoning}"

    # Contrarian signal at price extremes
    contrarian = _contrarian_edge(market_price)

    # Cross-market divergence
    cross_mkt = _cross_market_signal(ticker, market_price, all_markets)

    # Keyword sentiment (weak signal)
    keyword_sentiment = get_keyword_sentiment(title)

    # AI probability = event likelihood estimate
    ai_prob = event_prob
    ai_edge = round(ai_prob - market_price, 4)

    # Consensus = weighted combination of event analysis, market price, keywords
    consensus_prob = 0.5 * event_prob + 0.3 * market_price + 0.2 * (0.5 + keyword_sentiment * 0.2)
    consensus_edge = round(consensus_prob - market_price, 4)

    # Final sentiment edge = consensus + contrarian + cross-market
    sentiment_edge = round(consensus_edge + contrarian + cross_mkt, 4)

    return {
        "consensus_prob": round(consensus_prob, 4),
        "consensus_edge": consensus_edge,
        "source": "event_analysis+contrarian+cross_market",
        "ai_prob": round(ai_prob, 4),
        "ai_edge": ai_edge,
        "reasoning": reasoning,
        "sentiment_edge": sentiment_edge,
        "contrarian_edge": round(contrarian, 4),
        "cross_market_edge": round(cross_mkt, 4),
    }


def _get_economic_consensus_edge(ticker: str, market_price: float) -> dict:
    """Compare market price against hardcoded economic consensus probability."""
    for prefix, data in ECONOMIC_CONSENSUS.items():
        if not ticker.startswith(prefix):
            continue
        strike_probs = data.get("strike_probs", {})
        for strike_key, consensus_prob in strike_probs.items():
            if strike_key in ticker:
                edge = consensus_prob - market_price
                return {
                    "consensus_prob": round(consensus_prob, 4),
                    "consensus_edge": round(edge, 4),
                    "source": data["indicator"],
                    "details": data.get(
                        "current_rate",
                        data.get("current_avg", data.get("current_price", "")),
                    ),
                }
    return {"consensus_prob": 0.0, "consensus_edge": 0.0, "source": "", "details": ""}


# ── Compatibility Functions ────────────────────────────────────────────

def get_consensus_edge(ticker: str, market_price: float, title: str = "", category: str = "") -> dict:
    """Consensus edge for backtest and ensemble compatibility.

    If title is provided, runs the full event analysis pipeline.
    Otherwise falls back to economic consensus data or neutral default.
    """
    if title:
        result = get_sentiment(ticker, title, category, market_price)
        return {
            "consensus_edge": result["consensus_edge"],
            "consensus_prob": result["consensus_prob"],
            "source": result["source"],
        }
    # Fallback: try economic consensus
    econ = _get_economic_consensus_edge(ticker, market_price)
    if econ.get("consensus_prob", 0) > 0:
        return {
            "consensus_edge": econ["consensus_edge"],
            "consensus_prob": econ["consensus_prob"],
            "source": econ["source"],
        }
    return {
        "consensus_edge": 0.0,
        "consensus_prob": market_price,
        "source": "neutral_default",
    }


# ── AI Sentiment (Claude API) ─────────────────────────────────────────

_sentiment_cache: dict[str, dict] = {}  # ticker -> {result, timestamp}
CACHE_TTL_SECONDS = 3600  # 1 hour


def get_ai_sentiment(
    ticker: str, title: str, category: str, market_price: float
) -> dict:
    """Call Claude Sonnet to estimate probability of YES resolution.
    Cached for 1 hour per ticker. Returns empty result if no API key.
    """
    # Check cache
    cached = _sentiment_cache.get(ticker)
    if cached and (time.time() - cached["timestamp"]) < CACHE_TTL_SECONDS:
        result = cached["result"].copy()
        result["ai_edge"] = round(result["ai_prob"] - market_price, 4)
        return result

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return {"ai_prob": 0.0, "ai_edge": 0.0, "reasoning": ""}

    try:
        import anthropic

        client = anthropic.Anthropic(api_key=api_key)

        prompt = (
            f"Given this prediction market: {title}\n"
            f"Category: {category}\n"
            f"Current market price (probability): {market_price:.2f}\n\n"
            f"Based on the most recent publicly available information as of your knowledge, "
            f"estimate the probability of YES resolution as a number between 0 and 1. "
            f"Provide your reasoning in 2 sentences. "
            f'Return JSON: {{"probability": 0.XX, "reasoning": "..."}}'
        )

        message = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=256,
            messages=[{"role": "user", "content": prompt}],
        )

        response_text = message.content[0].text.strip()
        # Parse JSON from response (handle markdown code blocks)
        if "```" in response_text:
            response_text = response_text.split("```")[1]
            if response_text.startswith("json"):
                response_text = response_text[4:]
        parsed = json.loads(response_text)

        ai_prob = float(parsed.get("probability", 0.5))
        ai_prob = max(0.01, min(0.99, ai_prob))
        reasoning = str(parsed.get("reasoning", ""))

        result = {
            "ai_prob": round(ai_prob, 4),
            "ai_edge": round(ai_prob - market_price, 4),
            "reasoning": reasoning,
        }

        _sentiment_cache[ticker] = {"result": result, "timestamp": time.time()}
        logger.info(
            "AI sentiment for %s: prob=%.2f, edge=%.4f",
            ticker,
            ai_prob,
            result["ai_edge"],
        )
        return result

    except Exception as e:
        logger.warning("AI sentiment failed for %s: %s", ticker, e)
        return {"ai_prob": 0.0, "ai_edge": 0.0, "reasoning": f"Error: {e}"}
