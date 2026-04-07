"""Tests for the lognormal probability model used by external feeds."""

import pytest
from data.external_feeds import _lognormal_prob


class TestLognormalProbability:
    """P(S_T > K) using lognormal CDF — same math as digital option pricing."""

    def test_at_money_50_percent_for_short_horizon(self):
        # When current == strike, P should be near 0.5 (slightly less due to drift)
        prob = _lognormal_prob(current=100.0, strike=100.0, vol=0.30, hours=24, direction="above")
        assert 0.45 <= prob <= 0.55

    def test_deep_in_money_high_probability(self):
        # Current way above strike → very high P(above)
        prob = _lognormal_prob(current=100.0, strike=50.0, vol=0.30, hours=24, direction="above")
        assert prob > 0.95

    def test_deep_out_money_low_probability(self):
        # Current way below strike → very low P(above)
        prob = _lognormal_prob(current=50.0, strike=100.0, vol=0.30, hours=24, direction="above")
        assert prob < 0.05

    def test_below_direction_complement(self):
        # P(S > K) + P(S < K) should equal 1.0
        above = _lognormal_prob(100.0, 110.0, 0.30, 24, "above")
        below = _lognormal_prob(100.0, 110.0, 0.30, 24, "below")
        assert abs((above + below) - 1.0) < 1e-6

    def test_higher_vol_widens_distribution(self):
        # OTM strike with higher vol should have higher P(above)
        # Use long horizon (1 year) so vol differences are visible
        low_vol = _lognormal_prob(100.0, 120.0, 0.10, 24 * 365, "above")
        high_vol = _lognormal_prob(100.0, 120.0, 0.80, 24 * 365, "above")
        assert high_vol > low_vol

    def test_longer_horizon_widens_distribution(self):
        # Longer time to expiry → wider distribution → higher P(above) for OTM
        short = _lognormal_prob(100.0, 120.0, 0.30, 24, "above")
        long_h = _lognormal_prob(100.0, 120.0, 0.30, 24 * 30, "above")
        assert long_h > short

    def test_invalid_inputs_return_neutral(self):
        # Defensive: bad inputs should not crash and should return 0.5
        assert _lognormal_prob(0, 100, 0.30, 24) == 0.5
        assert _lognormal_prob(100, 0, 0.30, 24) == 0.5
        assert _lognormal_prob(100, 100, 0, 24) == 0.5
        assert _lognormal_prob(100, 100, 0.30, 0) == 0.5

    def test_btc_realistic_scenario(self):
        # BTC at $69K, 39% vol, 30 days, P(BTC > $80K)
        prob = _lognormal_prob(current=69000, strike=80000, vol=0.39, hours=24 * 30, direction="above")
        # Should be modest but not negligible (~10-25% range)
        assert 0.05 <= prob <= 0.30
