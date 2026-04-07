"""Tests for the Brier score calibration tracker."""

import os
import tempfile
import pytest
from analysis.calibration_tracker import CalibrationTracker


@pytest.fixture
def fresh_tracker(monkeypatch, tmp_path):
    """Tracker with a temp file path so tests don't pollute real data."""
    tmp_file = tmp_path / "calibration_test.json"
    monkeypatch.setattr("analysis.calibration_tracker.TRACKER_PATH", str(tmp_file))
    t = CalibrationTracker()
    t.records = []
    return t


class TestBrierScore:
    def test_empty_returns_no_data(self, fresh_tracker):
        result = fresh_tracker.get_brier_score()
        assert result["n_settled"] == 0
        assert result["model_brier"] is None

    def test_perfect_predictions_zero_brier(self, fresh_tracker):
        # Predicted 1.0, settled 1.0 → squared error = 0
        fresh_tracker.record_prediction("TEST1", 1.0, 0.5, "test")
        fresh_tracker.record_prediction("TEST2", 0.0, 0.5, "test")
        fresh_tracker.record_settlement("TEST1", 1.0)
        fresh_tracker.record_settlement("TEST2", 0.0)

        result = fresh_tracker.get_brier_score()
        assert result["model_brier"] == 0.0
        assert result["n_settled"] == 2

    def test_naive_predictions_score_25(self, fresh_tracker):
        # Always predicting 0.5 → average squared error = 0.25
        for i in range(10):
            fresh_tracker.record_prediction(f"T{i}", 0.5, 0.5, "test")
            fresh_tracker.record_settlement(f"T{i}", 1.0 if i % 2 == 0 else 0.0)

        result = fresh_tracker.get_brier_score()
        assert abs(result["model_brier"] - 0.25) < 1e-9

    def test_alpha_positive_when_better_than_market(self, fresh_tracker):
        # Model predicts 0.8 (correct), market priced 0.5 → model better
        fresh_tracker.record_prediction("T1", 0.8, 0.5, "test")
        fresh_tracker.record_settlement("T1", 1.0)

        result = fresh_tracker.get_brier_score()
        # Model error: (0.8 - 1.0)^2 = 0.04
        # Market error: (0.5 - 1.0)^2 = 0.25
        # Alpha = market - model = 0.21
        assert result["alpha"] == 0.21
        assert result["better_than_market"] is True


class TestGoLiveGate:
    def test_not_ready_with_zero_trades(self, fresh_tracker):
        summary = fresh_tracker.get_summary()
        assert summary["go_live_ready"] is False

    def test_not_ready_below_50_trades(self, fresh_tracker):
        # Only 10 trades, even if perfect, not ready
        for i in range(10):
            fresh_tracker.record_prediction(f"T{i}", 1.0 if i % 2 == 0 else 0.0, 0.5, "test")
            fresh_tracker.record_settlement(f"T{i}", 1.0 if i % 2 == 0 else 0.0)

        summary = fresh_tracker.get_summary()
        assert summary["settled"] == 10
        assert summary["go_live_ready"] is False  # Need 50+

    def test_ready_when_50_perfect_predictions(self, fresh_tracker):
        for i in range(50):
            outcome = 1.0 if i % 2 == 0 else 0.0
            fresh_tracker.record_prediction(f"T{i}", outcome, 0.5, "test")
            fresh_tracker.record_settlement(f"T{i}", outcome)

        summary = fresh_tracker.get_summary()
        assert summary["settled"] == 50
        assert summary["brier_score"]["better_than_market"] is True
        assert summary["go_live_ready"] is True
