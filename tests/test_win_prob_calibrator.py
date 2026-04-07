"""Tests for the isotonic WinProbCalibrator."""

import json

import pytest

from models.risk_model import WinProbCalibrator


class TestFallback:
    def test_unfit_uses_linear_fallback(self):
        cal = WinProbCalibrator()
        # Conservative linear: 0.5 + conf*0.15
        assert cal.calibrate(0.0) == pytest.approx(0.50)
        assert cal.calibrate(1.0) == pytest.approx(0.65)

    def test_clips_out_of_range_inputs(self):
        cal = WinProbCalibrator()
        assert cal.calibrate(-5.0) == pytest.approx(0.50)
        assert cal.calibrate(5.0) == pytest.approx(0.65)


class TestDegenerateGuard:
    def test_all_losers_refuses_to_fit(self, tmp_path, monkeypatch):
        # Backtest with no winners — must NOT fit (would clamp every prediction
        # to y_min and block all future trades).
        bt = tmp_path / "backtest.json"
        bt.write_text(json.dumps({
            "trades": [{"edge_at_entry": 0.05, "net_pnl": -1.0} for _ in range(20)]
        }))
        monkeypatch.setattr(
            WinProbCalibrator, "_load_backtest_pairs",
            classmethod(lambda cls, path=None: cls._load_backtest_pairs.__wrapped__(cls, str(bt))
                        if False else [(0.45, 0.0) for _ in range(20)])
        )
        monkeypatch.setattr(
            WinProbCalibrator, "_load_position_pairs",
            classmethod(lambda cls, path=None: [])
        )
        cal = WinProbCalibrator()
        n = cal.fit_from_history()
        assert n == 0
        assert not cal._is_fitted

    def test_too_few_samples_refuses_to_fit(self, monkeypatch):
        monkeypatch.setattr(
            WinProbCalibrator, "_load_backtest_pairs",
            classmethod(lambda cls, path=None: [(0.5, 1.0), (0.5, 0.0)])
        )
        monkeypatch.setattr(
            WinProbCalibrator, "_load_position_pairs",
            classmethod(lambda cls, path=None: [])
        )
        cal = WinProbCalibrator()
        assert cal.fit_from_history() == 0


class TestFitMixedSample:
    def test_isotonic_is_monotonic(self, monkeypatch):
        # Synthetic well-behaved sample: higher confidence -> higher hit rate
        pairs = []
        for _ in range(20):
            pairs.append((0.30, 0.0))
        for _ in range(15):
            pairs.append((0.50, 1.0))
        for _ in range(5):
            pairs.append((0.50, 0.0))
        for _ in range(20):
            pairs.append((0.80, 1.0))
        monkeypatch.setattr(
            WinProbCalibrator, "_load_backtest_pairs",
            classmethod(lambda cls, path=None: pairs)
        )
        monkeypatch.setattr(
            WinProbCalibrator, "_load_position_pairs",
            classmethod(lambda cls, path=None: [])
        )
        cal = WinProbCalibrator()
        n = cal.fit_from_history()
        assert n == len(pairs)
        assert cal._is_fitted

        low = cal.calibrate(0.30)
        mid = cal.calibrate(0.50)
        high = cal.calibrate(0.80)
        assert low < mid < high
        # All outputs must be inside the safety clip range
        assert 0.05 <= low <= 0.95
        assert 0.05 <= high <= 0.95

    def test_save_load_roundtrip(self, tmp_path, monkeypatch):
        pairs = [(0.30, 0.0)] * 10 + [(0.70, 1.0)] * 10
        monkeypatch.setattr(
            WinProbCalibrator, "_load_backtest_pairs",
            classmethod(lambda cls, path=None: pairs)
        )
        monkeypatch.setattr(
            WinProbCalibrator, "_load_position_pairs",
            classmethod(lambda cls, path=None: [])
        )
        cal = WinProbCalibrator()
        cal.fit_from_history()

        path = tmp_path / "cal.json"
        cal.save(str(path))

        cal2 = WinProbCalibrator()
        cal2.load(str(path))
        assert cal2._is_fitted
        assert cal2.calibrate(0.30) == pytest.approx(cal.calibrate(0.30), abs=1e-9)
        assert cal2.calibrate(0.70) == pytest.approx(cal.calibrate(0.70), abs=1e-9)
