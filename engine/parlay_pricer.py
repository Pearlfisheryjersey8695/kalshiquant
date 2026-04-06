"""
Parlay Decomposition Engine — The SIG Sports Playbook

Kalshi's most liquid markets are multi-leg parlays (KXMVESPORTSMULTIGAME,
KXMVECROSSCATEGORY). These are priced by retail bettors who don't
decompose the legs. We do.

Strategy:
  1. For each parlay, fetch the individual leg market prices from Kalshi
  2. Compute true probability: P(parlay) = P(leg1) × P(leg2) × ... × P(legN)
     (assumes independence — games are independent events)
  3. Compare to market price
  4. If |edge| > fees → TRADE

The edge comes from:
  - Retail bettors overestimating parlays (excitement premium)
  - Compounding vig: each leg has 2-5% vig, a 4-leg parlay has ~15% total
  - Stale leg prices: one leg may have settled while parlay hasn't updated
"""

import logging
import math
import time
import threading
from collections import defaultdict
from datetime import datetime, timezone

logger = logging.getLogger("kalshi.parlay")


class ParlayPricer:
    """Decomposes parlays into legs, prices each leg, and finds mispricings."""

    def __init__(self, kalshi_client):
        self._client = kalshi_client
        self._leg_cache: dict = {}  # market_ticker -> {price, ts}
        self._cache_ttl = 120  # 2 min cache for leg prices
        self._lock = threading.Lock()
        self._last_scan_results: list = []

    def _get_leg_price(self, market_ticker: str, side: str) -> float:
        """Get the probability for a parlay leg. Uses cache to avoid rate limits."""
        cache_key = f"{market_ticker}:{side}"
        now = time.time()

        with self._lock:
            cached = self._leg_cache.get(cache_key)
            if cached and (now - cached["ts"]) < self._cache_ttl:
                return cached["prob"]

        # Fetch from Kalshi API
        try:
            resp = self._client.get(f"/trade-api/v2/markets/{market_ticker}")
            m = resp.get("market", resp)
            yes_price = float(m.get("yes_price_dollars", m.get("last_price_dollars", 0)))

            if side == "yes":
                prob = yes_price if yes_price > 0 else 0.5
            else:
                prob = (1 - yes_price) if yes_price > 0 else 0.5

            # Clamp to avoid 0 or 1 (which would zero out the parlay)
            prob = max(0.01, min(0.99, prob))

            with self._lock:
                self._leg_cache[cache_key] = {"prob": prob, "ts": now}

            return prob

        except Exception as e:
            logger.debug("Leg price fetch failed for %s: %s", market_ticker, e)
            return 0.5  # default to coin flip if we can't price it

    def price_parlay(self, parlay_market: dict) -> dict | None:
        """
        Price a single parlay by decomposing into legs.

        Args:
            parlay_market: dict with 'ticker', 'price', 'volume', and either
                          'mve_selected_legs' or we fetch it from the API.

        Returns:
            dict with fair_value, edge, net_edge, legs, tradeable flag
        """
        ticker = parlay_market.get("ticker", "")
        market_price = float(parlay_market.get("price", 0))

        if market_price <= 0:
            return None

        # Get leg structure
        legs = parlay_market.get("mve_selected_legs")
        if not legs:
            # Fetch from API
            try:
                resp = self._client.get(f"/trade-api/v2/markets/{ticker}")
                m = resp.get("market", resp)
                legs = m.get("mve_selected_legs", [])
                time.sleep(0.3)
            except Exception:
                return None

        if not legs or len(legs) < 2:
            return None

        # Price each leg
        fair_value = 1.0
        leg_details = []
        legs_fetched = 0
        max_legs_to_fetch = 6  # rate limit protection

        for leg in legs:
            leg_ticker = leg.get("market_ticker", "")
            side = leg.get("side", "yes")

            if legs_fetched < max_legs_to_fetch:
                prob = self._get_leg_price(leg_ticker, side)
                legs_fetched += 1
                time.sleep(0.15)  # rate limit
            else:
                prob = 0.5  # estimate unfetched legs at 50%

            fair_value *= prob
            leg_details.append({
                "ticker": leg_ticker,
                "side": side,
                "prob": round(prob, 4),
                "fetched": legs_fetched <= max_legs_to_fetch,
            })

        # Compute edge
        edge = fair_value - market_price
        direction = "BUY_YES" if edge > 0 else "BUY_NO"

        # Kalshi fee
        fee_per_side = math.ceil(0.07 * market_price * (1 - market_price) * 100) / 100
        fee_rt = fee_per_side * 2
        net_edge = abs(edge) - fee_rt

        # Is it tradeable?
        tradeable = net_edge > 0.005 and abs(edge) > 0.02

        return {
            "ticker": ticker,
            "market_price": round(market_price, 4),
            "fair_value": round(fair_value, 4),
            "edge": round(edge, 4),
            "net_edge": round(net_edge, 4),
            "fee_rt": round(fee_rt, 4),
            "direction": direction,
            "tradeable": tradeable,
            "n_legs": len(legs),
            "legs_fetched": legs_fetched,
            "legs": leg_details,
            "volume": parlay_market.get("volume", 0),
            "edge_pct": round(abs(edge) / market_price * 100, 1) if market_price > 0 else 0,
        }

    def scan_all_parlays(self, state) -> list:
        """
        Scan all tracked parlay markets for mispricings.
        Called from the scheduler or on-demand via API.

        Args:
            state: MarketStateStore with live prices

        Returns:
            List of tradeable parlay signals, sorted by net_edge
        """
        all_markets = state.get_all_markets()
        parlays = [
            m for m in all_markets
            if "KXMVE" in m.get("ticker", "")
            and m.get("price", 0) > 0
            and m.get("volume", 0) > 50
        ]

        logger.info("Scanning %d parlays for mispricings...", len(parlays))
        results = []

        for m in parlays[:30]:  # scan top 30 by volume to avoid rate limits
            try:
                result = self.price_parlay(m)
                if result and result["tradeable"]:
                    results.append(result)
            except Exception as e:
                logger.debug("Parlay scan error for %s: %s", m.get("ticker", ""), e)

        results.sort(key=lambda x: x["net_edge"], reverse=True)
        self._last_scan_results = results

        logger.info("Parlay scan complete: %d tradeable of %d scanned",
                    len(results), min(len(parlays), 30))
        return results

    def get_last_scan(self) -> list:
        """Get cached scan results."""
        return self._last_scan_results

    def generate_signals(self, state) -> list:
        """
        Generate trading signals from parlay mispricings.
        Returns signals in the same format as the ensemble signal generator.
        """
        scan = self.scan_all_parlays(state)
        signals = []

        for result in scan[:10]:  # top 10 opportunities
            # Build signal in ensemble format
            price = result["market_price"]
            signal = {
                "ticker": result["ticker"],
                "title": f"Parlay ({result['n_legs']} legs)",
                "category": "Sports",
                "current_price": price,
                "fair_value": result["fair_value"],
                "edge": result["edge"],
                "net_edge": result["net_edge"],
                "fee_impact": result["fee_rt"],
                "direction": result["direction"],
                "confidence": min(abs(result["edge"]) / 0.10, 1.0),  # scale confidence
                "regime": "CONVERGENCE",  # parlays converge to settlement
                "strategy": "parlay_arb",
                "price_prediction_1h": 0,
                "prediction_confidence": 0,
                "recommended_contracts": self._size_parlay(result),
                "risk": {
                    "kelly_fraction": 0.01,
                    "size_dollars": 0,
                    "contracts": 0,
                    "stop_loss": max(price * 0.7, 0.01) if result["direction"] == "BUY_YES" else min(price * 1.3, 0.99),
                    "take_profit": result["fair_value"],
                    "true_max_loss": 0,
                    "stop_loss_amount": 0,
                    "max_gain": 0,
                    "risk_reward": 0,
                    "net_edge": result["net_edge"],
                    "fee_impact": result["fee_rt"],
                    "total_fees": 0,
                },
                "hedge": None,
                "reasons": [
                    f"Parlay decomposition: {result['n_legs']} legs, {result['legs_fetched']} priced",
                    f"Fair value {result['fair_value']:.4f} vs market {price:.4f}",
                    f"Edge: {result['edge']:+.4f} ({result['edge_pct']:.0f}%)",
                    f"Net edge after fees: {result['net_edge']:+.4f}",
                ],
                "volume": result.get("volume", 0),
                "open_interest": 0,
                "tradability_score": 50,
                "expiration_time": None,
                "meta_quality": min(abs(result["net_edge"]) * 5, 1.0),
                "sentiment_edge": 0,
                "_signal_source": "parlay_pricer",
                "_parlay_data": result,
            }
            signals.append(signal)

        return signals

    def _size_parlay(self, result: dict) -> int:
        """Conservative sizing for parlay trades."""
        price = result["market_price"]
        net_edge = result["net_edge"]

        # Cost per contract
        if result["direction"] == "BUY_NO":
            cost = 1.0 - price
        else:
            cost = price

        if cost <= 0:
            return 0

        # Max $50 per parlay trade (conservative — these are complex)
        max_dollars = 50
        contracts = int(max_dollars / cost)

        # Scale by edge confidence
        if net_edge < 0.05:
            contracts = max(1, contracts // 3)
        elif net_edge < 0.10:
            contracts = max(1, contracts // 2)

        return min(contracts, 200)  # cap at 200 contracts
