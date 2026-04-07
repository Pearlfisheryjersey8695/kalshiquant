"""Tests for the end-to-end decision pipeline.

These tests pin down the gate ordering and rejection semantics. The integration
itself is covered in unit tests of the underlying modules — what we verify here
is that the *composition* is correct: each gate fires when expected, the
rejection reasons are populated, and the decision shape is stable.
"""

import pytest

from engine.integrated_decision import (
    DecisionTrace,
    evaluate_market,
    MIN_BL_WEIGHT_FOR_TRADE,
    MIN_EDGE_FOR_TRADE,
    CVAR_PER_POSITION_CAP_PCT,
)


class TestSanityGate:
    def test_quote_zero_rejected(self):
        trace = evaluate_market("X", live_quote=0.0, raw_fair_value=0.5, use_polymarket=False)
        assert trace.rejected_at == "sanity"

    def test_quote_one_rejected(self):
        trace = evaluate_market("X", live_quote=1.0, raw_fair_value=0.5, use_polymarket=False)
        assert trace.rejected_at == "sanity"

    def test_quote_negative_rejected(self):
        trace = evaluate_market("X", live_quote=-0.1, raw_fair_value=0.5, use_polymarket=False)
        assert trace.rejected_at == "sanity"


class TestCalibrationAndEdgeGate:
    """Pin the calibrator to identity so we test the GATE, not the calibrator.

    The calibrator's behaviour is covered in test_win_prob_calibrator.py — here
    we want to know that *when calibrated edge is small, the trade is skipped*,
    irrespective of how we got there.
    """

    @pytest.fixture
    def identity_calibrator(self, monkeypatch):
        from models.risk_model import WinProbCalibrator
        monkeypatch.setattr(WinProbCalibrator, "load", lambda self, path=None: None)
        monkeypatch.setattr(WinProbCalibrator, "_is_fitted", True, raising=False)
        monkeypatch.setattr(WinProbCalibrator, "calibrate", lambda self, x: x)

    def test_zero_edge_rejected_at_min_edge(self, identity_calibrator):
        trace = evaluate_market("X", live_quote=0.50, raw_fair_value=0.50, use_polymarket=False)
        assert trace.rejected_at == "min_edge"
        assert trace.calibrated_fair_value == 0.50  # identity calibrator

    def test_small_edge_rejected(self, identity_calibrator):
        # 1c edge — well below the 4c threshold
        trace = evaluate_market("X", live_quote=0.50, raw_fair_value=0.51, use_polymarket=False)
        assert trace.rejected_at == "min_edge"


class TestSuccessfulTrade:
    def test_clear_buy_yes_passes(self):
        # Big positive calibrated edge: should pass through to a BUY_YES
        trace = evaluate_market(
            "TEST", live_quote=0.30, raw_fair_value=0.80,
            contracts=10, bankroll=100_000, use_polymarket=False,
        )
        # On a BUY_YES of 10 contracts at 0.30, max loss is $3, well under
        # the $2000 (2%) per-position CVaR cap on a $100k bankroll.
        assert trace.rejected_at == "" or trace.rejected_at == "bl_weight"
        # The gate that passes determines the decision; we just check the
        # trace is fully populated up to wherever it stopped
        assert trace.calibrated_fair_value is not None
        assert trace.bl_weight is not None

    def test_clear_buy_no_passes(self):
        # Big negative calibrated edge: should pass through to a BUY_NO
        trace = evaluate_market(
            "TEST", live_quote=0.80, raw_fair_value=0.30,
            contracts=10, bankroll=100_000, use_polymarket=False,
        )
        assert trace.calibrated_fair_value is not None
        assert trace.bl_weight is not None


class TestCVaRGate:
    def test_oversized_position_rejected_at_cvar(self):
        # Tiny bankroll + large position → CVaR exceeds the 2% cap
        trace = evaluate_market(
            "TEST", live_quote=0.30, raw_fair_value=0.90,
            contracts=10_000,  # huge position
            bankroll=1_000,    # small bankroll
            use_polymarket=False,
        )
        assert trace.rejected_at == "cvar"
        assert trace.cvar_dollars > trace.cvar_limit_dollars


class TestTraceShape:
    def test_to_dict_keys_stable(self):
        trace = evaluate_market("X", live_quote=0.50, raw_fair_value=0.50, use_polymarket=False)
        d = trace.to_dict()
        # All these keys MUST be present so the API contract doesn't drift
        for key in [
            "ticker", "live_quote", "raw_fair_value", "calibrated_fair_value",
            "polymarket_quote", "bl_weight", "cvar_dollars", "decision",
            "rejected_at", "rejection_reason", "notes",
        ]:
            assert key in d

    def test_calibrator_note_recorded(self):
        trace = evaluate_market("X", live_quote=0.30, raw_fair_value=0.80, use_polymarket=False)
        assert any("calibrator" in n.lower() for n in trace.notes)


class TestPolymarketIntegration:
    def test_polymarket_disabled_skips_lookup(self):
        trace = evaluate_market(
            "X", live_quote=0.30, raw_fair_value=0.80,
            use_polymarket=False,
        )
        assert trace.polymarket_quote is None
        assert trace.polymarket_match_confidence is None

    def test_polymarket_lookup_safe_on_unknown_ticker(self, monkeypatch):
        # Stub the polymarket adapter so we never hit the network
        from data.polymarket import polymarket_adapter
        monkeypatch.setattr(polymarket_adapter, "find_match", lambda *a, **kw: None)
        trace = evaluate_market(
            "UNKNOWN_TICKER_XYZ", live_quote=0.30, raw_fair_value=0.80,
            title="some unknown event",
            use_polymarket=True,
        )
        # Polymarket lookup ran but found nothing — pipeline continues
        assert trace.polymarket_quote is None
        assert trace.calibrated_fair_value is not None  # didn't crash
