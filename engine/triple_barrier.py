"""Triple-barrier exit method (López de Prado, AFML chapter 3).

Why this exists
---------------
Fixed-percent stop-losses and take-profits are the most common bug in retail
trading code. A 15% stop on a 5%-vol market exits before noise is exhausted; the
same 15% stop on a 40%-vol market gets crushed in a single bar. Both errors are
the same root cause: the stop is not scaled to the market's actual volatility.

The triple-barrier method (Marcos López de Prado, *Advances in Financial Machine
Learning*, ch. 3) replaces fixed levels with three barriers that adapt:

  1. **Upper barrier** (take-profit): entry + ``pt_mult * sigma``
  2. **Lower barrier** (stop-loss):    entry - ``sl_mult * sigma``
  3. **Vertical barrier** (time):      ``max_hold`` minutes after entry

Whichever barrier is touched FIRST determines the exit. ``sigma`` is the rolling
realized volatility (price units, not percent), so the same multiplier produces
a tight stop on calm markets and a wide stop on noisy ones.

For binary contracts in [0, 1] price space we additionally clip the barriers to
[0.01, 0.99] so we don't generate barriers off the contract boundaries.

This module is pure math — it doesn't know about positions, the state store, or
the execution engine. The execution engine constructs a TripleBarrier per
position and asks ``check_touch()`` each cycle.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class BarrierTouch(str, Enum):
    NONE = "none"
    UPPER = "upper"     # take-profit hit
    LOWER = "lower"     # stop-loss hit
    VERTICAL = "vertical"  # time barrier hit


@dataclass(frozen=True)
class TripleBarrier:
    """Volatility-scaled triple-barrier exit specification.

    Parameters
    ----------
    entry_price : float
        Entry price in [0, 1] for Kalshi binaries.
    sigma : float
        Realized volatility in PRICE units (not percent). e.g. 0.04 means a
        typical 1-period move is 4 cents.
    pt_mult : float
        Take-profit barrier in sigma units. 2.0 = 2σ above entry.
    sl_mult : float
        Stop-loss barrier in sigma units. 1.0 = 1σ below entry.
    max_hold_minutes : float
        Vertical (time) barrier in minutes from entry.
    direction : str
        "BUY_YES" or "BUY_NO". For BUY_NO the upper/lower interpretation flips:
        the trade profits when price goes DOWN, so the take-profit barrier is
        BELOW entry and the stop is ABOVE.
    """

    entry_price: float
    sigma: float
    pt_mult: float
    sl_mult: float
    max_hold_minutes: float
    direction: str = "BUY_YES"

    @property
    def upper_barrier(self) -> float:
        """Price level of the take-profit / stop barrier above entry."""
        if self.direction == "BUY_YES":
            return min(0.99, self.entry_price + self.pt_mult * self.sigma)
        # For BUY_NO, the level above entry is a STOP
        return min(0.99, self.entry_price + self.sl_mult * self.sigma)

    @property
    def lower_barrier(self) -> float:
        """Price level of the take-profit / stop barrier below entry."""
        if self.direction == "BUY_YES":
            return max(0.01, self.entry_price - self.sl_mult * self.sigma)
        # For BUY_NO, the level below entry is a TAKE-PROFIT
        return max(0.01, self.entry_price - self.pt_mult * self.sigma)

    def check_touch(self, current_price: float, elapsed_minutes: float) -> BarrierTouch:
        """Return which barrier (if any) has been touched.

        Order of evaluation matters: if multiple barriers are crossed in the
        same observation we report the most adverse one (stop > take-profit >
        time) — this is the conservative AFML convention.
        """
        # Check stop side first (most adverse)
        if self.direction == "BUY_YES":
            if current_price <= self.lower_barrier:
                return BarrierTouch.LOWER  # stop hit (loss)
            if current_price >= self.upper_barrier:
                return BarrierTouch.UPPER  # take-profit hit
        else:  # BUY_NO
            if current_price >= self.upper_barrier:
                return BarrierTouch.UPPER  # stop hit (loss)
            if current_price <= self.lower_barrier:
                return BarrierTouch.LOWER  # take-profit hit

        # Time barrier — only after price barriers are clear
        if elapsed_minutes >= self.max_hold_minutes:
            return BarrierTouch.VERTICAL
        return BarrierTouch.NONE

    def is_profit_touch(self, touch: BarrierTouch) -> bool:
        """Whether a given touch represents a take-profit (vs stop or time-out)."""
        if self.direction == "BUY_YES":
            return touch == BarrierTouch.UPPER
        return touch == BarrierTouch.LOWER

    def is_loss_touch(self, touch: BarrierTouch) -> bool:
        if self.direction == "BUY_YES":
            return touch == BarrierTouch.LOWER
        return touch == BarrierTouch.UPPER

    def reason_string(self, touch: BarrierTouch) -> str:
        """Human-readable exit reason for logging / journal."""
        if touch == BarrierTouch.NONE:
            return ""
        if self.is_profit_touch(touch):
            return f"TRIPLE_BARRIER_TP ({self.pt_mult}σ)"
        if self.is_loss_touch(touch):
            return f"TRIPLE_BARRIER_SL ({self.sl_mult}σ)"
        return f"TRIPLE_BARRIER_TIME ({self.max_hold_minutes:.0f}min)"


def realized_vol_from_prices(prices: list[float], min_samples: int = 5) -> float:
    """Sample realized vol in PRICE units (not percent) from a price series.

    Used by the execution engine to feed `sigma` into TripleBarrier. Falls back
    to a conservative 0.02 (= 2c per period) if there aren't enough samples.
    """
    if len(prices) < min_samples:
        return 0.02
    diffs = [prices[i] - prices[i - 1] for i in range(1, len(prices))]
    if not diffs:
        return 0.02
    mean = sum(diffs) / len(diffs)
    var = sum((d - mean) ** 2 for d in diffs) / len(diffs)
    sigma = var ** 0.5
    # Floor at 0.5c to avoid degenerate barriers on dead markets
    return max(sigma, 0.005)
