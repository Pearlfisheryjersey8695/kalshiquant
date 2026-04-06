import os
import time
import json
import uuid
import base64
import logging
import requests
from dotenv import load_dotenv
from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.primitives.asymmetric import padding

logger = logging.getLogger("kalshi")


class KalshiClient:
    def __init__(self, env_file=None):
        load_dotenv(env_file)

        api_key = os.getenv("KALSHI_API_KEY")
        base_url = os.getenv("KALSHI_BASE_URL")
        key_path_env = os.getenv("PRIVATE_KEY_PATH")

        if not api_key:
            raise ValueError("KALSHI_API_KEY missing in .env")
        if not base_url:
            raise ValueError("KALSHI_BASE_URL missing in .env")
        if not key_path_env:
            raise ValueError("PRIVATE_KEY_PATH missing in .env")

        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        key_path = os.path.join(project_root, key_path_env)

        if not os.path.exists(key_path):
            raise FileNotFoundError(f"Private key not found at: {key_path}")

        with open(key_path, "rb") as f:
            self.private_key = serialization.load_pem_private_key(
                f.read(),
                password=None,
            )

        self.api_key = api_key
        self.base_url = base_url.rstrip("/")

    # ---------- SIGNING ----------
    def _sign(self, method: str, path: str, body: str = ""):
        timestamp = str(int(time.time() * 1000))
        message = f"{timestamp}{method.upper()}{path}{body}".encode("utf-8")
        signature = self.private_key.sign(
            message,
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.DIGEST_LENGTH,
            ),
            hashes.SHA256(),
        )
        return timestamp, base64.b64encode(signature).decode("utf-8")

    # ---------- HEADERS ----------
    def _headers(self, method: str, path: str, body: str = ""):
        ts, sig = self._sign(method, path, body)
        return {
            "Content-Type": "application/json",
            "KALSHI-ACCESS-KEY": self.api_key,
            "KALSHI-ACCESS-TIMESTAMP": ts,
            "KALSHI-ACCESS-SIGNATURE": sig,
        }

    # ---------- GENERIC REQUESTS ----------
    def get(self, path: str, params=None):
        # Signature must include the full path with query string
        if params:
            from urllib.parse import urlencode
            sign_path = f"{path}?{urlencode(params)}"
        else:
            sign_path = path
        headers = self._headers("GET", sign_path)
        r = requests.get(
            self.base_url + path,
            headers=headers,
            params=params,
            timeout=30,
        )
        if not r.ok:
            logger.error("GET %s -> %s: %s", path, r.status_code, r.text)
        r.raise_for_status()
        return r.json()

    def post(self, path: str, data: dict):
        body = json.dumps(data, separators=(",", ":"))
        headers = self._headers("POST", path, body)
        r = requests.post(
            self.base_url + path,
            headers=headers,
            data=body,
            timeout=30,
        )
        if not r.ok:
            logger.error("POST %s -> %s: %s", path, r.status_code, r.text)
        r.raise_for_status()
        return r.json()

    # ---------- PAGINATION HELPER ----------
    def paginate(self, path: str, key: str, params: dict | None = None, max_pages: int = 20):
        """Fetch all pages for a cursor-paginated endpoint and return combined list.
        Includes rate limiting to avoid 429 errors from Kalshi API.
        """
        import time as _time
        params = dict(params or {})
        results = []
        for page in range(max_pages):
            try:
                resp = self.get(path, params=params)
            except Exception as e:
                if "429" in str(e):
                    logger.warning("Rate limited at page %d, waiting 2s...", page)
                    _time.sleep(2)
                    try:
                        resp = self.get(path, params=params)
                    except Exception:
                        break
                else:
                    raise
            results.extend(resp.get(key, []))
            cursor = resp.get("cursor")
            if not cursor:
                break
            params["cursor"] = cursor
            # Rate limit: 0.3s between pages
            if page < max_pages - 1:
                _time.sleep(0.3)
        return results

    # ---------- PORTFOLIO ----------
    def get_balance(self):
        return self.get("/trade-api/v2/portfolio/balance")

    def get_positions(self):
        return self.get("/trade-api/v2/portfolio/positions")

    # ---------- MARKETS ----------
    def get_markets(self, limit: int = 100, cursor: str | None = None):
        params = {"limit": limit}
        if cursor:
            params["cursor"] = cursor
        return self.get("/trade-api/v2/markets", params=params)

    def get_all_markets(self, limit: int = 200, max_pages: int = 15) -> list:
        return self.paginate("/trade-api/v2/markets", "markets", {"limit": limit}, max_pages=max_pages)

    def get_market(self, ticker: str):
        return self.get(f"/trade-api/v2/markets/{ticker}")

    # ---------- ORDERBOOK ----------
    def get_orderbook(self, ticker: str, depth: int = 10):
        return self.get(
            f"/trade-api/v2/markets/{ticker}/orderbook",
            params={"depth": depth},
        )

    # ---------- TRADES ----------
    def get_trades(self, ticker: str, limit: int = 100, cursor: str | None = None):
        params = {"limit": limit, "ticker": ticker}
        if cursor:
            params["cursor"] = cursor
        return self.get("/trade-api/v2/markets/trades", params=params)

    # ---------- SERIES ----------
    def get_series(self, limit: int = 200, cursor: str | None = None):
        params = {"limit": limit}
        if cursor:
            params["cursor"] = cursor
        return self.get("/trade-api/v2/series", params=params)

    def get_all_series(self, limit: int = 200) -> list:
        return self.paginate("/trade-api/v2/series", "series", {"limit": limit})

    def get_markets_in_series(self, series_ticker: str, limit: int = 200) -> list:
        return self.paginate(
            "/trade-api/v2/markets", "markets",
            {"limit": limit, "series_ticker": series_ticker},
        )

    # ---------- ORDERS ----------
    def place_order(
        self,
        ticker: str,
        action: str,          # "buy" | "sell"
        side: str,            # "yes" | "no"
        count: int,
        order_type: str = "limit",
        yes_price: int | None = None,   # cents (1-99)
        no_price: int | None = None,    # cents (1-99)
    ) -> dict:
        """
        Place an order on Kalshi.

        Args:
            ticker:     Market ticker, e.g. "INXD-23DEC31-T4000".
            action:     "buy" or "sell".
            side:       "yes" or "no".
            count:      Number of contracts.
            order_type: "limit" or "market".
            yes_price:  Limit price in cents for YES side (required for limit orders).
            no_price:   Limit price in cents for NO side (required for limit orders).
        """
        if action not in ("buy", "sell"):
            raise ValueError("action must be 'buy' or 'sell'")
        if side not in ("yes", "no"):
            raise ValueError("side must be 'yes' or 'no'")
        if order_type == "limit" and yes_price is None and no_price is None:
            raise ValueError("limit orders require yes_price or no_price")

        payload: dict = {
            "ticker": ticker,
            "client_order_id": str(uuid.uuid4()),
            "type": order_type,
            "action": action,
            "side": side,
            "count": count,
        }
        if yes_price is not None:
            payload["yes_price"] = yes_price
        if no_price is not None:
            payload["no_price"] = no_price

        logger.info("Placing %s %s %s x%d @ yes=%s no=%s", order_type, action, ticker, count, yes_price, no_price)
        return self.post("/trade-api/v2/portfolio/orders", payload)

    def cancel_order(self, order_id: str) -> dict:
        return self.post(f"/trade-api/v2/portfolio/orders/{order_id}/cancel", {})

    def get_orders(self, ticker: str | None = None, limit: int = 100) -> dict:
        params: dict = {"limit": limit}
        if ticker:
            params["ticker"] = ticker
        return self.get("/trade-api/v2/portfolio/orders", params=params)
