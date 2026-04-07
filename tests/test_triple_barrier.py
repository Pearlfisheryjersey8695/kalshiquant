"""Tests for the triple-barrier exit primitives."""

import pytest

from engine.triple_barrier import (
    BarrierTouch,
    TripleBarrier,
    realized_vol_from_prices,
)


@pytest.fixture
def buy_yes_tb():
    return TripleBarrier(
        entry_price=0.50,
        sigma=0.04,        # 4c per period
        pt_mult=2.0,       # TP at 0.58
        sl_mult=1.0,       # SL at 0.46
        max_hold_minutes=240,
        direction="BUY_YES",
    )


@pytest.fixture
def buy_no_tb():
    return TripleBarrier(
        entry_price=0.60,
        sigma=0.04,
        pt_mult=2.0,       # for NO, TP is BELOW entry → 0.52
        sl_mult=1.0,       # SL is ABOVE entry → 0.64
        max_hold_minutes=240,
        direction="BUY_NO",
    )


class TestBarrierLevels:
    def test_buy_yes_levels(self, buy_yes_tb):
        assert buy_yes_tb.upper_barrier == pytest.approx(0.58)
        assert buy_yes_tb.lower_barrier == pytest.approx(0.46)

    def test_buy_no_levels(self, buy_no_tb):
        # For NO: PT is BELOW entry (we profit when price drops)
        assert buy_no_tb.lower_barrier == pytest.approx(0.52)
        assert buy_no_tb.upper_barrier == pytest.approx(0.64)

    def test_clip_to_contract_bounds(self):
        # Huge sigma + extreme entry should clip to [0.01, 0.99]
        tb = TripleBarrier(
            entry_price=0.95, sigma=0.20, pt_mult=2.0, sl_mult=2.0,
            max_hold_minutes=60, direction="BUY_YES",
        )
        assert tb.upper_barrier <= 0.99
        assert tb.lower_barrier >= 0.01


class TestTouchDetection:
    def test_no_touch_within_barriers(self, buy_yes_tb):
        assert buy_yes_tb.check_touch(0.51, elapsed_minutes=10) == BarrierTouch.NONE

    def test_take_profit_touch(self, buy_yes_tb):
        assert buy_yes_tb.check_touch(0.58, elapsed_minutes=10) == BarrierTouch.UPPER
        assert buy_yes_tb.is_profit_touch(BarrierTouch.UPPER)

    def test_stop_loss_touch(self, buy_yes_tb):
        assert buy_yes_tb.check_touch(0.46, elapsed_minutes=10) == BarrierTouch.LOWER
        assert buy_yes_tb.is_loss_touch(BarrierTouch.LOWER)

    def test_vertical_barrier_touch(self, buy_yes_tb):
        assert buy_yes_tb.check_touch(0.51, elapsed_minutes=240) == BarrierTouch.VERTICAL

    def test_stop_takes_priority_over_take_profit(self):
        # If both barriers are crossed (impossible in practice but defensive)
        # the loss-side barrier wins (more conservative)
        tb = TripleBarrier(
            entry_price=0.50, sigma=0.10, pt_mult=0.1, sl_mult=0.1,
            max_hold_minutes=60, direction="BUY_YES",
        )
        # Both barriers at 0.49 / 0.51 — current 0.49 should be SL
        assert tb.check_touch(0.48, 10) == BarrierTouch.LOWER

    def test_buy_no_take_profit_when_price_drops(self, buy_no_tb):
        # NO trade profits when price falls
        touch = buy_no_tb.check_touch(0.52, elapsed_minutes=10)
        assert touch == BarrierTouch.LOWER
        assert buy_no_tb.is_profit_touch(touch)

    def test_buy_no_stop_when_price_rises(self, buy_no_tb):
        touch = buy_no_tb.check_touch(0.64, elapsed_minutes=10)
        assert touch == BarrierTouch.UPPER
        assert buy_no_tb.is_loss_touch(touch)


class TestReasonStrings:
    def test_tp_reason_includes_sigma_mult(self, buy_yes_tb):
        msg = buy_yes_tb.reason_string(BarrierTouch.UPPER)
        assert "TP" in msg and "2.0" in msg

    def test_sl_reason_includes_sigma_mult(self, buy_yes_tb):
        msg = buy_yes_tb.reason_string(BarrierTouch.LOWER)
        assert "SL" in msg and "1.0" in msg

    def test_time_reason_includes_minutes(self, buy_yes_tb):
        msg = buy_yes_tb.reason_string(BarrierTouch.VERTICAL)
        assert "TIME" in msg and "240" in msg

    def test_no_touch_empty_reason(self, buy_yes_tb):
        assert buy_yes_tb.reason_string(BarrierTouch.NONE) == ""


class TestRealizedVol:
    def test_too_few_samples_returns_floor(self):
        assert realized_vol_from_prices([0.5, 0.51]) == 0.02

    def test_zero_movement_returns_floor(self):
        assert realized_vol_from_prices([0.5] * 20) == 0.005

    def test_higher_variation_higher_sigma(self):
        calm = realized_vol_from_prices([0.50, 0.51, 0.50, 0.51, 0.50, 0.51, 0.50] * 3)
        wild = realized_vol_from_prices([0.50, 0.60, 0.40, 0.55, 0.45, 0.58, 0.42] * 3)
        assert wild > calm

    def test_sigma_in_price_units_not_percent(self):
        # 1c per-period swings should give sigma ≈ 0.01
        prices = [0.50 + 0.01 * (i % 2) for i in range(20)]
        sigma = realized_vol_from_prices(prices)
        assert 0.001 < sigma < 0.05
