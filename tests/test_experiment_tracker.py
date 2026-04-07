"""Tests for the experiment tracker (JSONL fallback path).

We don't test the MLflow path here — that would require an MLflow install
and would test their library, not ours. The fallback is the path that always
runs in CI.
"""

import json
import os

import pytest

from analysis import experiment_tracker
from analysis.experiment_tracker import list_runs, track


@pytest.fixture
def tmp_jsonl(monkeypatch, tmp_path):
    path = tmp_path / "experiments.jsonl"
    monkeypatch.setattr(experiment_tracker, "_FALLBACK_PATH", str(path))
    # Force the fallback path so the test doesn't depend on whether MLflow
    # happens to be installed in the test environment.
    monkeypatch.setattr(experiment_tracker, "_mlflow_available", lambda: False)
    yield path


def test_basic_run_writes_jsonl(tmp_jsonl):
    with track("xgb_v1", params={"depth": 6}) as run:
        run.log_metric("brier", 0.18)
        run.log_metric("ece", 0.04)
        run.set_tag("git_sha", "abc123")

    assert tmp_jsonl.exists()
    lines = tmp_jsonl.read_text().strip().split("\n")
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["name"] == "xgb_v1"
    assert record["params"]["depth"] == 6
    assert record["metrics"]["brier"] == 0.18
    assert record["metrics"]["ece"] == 0.04
    assert record["tags"]["git_sha"] == "abc123"
    assert record["duration_sec"] >= 0


def test_log_metric_history_preserved(tmp_jsonl):
    with track("walk_forward") as run:
        for step, brier in enumerate([0.30, 0.25, 0.22, 0.20]):
            run.log_metric("brier", brier, step=step)

    record = json.loads(tmp_jsonl.read_text().strip())
    # Last value goes to summary metrics
    assert record["metrics"]["brier"] == 0.20
    # Full history preserved separately
    assert len(record["metric_history"]["brier"]) == 4
    history_values = [v for _, v in record["metric_history"]["brier"]]
    assert history_values == [0.30, 0.25, 0.22, 0.20]


def test_log_params_bulk(tmp_jsonl):
    with track("test") as run:
        run.log_params({"lr": 0.01, "depth": 4, "n_est": 100})
    record = json.loads(tmp_jsonl.read_text().strip())
    assert record["params"] == {"lr": 0.01, "depth": 4, "n_est": 100}


def test_artifacts_recorded(tmp_jsonl):
    with track("test") as run:
        run.log_artifact("models/saved/foo.json")
        run.log_artifact("models/saved/bar.pkl")
    record = json.loads(tmp_jsonl.read_text().strip())
    assert "models/saved/foo.json" in record["artifacts"]
    assert "models/saved/bar.pkl" in record["artifacts"]


def test_list_runs_returns_recent_first(tmp_jsonl):
    for i in range(5):
        with track(f"run_{i}") as run:
            run.log_metric("x", float(i))

    runs = list_runs()
    assert len(runs) == 5
    # Most recent first
    assert runs[0]["name"] == "run_4"
    assert runs[-1]["name"] == "run_0"


def test_list_runs_limit(tmp_jsonl):
    for i in range(10):
        with track(f"r{i}"):
            pass
    runs = list_runs(limit=3)
    assert len(runs) == 3
    assert [r["name"] for r in runs] == ["r9", "r8", "r7"]


def test_run_id_unique(tmp_jsonl):
    ids = set()
    for _ in range(10):
        with track("test") as run:
            ids.add(run.run_id)
    assert len(ids) == 10  # all unique


def test_exception_in_run_still_persists(tmp_jsonl):
    # If the user's code raises inside the context, we still want the run
    # logged so they can see what params they used before it crashed.
    with pytest.raises(ValueError):
        with track("crashy") as run:
            run.log_param("attempt", 1)
            raise ValueError("boom")

    runs = list_runs()
    assert len(runs) == 1
    assert runs[0]["params"]["attempt"] == 1
