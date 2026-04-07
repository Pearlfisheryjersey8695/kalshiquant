"""Tests for the Monte Carlo CVaR simulator and stress scenarios.

The simulator models terminal payoff of binary contracts as Bernoulli with
correlation injected via a Gaussian copula on the latent normals.
"""

import numpy as np
import pytest

from models.risk_model import RiskModel


@pytest.fixture
def risk():
    return RiskModel(portfolio_value=10_000)


@pytest.fixture
def diversified_book():
    """8 positions across categories — large enough that VaR < CVaR < worst."""
    return [
        {"ticker": "KXBTCMAX-D1", "contracts": 100, "current_price": 0.30, "direction": "BUY_YES"},
        {"ticker": "KXBTCMIN-D2", "contracts":  80, "current_price": 0.55, "direction": "BUY_NO"},
        {"ticker": "KXFED-MAR",   "contracts": 200, "current_price": 0.65, "direction": "BUY_NO"},
        {"ticker": "KXFED-JUN",   "contracts": 150, "current_price": 0.40, "direction": "BUY_YES"},
        {"ticker": "KXINX-Q1",    "contracts": 150, "current_price": 0.45, "direction": "BUY_YES"},
        {"ticker": "KXINX-Q2",    "contracts": 120, "current_price": 0.60, "direction": "BUY_NO"},
        {"ticker": "KXNFL-W12",   "contracts":  90, "current_price": 0.52, "direction": "BUY_YES"},
        {"ticker": "KXMOVIE-A1",  "contracts":  60, "current_price": 0.25, "direction": "BUY_NO"},
    ]


class TestCVaRBasics:
    def test_empty_portfolio_zero_risk(self, risk):
        result = risk.portfolio_cvar([])
        assert result["var_95"] == 0
        assert result["cvar_95"] == 0
        assert result["n_sims"] == 0

    def test_cvar_at_least_var(self, risk, diversified_book):
        # CVaR is the mean of the tail; it must be >= VaR by definition
        result = risk.portfolio_cvar(diversified_book, seed=42)
        assert result["cvar_95"] >= result["var_95"]

    def test_worst_case_at_least_cvar(self, risk, diversified_book):
        # Worst sim must be at least as bad as the average of the tail
        result = risk.portfolio_cvar(diversified_book, seed=42)
        assert result["worst_case"] >= result["cvar_95"]

    def test_seed_reproducible(self, risk, diversified_book):
        a = risk.portfolio_cvar(diversified_book, seed=123)
        b = risk.portfolio_cvar(diversified_book, seed=123)
        assert a["cvar_95"] == b["cvar_95"]
        assert a["var_95"] == b["var_95"]

    def test_fair_priced_book_zero_expected_pnl(self, risk, diversified_book):
        # Each position priced at fair value -> expected PnL ≈ 0
        result = risk.portfolio_cvar(diversified_book, n_sims=20_000, seed=42)
        # 20k sims, std error scales with portfolio notional ~ a few hundred
        assert abs(result["expected_pnl"]) < 20.0


class TestCVaRMath:
    def test_single_position_cvar_equals_loss_on_loss(self, risk):
        # One position priced at 0.30 -> loses 0.30 * contracts with prob 0.70
        # The 5% tail of 1 position is just "we lost" -> CVaR == cost
        pos = [{"ticker": "T1", "contracts": 100, "current_price": 0.30, "direction": "BUY_YES"}]
        result = risk.portfolio_cvar(pos, n_sims=20_000, seed=1)
        # If we lose, we lose 0.30 * 100 = 30
        assert result["worst_case"] == pytest.approx(30.0, abs=0.5)
        assert result["cvar_95"] == pytest.approx(30.0, abs=0.5)

    def test_correlated_positions_have_higher_cvar(self, risk):
        # Two identical positions: when correlated they tail-cluster -> bigger CVaR
        pos = [
            {"ticker": "A", "contracts": 100, "current_price": 0.40, "direction": "BUY_YES"},
            {"ticker": "B", "contracts": 100, "current_price": 0.40, "direction": "BUY_YES"},
        ]
        # Uncorrelated baseline
        risk._correlations = {}
        baseline = risk.portfolio_cvar(pos, n_sims=20_000, seed=7)

        # Now inject high positive correlation
        risk._correlations = {("A", "B"): 0.90}
        correlated = risk.portfolio_cvar(pos, n_sims=20_000, seed=7)

        # Correlated tail should be at least as bad (usually worse)
        assert correlated["cvar_95"] >= baseline["cvar_95"] - 1.0


class TestStressScenarios:
    def test_all_five_scenarios_present(self, risk, diversified_book):
        results = risk.stress_test(diversified_book)
        assert set(results.keys()) == {
            "crypto_crash", "fed_surprise_hike", "spx_gap_down",
            "vol_spike", "liquidity_shock",
        }
        for v in results.values():
            assert "pnl" in v and "n_positions_hit" in v and "description" in v

    def test_crypto_crash_hits_btc_positions(self, risk, diversified_book):
        results = risk.stress_test(diversified_book)
        assert results["crypto_crash"]["n_positions_hit"] >= 2  # KXBTCMAX + KXBTCMIN

    def test_fed_scenario_hits_fed_positions(self, risk, diversified_book):
        results = risk.stress_test(diversified_book)
        assert results["fed_surprise_hike"]["n_positions_hit"] >= 2

    def test_liquidity_shock_hits_all_positions(self, risk, diversified_book):
        results = risk.stress_test(diversified_book)
        assert results["liquidity_shock"]["n_positions_hit"] == len(diversified_book)
        # Liquidity shock = 50% haircut on cost basis -> always negative
        assert results["liquidity_shock"]["pnl"] < 0

    def test_empty_book_zero_stress(self, risk):
        results = risk.stress_test([])
        for v in results.values():
            assert v["pnl"] == 0
            assert v["n_positions_hit"] == 0


class TestPortfolioVarBackCompat:
    def test_legacy_api_returns_var_95_only(self, risk, diversified_book):
        # The old portfolio_var() must still return a single float for callers
        # that haven't migrated yet (alerts, dashboard tile).
        var = risk.portfolio_var(diversified_book)
        assert isinstance(var, float)
        assert var > 0
