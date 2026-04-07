"""Tests for the Black-Litterman portfolio optimizer."""

import numpy as np
import pytest

from models.black_litterman import BLView, BlackLittermanOptimizer


@pytest.fixture
def opt():
    return BlackLittermanOptimizer()


class TestBasics:
    def test_empty_views_zero_result(self, opt):
        result = opt.optimize([])
        assert result.n_views == 0
        assert result.weights == {}
        assert result.leverage == 0.0

    def test_single_view_positive_weight(self, opt):
        view = BLView(ticker="BTC", expected_pnl=0.05, confidence=0.7)
        result = opt.optimize([view])
        assert result.n_views == 1
        assert result.weights["BTC"] > 0  # positive edge -> positive weight

    def test_negative_edge_negative_weight(self, opt):
        view = BLView(ticker="X", expected_pnl=-0.05, confidence=0.7)
        result = opt.optimize([view])
        assert result.weights["X"] < 0


class TestLeverageConstraint:
    def test_leverage_capped_at_max(self):
        # With high confidence + many positions, raw weights would exceed cap
        opt = BlackLittermanOptimizer(max_leverage=0.30)
        views = [
            BLView(f"T{i}", expected_pnl=0.10, confidence=0.95)
            for i in range(10)
        ]
        result = opt.optimize(views)
        assert result.leverage <= 0.30 + 1e-6

    def test_low_signals_dont_max_leverage(self, opt):
        # Tiny edges + low confidence -> total leverage well below max
        views = [
            BLView(f"T{i}", expected_pnl=0.001, confidence=0.30)
            for i in range(5)
        ]
        result = opt.optimize(views)
        assert result.leverage < opt.max_leverage


class TestConfidenceShrinkage:
    def test_low_confidence_shrinks_toward_zero(self, opt):
        high = BLView("X", expected_pnl=0.05, confidence=0.95)
        low = BLView("X", expected_pnl=0.05, confidence=0.10)
        w_high = opt.optimize([high]).weights["X"]
        w_low = opt.optimize([low]).weights["X"]
        # Higher confidence -> bigger weight
        assert w_high > w_low

    def test_zero_confidence_near_zero_weight(self, opt):
        view = BLView("X", expected_pnl=0.05, confidence=0.05)
        result = opt.optimize([view])
        # Effectively no view -> small weight
        assert abs(result.weights["X"]) < 0.10


class TestCorrelation:
    def test_correlated_positions_get_less_combined_weight(self, opt):
        # Two identical views with high correlation should NOT each get
        # full single-name weight; the correlation should suppress them.
        view_a = BLView("A", expected_pnl=0.05, confidence=0.7)
        view_b = BLView("B", expected_pnl=0.05, confidence=0.7)

        # Uncorrelated baseline
        independent_cov = np.eye(2) * (0.04 ** 2)
        independent = opt.optimize([view_a, view_b], cov_matrix=independent_cov)

        # High correlation
        rho = 0.90
        corr_cov = np.array([
            [0.04 ** 2, rho * 0.04 * 0.04],
            [rho * 0.04 * 0.04, 0.04 ** 2],
        ])
        correlated = opt.optimize([view_a, view_b], cov_matrix=corr_cov)

        # With correlation, the optimizer should pull the combined leverage DOWN
        assert correlated.leverage < independent.leverage + 1e-6

    def test_negative_correlation_gives_more_aggregate_weight(self, opt):
        view_a = BLView("A", expected_pnl=0.05, confidence=0.7)
        view_b = BLView("B", expected_pnl=0.05, confidence=0.7)

        # Negative correlation = natural hedge -> can size more aggressively
        neg_cov = np.array([
            [0.04 ** 2, -0.5 * 0.04 * 0.04],
            [-0.5 * 0.04 * 0.04, 0.04 ** 2],
        ])
        independent_cov = np.eye(2) * (0.04 ** 2)

        neg = opt.optimize([view_a, view_b], cov_matrix=neg_cov)
        ind = opt.optimize([view_a, view_b], cov_matrix=independent_cov)
        assert neg.leverage >= ind.leverage - 1e-6


class TestSignalsAdapter:
    def test_buy_yes_positive_edge(self, opt):
        signals = [
            {"ticker": "X", "net_edge": 0.04, "confidence": 0.6, "direction": "BUY_YES"},
        ]
        views = opt.views_from_signals(signals)
        assert views[0].expected_pnl == pytest.approx(0.04)

    def test_buy_no_flips_sign(self, opt):
        signals = [
            {"ticker": "X", "net_edge": 0.04, "confidence": 0.6, "direction": "BUY_NO"},
        ]
        views = opt.views_from_signals(signals)
        assert views[0].expected_pnl == pytest.approx(-0.04)

    def test_uses_net_edge_over_edge(self, opt):
        signals = [
            {"ticker": "X", "net_edge": 0.03, "edge": 0.05,
             "confidence": 0.6, "direction": "BUY_YES"},
        ]
        views = opt.views_from_signals(signals)
        assert views[0].expected_pnl == pytest.approx(0.03)
