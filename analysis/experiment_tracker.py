"""Experiment tracking with MLflow as the primary backend and a JSONL fallback.

Why this design
---------------
A real research workflow needs experiment tracking — without it, "we tried that
last week and it didn't work" becomes the dominant cost. MLflow is the standard
tool but it's heavy (sqlalchemy, gunicorn, alembic) and not always installable
in restricted environments.

This wrapper exposes the same surface area regardless of whether MLflow is
available:

    with track("xgboost_v3", params={"depth": 6}) as run:
        run.log_metric("brier", 0.182)
        run.log_metric("ece", 0.04)
        run.log_artifact("models/saved/xgb_v3.json")

If MLflow is installed, runs go to the local mlruns/ directory and you can
launch `mlflow ui` to inspect them. If not, runs are appended to
data/experiments.jsonl as one JSON object per line — still grep-able, still
diff-able, still better than nothing.

The fallback format is intentionally simple so other tools can read it without
parsing MLflow's internal format.
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("kalshi.experiments")

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_FALLBACK_PATH = os.path.join(_PROJECT_ROOT, "data", "experiments.jsonl")


def _mlflow_available() -> bool:
    try:
        import mlflow  # noqa: F401
        return True
    except ImportError:
        return False


@dataclass
class _Run:
    """In-memory run record. Mirrors a tiny subset of MLflow's Run API."""
    name: str
    run_id: str
    start_time: float
    params: dict[str, Any] = field(default_factory=dict)
    metrics: dict[str, list[tuple[float, float]]] = field(default_factory=dict)
    artifacts: list[str] = field(default_factory=list)
    tags: dict[str, str] = field(default_factory=dict)
    end_time: float | None = None
    backend: str = "jsonl"

    # ── MLflow-compatible API ────────────────────────────────────────────
    def log_param(self, key: str, value: Any) -> None:
        self.params[key] = value
        if self.backend == "mlflow":
            import mlflow
            mlflow.log_param(key, value)

    def log_params(self, params: dict[str, Any]) -> None:
        for k, v in params.items():
            self.log_param(k, v)

    def log_metric(self, key: str, value: float, step: int | float | None = None) -> None:
        ts = time.time()
        self.metrics.setdefault(key, []).append((ts, float(value)))
        if self.backend == "mlflow":
            import mlflow
            mlflow.log_metric(key, value, step=int(step) if step is not None else None)

    def log_metrics(self, metrics: dict[str, float], step: int | None = None) -> None:
        for k, v in metrics.items():
            self.log_metric(k, v, step=step)

    def log_artifact(self, path: str) -> None:
        self.artifacts.append(path)
        if self.backend == "mlflow":
            import mlflow
            try:
                mlflow.log_artifact(path)
            except Exception as e:
                logger.warning("MLflow log_artifact failed: %s", e)

    def set_tag(self, key: str, value: str) -> None:
        self.tags[key] = value
        if self.backend == "mlflow":
            import mlflow
            mlflow.set_tag(key, value)


def _persist_jsonl(run: _Run) -> None:
    """Append a run record to the JSONL fallback file."""
    os.makedirs(os.path.dirname(_FALLBACK_PATH), exist_ok=True)
    record = {
        "name": run.name,
        "run_id": run.run_id,
        "start_time": run.start_time,
        "end_time": run.end_time,
        "duration_sec": (run.end_time or time.time()) - run.start_time,
        "params": run.params,
        # For metrics we keep only the LAST value per key in the JSONL summary;
        # the full history is also written under "metric_history" for replayability.
        "metrics": {k: v[-1][1] for k, v in run.metrics.items()},
        "metric_history": {k: v for k, v in run.metrics.items()},
        "artifacts": run.artifacts,
        "tags": run.tags,
        "backend": run.backend,
    }
    with open(_FALLBACK_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")


@contextmanager
def track(
    experiment_name: str,
    params: dict[str, Any] | None = None,
    tags: dict[str, str] | None = None,
):
    """Context manager that yields a _Run.

    Routes to MLflow if available; otherwise persists to JSONL.
    """
    backend = "mlflow" if _mlflow_available() else "jsonl"
    run = _Run(
        name=experiment_name,
        run_id=str(uuid.uuid4())[:8],
        start_time=time.time(),
        backend=backend,
    )
    if params:
        run.params.update(params)
    if tags:
        run.tags.update(tags)

    mlflow_run = None
    if backend == "mlflow":
        try:
            import mlflow
            mlflow.set_experiment(experiment_name)
            mlflow_run = mlflow.start_run()
            for k, v in run.params.items():
                mlflow.log_param(k, v)
            for k, v in run.tags.items():
                mlflow.set_tag(k, v)
        except Exception as e:
            # If MLflow init fails for any reason, fall back to JSONL silently
            logger.warning("MLflow init failed (%s) — using JSONL fallback", e)
            backend = "jsonl"
            run.backend = "jsonl"
            mlflow_run = None

    try:
        yield run
    finally:
        run.end_time = time.time()
        if mlflow_run is not None:
            try:
                import mlflow
                mlflow.end_run()
            except Exception as e:
                logger.warning("MLflow end_run failed: %s", e)
        # Always also persist to JSONL — even MLflow runs benefit from a
        # local human-readable copy.
        try:
            _persist_jsonl(run)
        except Exception as e:
            logger.warning("Experiment JSONL persist failed: %s", e)


def list_runs(limit: int = 50) -> list[dict]:
    """Read recent runs from the JSONL fallback file."""
    if not os.path.exists(_FALLBACK_PATH):
        return []
    runs: list[dict] = []
    with open(_FALLBACK_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                runs.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return runs[-limit:][::-1]  # most recent first
