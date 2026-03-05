"""
Orderbook reconstruction from Kalshi WebSocket snapshots + deltas.
Maintains per-market yes/no books (price_cents -> quantity).
"""

import logging

logger = logging.getLogger("kalshi.orderbook")


class Orderbook:
    """Single market's orderbook state."""

    def __init__(self, ticker: str):
        self.ticker = ticker
        self.yes: dict[int, int] = {}   # price_cents -> qty
        self.no: dict[int, int] = {}    # price_cents -> qty
        self._seq: int = -1
        self._has_snapshot = False

    def apply_snapshot(self, msg: dict, seq: int) -> None:
        """Replace entire book from orderbook_snapshot message."""
        self.yes = {}
        self.no = {}
        for price, qty in msg.get("yes", []):
            if qty > 0:
                self.yes[price] = qty
        for price, qty in msg.get("no", []):
            if qty > 0:
                self.no[price] = qty
        self._seq = seq
        self._has_snapshot = True

    def apply_delta(self, msg: dict, seq: int) -> bool:
        """
        Apply incremental orderbook_delta.
        Returns False if seq gap detected (caller should resubscribe).
        """
        if not self._has_snapshot:
            return False

        if self._seq >= 0 and seq != self._seq + 1:
            logger.warning("%s: seq gap (%d -> %d)", self.ticker, self._seq, seq)
            self._has_snapshot = False
            return False

        self._seq = seq
        price = msg.get("price", 0)
        delta = msg.get("delta", 0)
        side = msg.get("side", "yes")

        book = self.yes if side == "yes" else self.no
        current = book.get(price, 0)
        new_qty = current + delta

        if new_qty <= 0:
            book.pop(price, None)
        else:
            book[price] = new_qty

        return True

    def get_mid_price_cents(self) -> float:
        """Best bid + best ask / 2 on yes side."""
        if not self.yes:
            return 50.0
        sorted_prices = sorted(self.yes.keys())
        # Best bid = highest price with qty, best ask = lowest no-side
        # Simplification: use yes-side spread
        best_bid = sorted_prices[-1] if sorted_prices else 50
        # Best ask from no side: 100 - best_no_bid
        if self.no:
            best_no_bid = max(self.no.keys())
            best_ask = 100 - best_no_bid
        else:
            best_ask = best_bid + 1
        return (best_bid + best_ask) / 2.0

    def get_imbalance(self, depth_cents: int = 5) -> float:
        """
        Bid/ask imbalance within depth_cents of mid.
        Returns [-1, 1]: positive = more bids, negative = more asks.
        """
        mid = self.get_mid_price_cents()
        bid_qty = sum(
            qty for price, qty in self.yes.items()
            if price <= mid and abs(price - mid) <= depth_cents
        )
        # Ask side: no-side bids near no_mid, or yes prices above mid
        ask_qty = sum(
            qty for price, qty in self.yes.items()
            if price > mid and abs(price - mid) <= depth_cents
        )
        no_mid = 100 - mid
        ask_qty += sum(
            qty for price, qty in self.no.items()
            if abs(price - no_mid) <= depth_cents
        )

        total = bid_qty + ask_qty
        if total == 0:
            return 0.0
        return (bid_qty - ask_qty) / total

    def get_vpin(self, trades: list[dict], window: int = 50) -> float:
        """Volume-synchronized probability of informed trading.
        Buckets recent trades by tick rule (uptick = buy).
        VPIN = |buy_vol - sell_vol| / total_vol.
        High VPIN = informed traders active = price about to move.
        """
        if len(trades) < 2:
            return 0.0
        recent = trades[-window:]
        buy_vol, sell_vol = 0.0, 0.0
        for i, t in enumerate(recent):
            vol = t.get("count", 1)
            if i == 0:
                buy_vol += vol
                continue
            prev_price = recent[i - 1].get("yes_price", 50)
            curr_price = t.get("yes_price", 50)
            if curr_price > prev_price:
                buy_vol += vol
            elif curr_price < prev_price:
                sell_vol += vol
            else:
                buy_vol += vol / 2
                sell_vol += vol / 2
        total = buy_vol + sell_vol
        if total == 0:
            return 0.0
        return abs(buy_vol - sell_vol) / total

    def get_book_pressure(self, depth_cents: int = 10) -> float:
        """Ratio of bid qty within depth_cents of mid vs ask qty.
        Returns [-1, 1]: positive = more bids (bullish).
        Captures deeper liquidity asymmetry than get_imbalance(5).
        """
        mid = self.get_mid_price_cents()
        bid_qty = sum(
            qty for price, qty in self.yes.items()
            if price <= mid and mid - price <= depth_cents
        )
        ask_qty = sum(
            qty for price, qty in self.yes.items()
            if price > mid and price - mid <= depth_cents
        )
        no_mid = 100 - mid
        ask_qty += sum(
            qty for price, qty in self.no.items()
            if abs(price - no_mid) <= depth_cents
        )
        total = bid_qty + ask_qty
        if total == 0:
            return 0.0
        return (bid_qty - ask_qty) / total

    def walk_book(self, contracts: int, side: str = "buy_yes") -> dict:
        """Walk through orderbook filling contracts at each price level.
        Returns avg fill price, total filled, levels consumed, slippage.
        """
        if side == "buy_yes":
            asks = sorted(self.yes.items(), key=lambda x: x[0])
        elif side == "buy_no":
            asks = sorted(self.no.items(), key=lambda x: x[0])
        else:
            return {"avg_fill_cents": self.get_mid_price_cents(),
                    "filled": 0, "slippage_cents": 0, "levels": 0}

        mid = self.get_mid_price_cents()
        filled = 0
        cost = 0.0
        levels = 0

        for price, qty in asks:
            take = min(qty, contracts - filled)
            cost += take * price
            filled += take
            levels += 1
            if filled >= contracts:
                break

        if filled == 0:
            return {"avg_fill_cents": mid, "filled": 0,
                    "slippage_cents": 0, "levels": 0}

        avg_fill = cost / filled
        slippage = avg_fill - mid

        return {
            "avg_fill_cents": round(avg_fill, 2),
            "filled": filled,
            "slippage_cents": round(slippage, 2),
            "levels": levels,
        }

    @property
    def has_data(self) -> bool:
        return self._has_snapshot


class OrderbookStore:
    """Collection of per-market orderbooks."""

    def __init__(self):
        self._books: dict[str, Orderbook] = {}

    def get_or_create(self, ticker: str) -> Orderbook:
        if ticker not in self._books:
            self._books[ticker] = Orderbook(ticker)
        return self._books[ticker]

    def get(self, ticker: str) -> Orderbook | None:
        return self._books.get(ticker)

    def needs_resubscribe(self) -> list[str]:
        """Return tickers that lost their snapshot and need resubscription."""
        return [t for t, ob in self._books.items() if not ob.has_data]

    def invalidate_all(self) -> None:
        """Mark all orderbooks as stale (e.g. after WS disconnect)."""
        for ob in self._books.values():
            ob._has_snapshot = False

    def all_tickers(self) -> list[str]:
        return list(self._books.keys())
