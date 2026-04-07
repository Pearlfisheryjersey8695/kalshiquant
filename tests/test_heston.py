"""Tests for the Heston stochastic-volatility digital pricer."""

import math

import pytest

from models.heston import (
    HestonParams,
    default_params_for_asset,
    heston_digital_prob,
    heston_or_lognormal,
)


@pytest.fixture
def base_params():
    return default_params_for_asset(annual_vol=0.30, asset="default")


class TestSanity:
    def test_at_money_short_horizon_50pct(self, base_params):
        # At spot == strike, P(S_T > K) ≈ 0.5 (small drift correction)
        T = 1.0 / 365  # 1 day
        prob = heston_digital_prob(100.0, 100.0, T, base_params, direction="above")
        assert 0.45 <= prob <= 0.55

    def test_deep_in_money_high_prob(self, base_params):
        T = 1.0 / 365
        prob = heston_digital_prob(150.0, 100.0, T, base_params, direction="above")
        assert prob > 0.95

    def test_deep_out_money_low_prob(self, base_params):
        T = 1.0 / 365
        prob = heston_digital_prob(50.0, 100.0, T, base_params, direction="above")
        assert prob < 0.05

    def test_above_below_complement(self, base_params):
        T = 1.0 / 365
        above = heston_digital_prob(100.0, 110.0, T, base_params, direction="above")
        below = heston_digital_prob(100.0, 110.0, T, base_params, direction="below")
        assert abs(above + below - 1.0) < 0.01

    def test_zero_inputs_return_neutral(self, base_params):
        assert heston_digital_prob(0, 100, 1.0/365, base_params) == 0.5
        assert heston_digital_prob(100, 0, 1.0/365, base_params) == 0.5
        assert heston_digital_prob(100, 100, 0, base_params) == 0.5


class TestVolEffects:
    def test_higher_vol_widens_distribution(self):
        T = 1.0 / 365
        low_vol = default_params_for_asset(0.10, "default")
        high_vol = default_params_for_asset(0.80, "default")
        # OTM strike — higher vol should give higher probability of reaching it
        low_p = heston_digital_prob(100, 110, T, low_vol, direction="above")
        high_p = heston_digital_prob(100, 110, T, high_vol, direction="above")
        assert high_p > low_p

    def test_longer_horizon_widens_distribution(self, base_params):
        # Longer T → more time to reach OTM strike
        short = heston_digital_prob(100, 110, 1.0 / 365, base_params, direction="above")
        long_h = heston_digital_prob(100, 110, 30.0 / 365, base_params, direction="above")
        assert long_h > short


class TestAssetPresets:
    def test_btc_preset_uses_higher_vol_of_vol(self):
        btc = default_params_for_asset(0.60, "btc")
        spx = default_params_for_asset(0.20, "spx")
        # BTC vol-of-vol should be higher than SPX (literature consensus)
        assert btc.sigma_v > spx.sigma_v
        # SPX should have stronger negative leverage effect
        assert spx.rho < btc.rho


class TestAutoRoute:
    def test_long_horizon_uses_lognormal(self):
        prob, model = heston_or_lognormal(
            100, 110, hours_to_expiry=24 * 30, annual_vol=0.30
        )
        assert model == "lognormal"

    def test_short_horizon_uses_heston(self):
        prob, model = heston_or_lognormal(
            100, 110, hours_to_expiry=12, annual_vol=0.30
        )
        assert model == "heston"

    def test_threshold_boundary(self):
        # Right at the threshold → lognormal (>=)
        _, model = heston_or_lognormal(
            100, 110, hours_to_expiry=48, annual_vol=0.30, near_expiry_threshold_hours=48
        )
        assert model == "lognormal"


class TestBtcRealistic:
    def test_btc_24h_5pct_otm_realistic(self):
        # BTC at 70k, strike 73.5k (5% OTM), 24h, 60% annual vol.
        # Daily vol ≈ 60%/sqrt(365) ≈ 3.1% → 5% strike = ~1.6σ → P ≈ 5%.
        # The fat-tail Heston correction should push this slightly above the
        # naïve normal-CDF estimate, but the move is large for a 1-day horizon
        # so we expect 3-12%.
        params = default_params_for_asset(0.60, "btc")
        T = 1.0 / 365
        prob = heston_digital_prob(70_000, 73_500, T, params, direction="above")
        assert 0.03 <= prob <= 0.12
