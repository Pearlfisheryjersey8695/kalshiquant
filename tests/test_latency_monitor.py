"""Tests for the signal-loop latency monitor."""

import time

from engine.latency_monitor import LatencyMonitor


def test_records_per_stage_timings():
    m = LatencyMonitor()
    with m.stage("a"):
        time.sleep(0.005)
    with m.stage("b"):
        time.sleep(0.001)
    cycle = m.complete_cycle()
    assert "a" in cycle and "b" in cycle and "__total__" in cycle
    assert cycle["a"] >= cycle["b"]


def test_stats_p50_p95_present():
    m = LatencyMonitor()
    for _ in range(10):
        with m.stage("x"):
            time.sleep(0.001)
        m.complete_cycle()
    s = m.stats()
    assert s["stages"]["x"]["n"] == 10
    assert s["stages"]["x"]["p50_ms"] >= 0
    assert s["stages"]["x"]["p95_ms"] >= s["stages"]["x"]["p50_ms"]
    assert s["cycle"]["n"] == 10


def test_repeated_stage_in_same_cycle_sums():
    m = LatencyMonitor()
    with m.stage("dup"):
        time.sleep(0.002)
    with m.stage("dup"):
        time.sleep(0.002)
    cycle = m.complete_cycle()
    # Two 2ms calls -> at least 4ms summed
    assert cycle["dup"] >= 0.0035
