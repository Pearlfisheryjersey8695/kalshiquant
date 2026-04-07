"""Tests for the Kalshi fee model."""

import pytest
from models.risk_model import kalshi_fee, kalshi_fee_rt


class TestKalshiFee:
    """Kalshi fee = ceil(0.07 * P * (1-P) * 100) / 100 per side."""

    def test_zero_price_no_fee(self):
        assert kalshi_fee(0.0) == 0.0

    def test_one_price_no_fee(self):
        assert kalshi_fee(1.0) == 0.0

    def test_mid_price_max_fee(self):
        # At p=0.50, p*(1-p) = 0.25, fee = ceil(0.07 * 0.25 * 100) / 100 = ceil(1.75)/100 = 0.02
        assert kalshi_fee(0.50) == 0.02

    def test_extreme_low_price(self):
        # p=0.05 -> 0.07 * 0.05 * 0.95 * 100 = 0.3325 -> ceil = 1 -> 0.01
        assert kalshi_fee(0.05) == 0.01

    def test_extreme_high_price(self):
        # Symmetric: p=0.95 should equal p=0.05
        assert kalshi_fee(0.95) == kalshi_fee(0.05)

    def test_round_trip_doubles_when_same_price(self):
        for p in [0.10, 0.30, 0.50, 0.70, 0.90]:
            assert kalshi_fee_rt(p) == 2 * kalshi_fee(p)

    def test_round_trip_with_different_exit(self):
        # When entry and exit prices differ, fees should sum
        entry = 0.30
        exit_p = 0.50
        expected = kalshi_fee(entry) + kalshi_fee(exit_p)
        assert kalshi_fee_rt(entry, exit_p) == expected

    def test_fee_always_positive_in_range(self):
        for p in [0.01, 0.10, 0.25, 0.50, 0.75, 0.90, 0.99]:
            assert kalshi_fee(p) >= 0.0

    def test_fee_symmetric_around_half(self):
        # Fee curve should be symmetric: fee(0.3) == fee(0.7)
        assert kalshi_fee(0.30) == kalshi_fee(0.70)
        assert kalshi_fee(0.10) == kalshi_fee(0.90)
