"""Lightweight latency monitor for the signal loop.

Why: a 5-minute signal cycle has multiple stages (external feeds, live model
inference, batch fallback, parlay pricer, execution). When the cycle gets slow,
we need to know *which* stage regressed — not just that the whole loop got slow.

Usage:
    monitor = LatencyMonitor()
    with monitor.stage("live_ensemble"):
        run_live_ensemble(...)
    monitor.complete_cycle()
    monitor.stats()  # -> dict of p50/p95/max per stage
"""

import time
from collections import defaultdict, deque
from contextlib import contextmanager


class LatencyMonitor:
    def __init__(self, window: int = 50):
        self._window = window
        self._stages: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=window))
        self._cycle_total: deque[float] = deque(maxlen=window)
        self._current_cycle: dict[str, float] = {}
        self._cycle_start: float | None = None
        self._last_cycle_at: float | None = None
        self._last_cycle_stages: dict[str, float] = {}

    @contextmanager
    def stage(self, name: str):
        """Time a stage. Repeated stages within the same cycle are summed."""
        if self._cycle_start is None:
            self._cycle_start = time.perf_counter()
        t0 = time.perf_counter()
        try:
            yield
        finally:
            dt = time.perf_counter() - t0
            self._current_cycle[name] = self._current_cycle.get(name, 0.0) + dt

    def complete_cycle(self) -> dict[str, float]:
        """Roll the current cycle's timings into the rolling windows."""
        if self._cycle_start is None:
            return {}
        total = time.perf_counter() - self._cycle_start
        for name, dt in self._current_cycle.items():
            self._stages[name].append(dt)
        self._cycle_total.append(total)
        self._last_cycle_stages = dict(self._current_cycle)
        self._last_cycle_stages["__total__"] = total
        self._last_cycle_at = time.time()
        # reset for next cycle
        self._current_cycle = {}
        self._cycle_start = None
        return self._last_cycle_stages

    @staticmethod
    def _percentile(values: list[float], pct: float) -> float:
        if not values:
            return 0.0
        s = sorted(values)
        k = max(0, min(len(s) - 1, int(round((pct / 100.0) * (len(s) - 1)))))
        return s[k]

    def stats(self) -> dict:
        """Return p50/p95/max per stage plus the total cycle distribution."""
        out: dict[str, dict] = {}
        for name, dq in self._stages.items():
            vals = list(dq)
            out[name] = {
                "n": len(vals),
                "p50_ms": round(self._percentile(vals, 50) * 1000, 1),
                "p95_ms": round(self._percentile(vals, 95) * 1000, 1),
                "max_ms": round(max(vals) * 1000, 1) if vals else 0.0,
            }
        totals = list(self._cycle_total)
        return {
            "stages": out,
            "cycle": {
                "n": len(totals),
                "p50_ms": round(self._percentile(totals, 50) * 1000, 1),
                "p95_ms": round(self._percentile(totals, 95) * 1000, 1),
                "max_ms": round(max(totals) * 1000, 1) if totals else 0.0,
            },
            "last_cycle_at": self._last_cycle_at,
            "last_cycle_stages_ms": {
                k: round(v * 1000, 1) for k, v in self._last_cycle_stages.items()
            },
        }
