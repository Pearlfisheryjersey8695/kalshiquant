"""
Sentiment pipeline: economic consensus + AI probability estimation.
Cached per-market (1h TTL) to avoid excessive API calls.
"""

import json
import logging
import os
import time

logger = logging.getLogger("kalshi.sentiment")

# ── Economic Consensus Data (hardcoded for hackathon, Mar 2026) ──────────

ECONOMIC_CONSENSUS = {
    "KXFED": {
        "indicator": "Federal Funds Rate",
        "current_rate": "4.25-4.50%",
        "next_meeting": "2026-03-18",
        "cut_25bp_prob": 0.55,
        "hold_prob": 0.40,
        "hike_prob": 0.05,
        # Strike mapping: probability that upper bound > X%
        "strike_probs": {
            "T2.75": 0.99,
            "T3.00": 0.99,
            "T3.25": 0.95,
            "T3.50": 0.95,
            "T3.75": 0.95,
        },
    },
    "KXAAAGASM": {
        "indicator": "Average Gas Prices",
        "current_avg": "$3.15/gallon",
        "consensus_range": [2.95, 3.25],
        "strike_probs": {
            "2.90": 0.85,
            "3.00": 0.70,
            "3.10": 0.55,
            "3.30": 0.25,
            "3.70": 0.05,
        },
    },
    "KXBTCMAXMON": {
        "indicator": "Bitcoin Price (Max)",
        "current_price": "$84,000",
        "consensus_range": [75000, 95000],
        "strike_probs": {
            "7000000": 0.90,
            "7250000": 0.82,
            "7500000": 0.72,
            "7750000": 0.60,
            "8000000": 0.48,
            "8250000": 0.35,
            "8500000": 0.22,
        },
    },
    "KXBTCMINMON": {
        "indicator": "Bitcoin Price (Min)",
        "current_price": "$84,000",
        "strike_probs": {
            "5500000": 0.08,
            "5750000": 0.12,
            "6000000": 0.18,
            "6250000": 0.28,
            "6500000": 0.40,
        },
    },
}


def get_consensus_edge(ticker: str, market_price: float) -> dict:
    """Compare market price against consensus probability.
    Returns {"consensus_prob": float, "consensus_edge": float, "source": str}.
    """
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


# ── AI Sentiment (Claude API) ────────────────────────────────────────────

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


def get_sentiment(
    ticker: str, title: str, category: str, market_price: float
) -> dict:
    """Combined sentiment: consensus + AI estimate."""
    consensus = get_consensus_edge(ticker, market_price)
    ai = get_ai_sentiment(ticker, title, category, market_price)
    return {
        **consensus,
        **ai,
        "sentiment_edge": round(
            _weighted_avg(consensus["consensus_edge"], ai["ai_edge"]),
            4,
        ),
    }


def _weighted_avg(consensus_edge: float, ai_edge: float) -> float:
    """Weighted average of consensus and AI edges."""
    has_consensus = abs(consensus_edge) > 0.001
    has_ai = abs(ai_edge) > 0.001
    if has_consensus and has_ai:
        return consensus_edge * 0.6 + ai_edge * 0.4
    elif has_consensus:
        return consensus_edge
    elif has_ai:
        return ai_edge
    return 0.0
