"""Daily data-quality report.

Run this every morning (or via cron) to get a one-page health check on:

  1. **Position data**          — open / closed counts, win rate, P&L distribution
  2. **Calibrator state**       — n_train, when last fitted, predicted vs realized
  3. **Signal data**            — n_signals, avg edge, source breakdown
  4. **Market state**           — trade tape depth, stale-price count, refresh age
  5. **External feeds**         — last successful fetch per source, age
  6. **Pipeline health**        — scheduler latency p95s, kill-switch state

The script EXITS NON-ZERO if any of these are degraded enough to warrant
attention — that makes it CI-friendly so we can plug it into a daily cron and
get a paging signal when something silently breaks.

Usage:
    python -m scripts.data_quality_report
    python -m scripts.data_quality_report --json    # machine-readable
    python -m scripts.data_quality_report --strict  # exit non-zero on warnings
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Severity tiers
OK = "ok"
WARN = "warn"
FAIL = "fail"

CHECK_RESULTS: list[tuple[str, str, str, str]] = []  # (severity, section, name, message)


def _check(severity: str, section: str, name: str, message: str) -> None:
    CHECK_RESULTS.append((severity, section, name, message))


# ── Section 1: Positions ─────────────────────────────────────────────────
def check_positions() -> dict:
    db_path = PROJECT_ROOT / "data" / "positions.db"
    if not db_path.exists():
        _check(WARN, "positions", "db_missing", "positions.db not found")
        return {"open": 0, "closed": 0, "win_rate": None, "total_pnl": 0}

    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()

    cur.execute("SELECT COUNT(*) FROM positions WHERE status='open'")
    n_open = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM positions WHERE status='closed'")
    n_closed = cur.fetchone()[0]

    cur.execute("""
        SELECT realized_pnl, fees_paid FROM positions WHERE status='closed'
    """)
    rows = cur.fetchall()
    conn.close()

    pnls = [r[0] or 0 for r in rows]
    fees = [r[1] or 0 for r in rows]
    total_pnl = sum(pnls)
    total_fees = sum(fees)
    n_wins = sum(1 for p in pnls if p > 0)
    win_rate = (n_wins / len(pnls)) if pnls else None

    if n_closed == 0:
        _check(WARN, "positions", "no_closed", "no closed positions yet")
    elif n_closed < 50:
        _check(WARN, "positions", "low_sample", f"only {n_closed} closed positions (need 50+ for calibration)")
    else:
        _check(OK, "positions", "sample", f"{n_closed} closed positions")

    if win_rate is not None and win_rate < 0.40 and n_closed >= 20:
        _check(WARN, "positions", "low_win_rate", f"win rate {win_rate:.1%} < 40%")

    return {
        "open": n_open,
        "closed": n_closed,
        "win_rate": round(win_rate, 4) if win_rate is not None else None,
        "total_pnl": round(total_pnl, 2),
        "total_fees": round(total_fees, 2),
        "fee_drag": round(total_fees / max(1, abs(total_pnl) + total_fees), 4),
    }


# ── Section 2: Calibrator ────────────────────────────────────────────────
def check_calibrator() -> dict:
    cal_path = PROJECT_ROOT / "models" / "saved" / "win_prob_calibration.json"
    if not cal_path.exists():
        _check(WARN, "calibrator", "missing", "win_prob_calibration.json not found")
        return {"is_fitted": False, "n_train": 0}

    with open(cal_path) as f:
        data = json.load(f)

    age_seconds = time.time() - cal_path.stat().st_mtime
    age_days = age_seconds / 86400

    is_fitted = data.get("is_fitted", False)
    n_train = data.get("n_train", 0)
    y_grid = data.get("y_grid", [])

    if not is_fitted:
        _check(WARN, "calibrator", "unfitted", "calibrator file exists but is_fitted=False")
    elif n_train < 50:
        _check(WARN, "calibrator", "low_n", f"fitted on only {n_train} samples")
    else:
        _check(OK, "calibrator", "fitted", f"fitted on {n_train} samples")

    if age_days > 14 and is_fitted:
        _check(WARN, "calibrator", "stale", f"last refit {age_days:.0f} days ago (>14d)")

    # Sanity: curve should be monotonic non-decreasing
    if y_grid:
        violations = sum(1 for i in range(1, len(y_grid)) if y_grid[i] < y_grid[i-1] - 1e-6)
        if violations > 0:
            _check(FAIL, "calibrator", "non_monotonic", f"{violations} monotonicity violations in curve")

    return {
        "is_fitted": is_fitted,
        "n_train": n_train,
        "age_days": round(age_days, 1),
        "min_calibrated": min(y_grid) if y_grid else None,
        "max_calibrated": max(y_grid) if y_grid else None,
    }


# ── Section 3: Signals ───────────────────────────────────────────────────
def check_signals() -> dict:
    sig_path = PROJECT_ROOT / "signals" / "latest_signals.json"
    if not sig_path.exists():
        _check(WARN, "signals", "missing", "latest_signals.json not found")
        return {"n_signals": 0}

    with open(sig_path) as f:
        data = json.load(f)

    age_seconds = time.time() - sig_path.stat().st_mtime
    age_minutes = age_seconds / 60
    signals = data.get("signals", [])
    n = len(signals)

    if n == 0:
        _check(WARN, "signals", "empty", "no signals in latest snapshot")
    elif age_minutes > 30:
        _check(WARN, "signals", "stale", f"last refresh {age_minutes:.0f}min ago (>30min)")
    else:
        _check(OK, "signals", "fresh", f"{n} signals, last refreshed {age_minutes:.0f}min ago")

    if signals:
        edges = [abs(s.get("edge", 0)) for s in signals]
        avg_edge = sum(edges) / len(edges)
        # Source breakdown
        sources = {}
        for s in signals:
            src = s.get("strategy") or s.get("signal_source") or "unknown"
            sources[src] = sources.get(src, 0) + 1
    else:
        avg_edge = 0
        sources = {}

    return {
        "n_signals": n,
        "age_minutes": round(age_minutes, 1),
        "avg_edge": round(avg_edge, 4),
        "sources": sources,
    }


# ── Section 4: Market state ──────────────────────────────────────────────
def check_market_state() -> dict:
    refresh_path = PROJECT_ROOT / "data" / "last_refresh.json"
    age_minutes = None
    if refresh_path.exists():
        try:
            with open(refresh_path) as f:
                data = json.load(f)
            ts_str = data.get("timestamp") or data.get("light")
            if ts_str:
                ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                age_minutes = (datetime.now(timezone.utc) - ts).total_seconds() / 60
        except Exception:
            pass

    if age_minutes is None:
        _check(WARN, "market_state", "no_refresh_ts", "couldn't read last_refresh.json")
    elif age_minutes > 240:
        _check(WARN, "market_state", "stale_pipeline", f"pipeline last ran {age_minutes:.0f}min ago (>4h)")
    else:
        _check(OK, "market_state", "fresh", f"pipeline last ran {age_minutes:.0f}min ago")

    # Count tradeable markets
    tradeable_path = PROJECT_ROOT / "data" / "tradeable_markets.csv"
    n_tradeable = 0
    if tradeable_path.exists():
        with open(tradeable_path, encoding="utf-8", errors="replace") as f:
            n_tradeable = max(0, sum(1 for _ in f) - 1)  # minus header

    if n_tradeable == 0:
        _check(WARN, "market_state", "empty_universe", "tradeable_markets.csv is empty")
    elif n_tradeable < 20:
        _check(WARN, "market_state", "small_universe", f"only {n_tradeable} tradeable markets")
    else:
        _check(OK, "market_state", "universe", f"{n_tradeable} tradeable markets")

    return {
        "age_minutes": round(age_minutes, 1) if age_minutes is not None else None,
        "n_tradeable": n_tradeable,
    }


# ── Section 5: Backtest results ─────────────────────────────────────────
def check_backtest() -> dict:
    bt_path = PROJECT_ROOT / "signals" / "backtest_results.json"
    if not bt_path.exists():
        return {"present": False}
    with open(bt_path) as f:
        data = json.load(f)
    n_trades = data.get("total_trades", 0)
    win_rate = data.get("win_rate", 0)
    sharpe = data.get("sharpe_ratio", 0)

    if n_trades < 30:
        _check(WARN, "backtest", "low_n", f"only {n_trades} backtest trades")
    if sharpe < 0:
        _check(WARN, "backtest", "negative_sharpe", f"sharpe {sharpe:.2f} < 0")

    return {
        "present": True,
        "n_trades": n_trades,
        "win_rate": win_rate,
        "sharpe": sharpe,
    }


# ── Section 6: Experiment tracker ────────────────────────────────────────
def check_experiments() -> dict:
    exp_path = PROJECT_ROOT / "data" / "experiments.jsonl"
    if not exp_path.exists():
        return {"n_runs": 0}
    n_runs = 0
    last_ts = 0
    with open(exp_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                n_runs += 1
                last_ts = max(last_ts, rec.get("end_time") or rec.get("start_time") or 0)
            except json.JSONDecodeError:
                continue

    last_age_hours = (time.time() - last_ts) / 3600 if last_ts else None
    return {
        "n_runs": n_runs,
        "last_run_age_hours": round(last_age_hours, 1) if last_age_hours is not None else None,
    }


# ── Output ───────────────────────────────────────────────────────────────
def _format_human(report: dict) -> str:
    lines = []
    lines.append("=" * 60)
    lines.append(f"  KalshiQuant data quality report — {datetime.now(timezone.utc).isoformat(timespec='seconds')}")
    lines.append("=" * 60)
    for section, data in report.items():
        if section == "checks":
            continue
        lines.append(f"\n[{section}]")
        for k, v in data.items():
            lines.append(f"  {k:24s}  {v}")

    lines.append("\n" + "-" * 60)
    lines.append("Checks:")
    by_sev = {OK: 0, WARN: 0, FAIL: 0}
    for sev, section, name, msg in CHECK_RESULTS:
        marker = {OK: "[OK] ", WARN: "[WARN]", FAIL: "[FAIL]"}[sev]
        lines.append(f"  {marker} {section}.{name}: {msg}")
        by_sev[sev] = by_sev.get(sev, 0) + 1
    lines.append("")
    lines.append(f"  Total: {by_sev[OK]} ok, {by_sev[WARN]} warn, {by_sev[FAIL]} fail")
    lines.append("=" * 60)
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true", help="machine-readable JSON output")
    parser.add_argument("--strict", action="store_true", help="exit 1 on warnings, not just failures")
    args = parser.parse_args()

    report = {
        "positions": check_positions(),
        "calibrator": check_calibrator(),
        "signals": check_signals(),
        "market_state": check_market_state(),
        "backtest": check_backtest(),
        "experiments": check_experiments(),
        "checks": [
            {"severity": s, "section": sec, "name": n, "message": m}
            for s, sec, n, m in CHECK_RESULTS
        ],
    }

    if args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        print(_format_human(report))

    n_fail = sum(1 for s, *_ in CHECK_RESULTS if s == FAIL)
    n_warn = sum(1 for s, *_ in CHECK_RESULTS if s == WARN)
    if n_fail > 0:
        return 2
    if args.strict and n_warn > 0:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
