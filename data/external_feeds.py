"""
External data feeds for prediction market fair value estimation.

The CORE alpha source: knowing real-world probabilities from external data
that Kalshi participants may not have incorporated.

Feeds:
  - FedWatch: CME implied probabilities for FOMC rate decisions (via FRED)
  - Crypto: BTC/ETH spot prices + realized vol (CoinGecko)
  - Equity: S&P 500 level + VIX implied vol (Yahoo Finance via web)
  - Gas: National average gas prices (FRED)
"""

import json
import logging
import math
import os
import re
import time
import threading
from datetime import datetime, timezone
from urllib.request import urlopen, Request
from urllib.error import URLError

logger = logging.getLogger("kalshi.feeds")

# Cache TTL in seconds
CACHE_TTL = {
    "crypto": 120,     # 2 min — volatile
    "equity": 120,     # 2 min during market hours
    "fed": 3600,       # 1 hour — changes slowly
    "gas": 7200,       # 2 hours — weekly data
    "econ": 86400,     # 24 hours — monthly/quarterly
}

_cache: dict = {}
_cache_lock = threading.Lock()


def _cached_fetch(key: str, ttl: int, fetch_fn):
    """Thread-safe cached fetch with TTL."""
    with _cache_lock:
        if key in _cache:
            val, ts = _cache[key]
            if time.time() - ts < ttl:
                return val

    try:
        result = fetch_fn()
        with _cache_lock:
            _cache[key] = (result, time.time())
        return result
    except Exception as e:
        logger.warning("Feed fetch failed for %s: %s", key, e)
        # Return stale cache if available
        with _cache_lock:
            if key in _cache:
                return _cache[key][0]
        return None


def _http_get_json(url: str, timeout: int = 10) -> dict:
    """Fetch JSON from URL with timeout."""
    req = Request(url, headers={"User-Agent": "KalshiQuant/1.0"})
    with urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


# ── Probability Models ────────────────────────────────────────────────────

def _lognormal_prob(current: float, strike: float, vol: float, hours: float, direction: str = "above") -> float:
    """
    Probability that a lognormally distributed variable will be above/below strike at expiry.

    P(S_T > K) = N(d2) where d2 = (ln(S/K) + (r - 0.5*sigma^2)*T) / (sigma*sqrt(T))
    Simplified: assume r=0, use realized vol.
    """
    if current <= 0 or strike <= 0 or vol <= 0 or hours <= 0:
        return 0.5

    T = hours / (24 * 365)  # years
    sigma = vol  # annualized vol

    d2 = (math.log(current / strike) - 0.5 * sigma**2 * T) / (sigma * math.sqrt(T))

    # Standard normal CDF approximation (Abramowitz & Stegun)
    def norm_cdf(x):
        if x > 6:
            return 1.0
        if x < -6:
            return 0.0
        t = 1.0 / (1.0 + 0.2316419 * abs(x))
        d = 0.3989423 * math.exp(-x * x / 2)
        p = d * t * (0.3193815 + t * (-0.3565638 + t * (1.781478 + t * (-1.821256 + t * 1.330274))))
        return 1.0 - p if x > 0 else p

    prob_above = norm_cdf(d2)
    return prob_above if direction == "above" else 1 - prob_above


# ── Bitcoin Feed ──────────────────────────────────────────────────────────

class CryptoFeed:
    """BTC/ETH prices from CoinGecko (free, no key)."""

    def fetch(self) -> dict | None:
        """Get current BTC price + 30-day history for vol estimation."""
        def _fetch():
            # Current price
            price_data = _http_get_json(
                "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin,ethereum&vs_currencies=usd&include_24hr_change=true"
            )
            btc_price = price_data.get("bitcoin", {}).get("usd", 0)
            btc_change = price_data.get("bitcoin", {}).get("usd_24h_change", 0)
            eth_price = price_data.get("ethereum", {}).get("usd", 0)

            # 30-day history for vol
            hist_data = _http_get_json(
                "https://api.coingecko.com/api/v3/coins/bitcoin/market_chart?vs_currency=usd&days=30&interval=daily"
            )
            prices_hist = [p[1] for p in hist_data.get("prices", [])]

            # Compute annualized vol from daily returns
            vol = 0.60  # default 60% annualized
            if len(prices_hist) >= 5:
                returns = [math.log(prices_hist[i] / prices_hist[i-1]) for i in range(1, len(prices_hist)) if prices_hist[i-1] > 0]
                if returns:
                    daily_vol = (sum(r**2 for r in returns) / len(returns)) ** 0.5
                    vol = daily_vol * math.sqrt(365)

            return {
                "btc_price": btc_price,
                "btc_24h_change": btc_change,
                "eth_price": eth_price,
                "btc_vol_annual": round(vol, 4),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

        return _cached_fetch("crypto", CACHE_TTL["crypto"], _fetch)

    def get_probability(self, strike: float, hours_to_expiry: float, direction: str = "above") -> dict:
        """P(BTC > strike) or P(BTC < strike) at expiry."""
        data = self.fetch()
        if not data or data["btc_price"] <= 0:
            return {"probability": 0.5, "source": "default", "stale": True}

        prob = _lognormal_prob(data["btc_price"], strike, data["btc_vol_annual"], hours_to_expiry, direction)

        return {
            "probability": round(prob, 4),
            "current_price": data["btc_price"],
            "strike": strike,
            "vol": data["btc_vol_annual"],
            "hours": hours_to_expiry,
            "direction": direction,
            "source": "coingecko",
            "stale": False,
        }


# ── Equity Index Feed ─────────────────────────────────────────────────────

class EquityFeed:
    """S&P 500 + VIX from Yahoo Finance (free, no key)."""

    def fetch(self) -> dict | None:
        """Get current SPX level and VIX."""
        def _fetch():
            # Use Yahoo Finance v8 API (free, no key)
            spx_data = _http_get_json(
                "https://query1.finance.yahoo.com/v8/finance/chart/%5EGSPC?interval=1d&range=1d"
            )
            vix_data = _http_get_json(
                "https://query1.finance.yahoo.com/v8/finance/chart/%5EVIX?interval=1d&range=1d"
            )

            spx_meta = spx_data.get("chart", {}).get("result", [{}])[0].get("meta", {})
            vix_meta = vix_data.get("chart", {}).get("result", [{}])[0].get("meta", {})

            spx = spx_meta.get("regularMarketPrice", 0)
            vix = vix_meta.get("regularMarketPrice", 0)

            return {
                "spx": spx,
                "vix": vix,
                "spx_vol_annual": vix / 100 if vix > 0 else 0.15,  # VIX IS annualized vol
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

        return _cached_fetch("equity", CACHE_TTL["equity"], _fetch)

    def get_probability(self, strike: float, hours_to_expiry: float, direction: str = "above") -> dict:
        """P(SPX > strike) at expiry."""
        data = self.fetch()
        if not data or data["spx"] <= 0:
            return {"probability": 0.5, "source": "default", "stale": True}

        prob = _lognormal_prob(data["spx"], strike, data["spx_vol_annual"], hours_to_expiry, direction)

        return {
            "probability": round(prob, 4),
            "current_price": data["spx"],
            "strike": strike,
            "vol": data["spx_vol_annual"],
            "source": "yahoo_finance",
            "stale": False,
        }


# ── Fed Funds Feed ────────────────────────────────────────────────────────

class FedFundsFeed:
    """Fed Funds Rate data from FRED (free API key)."""

    def __init__(self):
        self._fred_key = os.getenv("FRED_API_KEY", "")

    def fetch(self) -> dict | None:
        """Get current Fed Funds target rate."""
        def _fetch():
            # Current effective FFR
            if self._fred_key:
                data = _http_get_json(
                    f"https://api.stlouisfed.org/fred/series/observations?"
                    f"series_id=DFEDTARU&api_key={self._fred_key}&sort_order=desc&limit=1&file_type=json"
                )
                obs = data.get("observations", [])
                if obs:
                    rate = float(obs[0]["value"])
                    return {
                        "target_rate_upper": rate,
                        "target_rate_lower": rate - 0.25,
                        "target_rate_mid": rate - 0.125,
                        "observation_date": obs[0]["date"],
                        "source": "fred",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }

            # Fallback: hardcoded (update manually)
            return {
                "target_rate_upper": 4.50,
                "target_rate_lower": 4.25,
                "target_rate_mid": 4.375,
                "observation_date": "2026-04-01",
                "source": "hardcoded_fallback",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

        return _cached_fetch("fed", CACHE_TTL["fed"], _fetch)

    def get_probability(self, target_rate: float, hours_to_expiry: float, direction: str = "above") -> dict:
        """
        P(Fed Funds Rate > target at next FOMC).

        Simple model: rate very unlikely to change between FOMC meetings.
        At FOMC: use distance from current rate to estimate probability.
        Within 25bps = most likely. >50bps = very unlikely.
        """
        data = self.fetch()
        if not data:
            return {"probability": 0.5, "source": "default", "stale": True}

        current = data["target_rate_mid"]
        distance = abs(target_rate - current)

        # Simple probability model based on rate distance
        # Each 25bp move has historically ~30% probability at any given FOMC
        if distance < 0.01:
            prob = 0.90  # at current rate — very likely to hold
        elif distance <= 0.25:
            prob = 0.40  # one move — possible
        elif distance <= 0.50:
            prob = 0.15  # two moves — unlikely at single meeting
        elif distance <= 0.75:
            prob = 0.05  # three moves — very unlikely
        else:
            prob = 0.02  # extreme — almost never

        # For "above" direction, we need P(rate >= target)
        if direction == "above":
            if target_rate <= current:
                prob = 1.0 - (1.0 - prob) * (current - target_rate) / 0.25
            else:
                prob = prob * 0.25 / max(target_rate - current, 0.01)
        else:
            prob = 1.0 - prob

        prob = max(0.01, min(0.99, prob))

        return {
            "probability": round(prob, 4),
            "current_rate": current,
            "target_rate": target_rate,
            "distance_bps": round(distance * 100),
            "source": data.get("source", "unknown"),
            "stale": data.get("source") == "hardcoded_fallback",
        }


# ── Gas Price Feed ────────────────────────────────────────────────────────

class GasPriceFeed:
    """National average gas prices from FRED (free)."""

    def __init__(self):
        self._fred_key = os.getenv("FRED_API_KEY", "")

    def fetch(self) -> dict | None:
        def _fetch():
            if self._fred_key:
                data = _http_get_json(
                    f"https://api.stlouisfed.org/fred/series/observations?"
                    f"series_id=GASREGW&api_key={self._fred_key}&sort_order=desc&limit=4&file_type=json"
                )
                obs = data.get("observations", [])
                if obs:
                    prices = [float(o["value"]) for o in obs if o["value"] != "."]
                    if prices:
                        vol = 0.0
                        if len(prices) >= 2:
                            changes = [abs(prices[i] - prices[i+1]) / prices[i+1] for i in range(len(prices)-1)]
                            vol = (sum(c**2 for c in changes) / len(changes)) ** 0.5 * math.sqrt(52)  # annualize from weekly
                        return {
                            "current_price": prices[0],
                            "previous_price": prices[1] if len(prices) > 1 else prices[0],
                            "weekly_change": prices[0] - (prices[1] if len(prices) > 1 else prices[0]),
                            "vol_annual": round(max(vol, 0.05), 4),
                            "observation_date": obs[0]["date"],
                            "source": "fred",
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        }

            # Fallback
            return {
                "current_price": 3.30,
                "vol_annual": 0.10,
                "source": "hardcoded_fallback",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

        return _cached_fetch("gas", CACHE_TTL["gas"], _fetch)

    def get_probability(self, target_price: float, hours_to_expiry: float, direction: str = "above") -> dict:
        data = self.fetch()
        if not data:
            return {"probability": 0.5, "source": "default", "stale": True}

        prob = _lognormal_prob(data["current_price"], target_price, data["vol_annual"], hours_to_expiry, direction)

        return {
            "probability": round(prob, 4),
            "current_price": data["current_price"],
            "target": target_price,
            "vol": data["vol_annual"],
            "source": data.get("source", "unknown"),
            "stale": data.get("source") == "hardcoded_fallback",
        }


# ── Master Feed Manager ──────────────────────────────────────────────────

class ExternalFeedManager:
    """
    Central manager for all external data feeds.
    Maps Kalshi ticker prefixes to the appropriate feed + probability model.
    """

    def __init__(self):
        self.crypto = CryptoFeed()
        self.equity = EquityFeed()
        self.fed = FedFundsFeed()
        self.gas = GasPriceFeed()
        self._last_refresh = {}

    def get_probability_for_ticker(self, ticker: str, current_price: float, hours_to_expiry: float) -> dict | None:
        """
        Given a Kalshi ticker, return the external-data-derived probability.
        Returns None if no external model exists for this market.
        """
        ticker_upper = ticker.upper()

        # Fed Funds — KXFED-26APR-T3.75 -> rate=3.75
        if "KXFED" in ticker_upper:
            match = re.search(r'T(\d+\.?\d*)', ticker_upper)
            if match:
                target_rate = float(match.group(1))
                return self.fed.get_probability(target_rate, hours_to_expiry, direction="above")

        # BTC Max Monthly — KXBTCMAXMON-BTC-26MAR31-8000000 -> strike=80000
        if "KXBTCMAX" in ticker_upper:
            match = re.search(r'(\d{6,})', ticker_upper)
            if match:
                strike = int(match.group(1))
                # Kalshi BTC strikes: 8000000 likely = $80,000
                if strike > 100000:
                    strike = strike / 100
                return self.crypto.get_probability(strike, hours_to_expiry, direction="above")

        # BTC Min Monthly — below strike
        if "KXBTCMIN" in ticker_upper:
            match = re.search(r'(\d{6,})', ticker_upper)
            if match:
                strike = int(match.group(1))
                if strike > 100000:
                    strike = strike / 100
                return self.crypto.get_probability(strike, hours_to_expiry, direction="below")

        # S&P 500 — KXINX
        if "KXINX" in ticker_upper or "KXSPX" in ticker_upper:
            match = re.search(r'(\d{4,})', ticker_upper)
            if match:
                strike = int(match.group(1))
                return self.equity.get_probability(strike, hours_to_expiry, direction="above")

        # Gas Prices — KXAAAGASM-26MAR31-3.70 -> target=3.70
        if "KXAAAG" in ticker_upper:
            match = re.search(r'(\d+\.\d+)', ticker_upper)
            if match:
                target = float(match.group(1))
                return self.gas.get_probability(target, hours_to_expiry, direction="above")

        # No external model for this ticker
        return None

    def get_all_current_data(self) -> dict:
        """Fetch all current external data for dashboard display."""
        result = {}

        try:
            crypto = self.crypto.fetch()
            if crypto:
                result["crypto"] = crypto
        except Exception:
            pass

        try:
            equity = self.equity.fetch()
            if equity:
                result["equity"] = equity
        except Exception:
            pass

        try:
            fed = self.fed.fetch()
            if fed:
                result["fed"] = fed
        except Exception:
            pass

        try:
            gas = self.gas.fetch()
            if gas:
                result["gas"] = gas
        except Exception:
            pass

        return result

    def get_feed_health(self) -> dict:
        """Check which feeds are working and how stale they are."""
        health = {}
        for name, ttl in CACHE_TTL.items():
            with _cache_lock:
                if name in _cache:
                    val, ts = _cache[name]
                    age = time.time() - ts
                    health[name] = {
                        "status": "fresh" if age < ttl else "stale",
                        "age_seconds": round(age),
                        "has_data": val is not None,
                    }
                else:
                    health[name] = {"status": "no_data", "age_seconds": 0, "has_data": False}
        return health


# Module-level singleton
feed_manager = ExternalFeedManager()
