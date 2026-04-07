"""Tests for order-flow toxicity metrics (VPIN + Kyle's lambda)."""

import pytest

from analysis.order_flow import (
    compute_kyle_lambda,
    compute_vpin,
    is_toxic,
    order_flow_metrics,
)


def _trades(prices: list[float], volumes: list[int]) -> list[dict]:
    return [{"yes_price": p, "count": v} for p, v in zip(prices, volumes)]


class TestVPIN:
    def test_empty_trades_zero_vpin(self):
        v, n, _ = compute_vpin([])
        assert v == 0.0
        assert n == 0

    def test_balanced_flow_low_vpin(self):
        # Alternating up/down ticks with equal volume → near-zero imbalance
        prices = [0.50 + 0.01 * (1 if i % 2 == 0 else -1) for i in range(100)]
        vols = [10] * 100
        v, n, _ = compute_vpin(_trades(prices, vols), n_buckets=5)
        assert n > 0
        assert v < 0.40  # balanced flow

    def test_one_sided_buy_flow_high_vpin(self):
        # Strong upward trend with all volume — one-sided informed flow
        prices = [0.50 + 0.01 * i for i in range(50)]
        vols = [10] * 50
        v, n, _ = compute_vpin(_trades(prices, vols), n_buckets=5)
        assert v > 0.60

    def test_one_sided_sell_flow_high_vpin(self):
        prices = [0.80 - 0.01 * i for i in range(50)]
        vols = [10] * 50
        v, n, _ = compute_vpin(_trades(prices, vols), n_buckets=5)
        assert v > 0.60

    def test_handles_cents_and_decimal_prices(self):
        # If yes_price is in cents (>1), it should be normalised internally
        cents_trades = [{"yes_price": 50 + i, "count": 10} for i in range(20)]
        decimal_trades = [{"yes_price": (50 + i) / 100.0, "count": 10} for i in range(20)]
        v_cents, _, _ = compute_vpin(cents_trades)
        v_decimal, _, _ = compute_vpin(decimal_trades)
        assert v_cents == pytest.approx(v_decimal)


class TestKyleLambda:
    def test_too_few_trades_returns_zero(self):
        assert compute_kyle_lambda(_trades([0.5, 0.51], [10, 10])) == 0.0

    def test_no_price_change_zero_lambda(self):
        prices = [0.50] * 30
        vols = [10] * 30
        assert compute_kyle_lambda(_trades(prices, vols)) == 0.0

    def test_positive_lambda_when_buys_lift_price(self):
        # Each upward tick is followed by another → positive correlation
        prices = [0.50 + 0.005 * i for i in range(30)]
        vols = [10] * 30
        lam = compute_kyle_lambda(_trades(prices, vols))
        assert lam > 0  # buying pressure is moving the price up

    def test_lambda_higher_for_thinner_market(self):
        # Same price impact on smaller volume → higher lambda
        prices = [0.50 + 0.005 * i for i in range(30)]
        thin = compute_kyle_lambda(_trades(prices, [1] * 30))
        thick = compute_kyle_lambda(_trades(prices, [100] * 30))
        assert thin > thick


class TestMetricsBundle:
    def test_low_vpin_labelled_low(self):
        prices = [0.50 + 0.01 * (1 if i % 2 == 0 else -1) for i in range(100)]
        m = order_flow_metrics(_trades(prices, [10] * 100))
        assert m.toxicity_label == "low"
        assert not is_toxic(m)

    def test_high_vpin_labelled_high(self):
        prices = [0.50 + 0.01 * i for i in range(50)]
        m = order_flow_metrics(_trades(prices, [10] * 50))
        assert m.toxicity_label == "high"
        assert is_toxic(m)

    def test_metrics_round_to_4_dp(self):
        prices = [0.50, 0.51, 0.50, 0.52, 0.51]
        m = order_flow_metrics(_trades(prices, [10] * 5))
        assert isinstance(m.vpin, float)
        assert m.n_trades == 5
