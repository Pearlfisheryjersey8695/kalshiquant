"""Tests for the data quality report script.

We test the report logic against a synthetic project root so it doesn't
depend on the live state of the actual project.
"""

import json
import sqlite3

import pytest

from scripts import data_quality_report as dqr


@pytest.fixture
def fake_root(tmp_path, monkeypatch):
    """Build a minimal fake project tree under tmp_path."""
    monkeypatch.setattr(dqr, "PROJECT_ROOT", tmp_path)
    (tmp_path / "data").mkdir()
    (tmp_path / "models" / "saved").mkdir(parents=True)
    (tmp_path / "signals").mkdir()
    # Reset module-level state between tests
    dqr.CHECK_RESULTS.clear()
    return tmp_path


def _write_positions_db(root, rows):
    """rows: list of (status, realized_pnl, fees_paid)."""
    db = root / "data" / "positions.db"
    conn = sqlite3.connect(str(db))
    conn.execute("""
        CREATE TABLE positions (
            id INTEGER PRIMARY KEY,
            status TEXT,
            realized_pnl REAL,
            fees_paid REAL
        )
    """)
    conn.executemany(
        "INSERT INTO positions (status, realized_pnl, fees_paid) VALUES (?, ?, ?)",
        rows,
    )
    conn.commit()
    conn.close()


class TestPositions:
    def test_no_db_warns(self, fake_root):
        result = dqr.check_positions()
        assert result["closed"] == 0
        assert any(c[0] == "warn" for c in dqr.CHECK_RESULTS)

    def test_low_sample_warns(self, fake_root):
        _write_positions_db(fake_root, [("closed", 5.0, 0.5)] * 10)
        result = dqr.check_positions()
        assert result["closed"] == 10
        assert any("low_sample" in c[2] for c in dqr.CHECK_RESULTS)

    def test_healthy_sample_ok(self, fake_root):
        rows = [("closed", 5.0, 0.5)] * 30 + [("closed", -3.0, 0.5)] * 30
        _write_positions_db(fake_root, rows)
        result = dqr.check_positions()
        assert result["closed"] == 60
        assert result["win_rate"] == pytest.approx(0.5)
        assert any(c[0] == "ok" and c[2] == "sample" for c in dqr.CHECK_RESULTS)


class TestCalibrator:
    def test_missing_warns(self, fake_root):
        result = dqr.check_calibrator()
        assert result["is_fitted"] is False
        assert any(c[2] == "missing" for c in dqr.CHECK_RESULTS)

    def test_fitted_with_enough_samples_ok(self, fake_root):
        cal_path = fake_root / "models" / "saved" / "win_prob_calibration.json"
        cal_path.write_text(json.dumps({
            "is_fitted": True,
            "n_train": 200,
            "x_grid": [0.0, 0.5, 1.0],
            "y_grid": [0.10, 0.50, 0.90],
        }))
        result = dqr.check_calibrator()
        assert result["is_fitted"] is True
        assert result["n_train"] == 200
        assert any(c[0] == "ok" and c[2] == "fitted" for c in dqr.CHECK_RESULTS)

    def test_non_monotonic_curve_fails(self, fake_root):
        cal_path = fake_root / "models" / "saved" / "win_prob_calibration.json"
        # y_grid going DOWN somewhere — should be flagged
        cal_path.write_text(json.dumps({
            "is_fitted": True,
            "n_train": 200,
            "x_grid": [0.0, 0.5, 1.0],
            "y_grid": [0.10, 0.50, 0.30],  # 0.50 -> 0.30 violates monotonicity
        }))
        dqr.check_calibrator()
        assert any(c[0] == "fail" and c[2] == "non_monotonic" for c in dqr.CHECK_RESULTS)


class TestSignals:
    def test_missing_warns(self, fake_root):
        result = dqr.check_signals()
        assert result["n_signals"] == 0
        assert any(c[2] == "missing" for c in dqr.CHECK_RESULTS)

    def test_fresh_signals_ok(self, fake_root):
        sig_path = fake_root / "signals" / "latest_signals.json"
        sig_path.write_text(json.dumps({
            "signals": [
                {"ticker": "T1", "edge": 0.05, "strategy": "convergence"},
                {"ticker": "T2", "edge": 0.03, "strategy": "momentum"},
            ]
        }))
        result = dqr.check_signals()
        assert result["n_signals"] == 2
        assert result["sources"] == {"convergence": 1, "momentum": 1}
        assert any(c[0] == "ok" and c[2] == "fresh" for c in dqr.CHECK_RESULTS)


class TestMainEntrypoint:
    def test_main_returns_zero_on_clean_project(self, fake_root, monkeypatch, capsys):
        # Healthy fake project: 60 closed positions, fitted calibrator, signals, etc.
        _write_positions_db(fake_root, [("closed", 5.0, 0.5)] * 30 + [("closed", -3.0, 0.5)] * 30)
        (fake_root / "models" / "saved" / "win_prob_calibration.json").write_text(
            json.dumps({"is_fitted": True, "n_train": 200,
                        "x_grid": [0, 1], "y_grid": [0.1, 0.9]})
        )
        (fake_root / "signals" / "latest_signals.json").write_text(
            json.dumps({"signals": [{"ticker": "T", "edge": 0.05}]})
        )
        (fake_root / "data" / "tradeable_markets.csv").write_text(
            "ticker,vol\n" + "\n".join(f"T{i},100" for i in range(50))
        )
        from datetime import datetime, timezone
        (fake_root / "data" / "last_refresh.json").write_text(
            json.dumps({"timestamp": datetime.now(timezone.utc).isoformat()})
        )

        monkeypatch.setattr("sys.argv", ["data_quality_report"])
        rc = dqr.main()
        # No fails -> rc should be 0 (warnings allowed unless --strict)
        assert rc == 0
