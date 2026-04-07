"""Tests for the look-ahead bias scanner.

We give the scanner small synthetic snippets and assert it catches the patterns
we care about and doesn't false-positive on the safe variants.
"""

import os

import pytest

from scripts.scan_lookahead import _scan_file


def _write(tmp_path, body: str) -> str:
    p = tmp_path / "snippet.py"
    p.write_text(body)
    return str(p)


def test_negative_shift_is_critical(tmp_path):
    f = _write(tmp_path, "x = df.shift(-1)\n")
    findings = _scan_file(f)
    assert any(x.severity == "CRITICAL" and "shift" in x.pattern for x in findings)


def test_unlagged_rolling_is_warning(tmp_path):
    f = _write(tmp_path, "m = s.rolling(12).mean()\n")
    findings = _scan_file(f)
    assert any(x.severity == "WARNING" and "rolling" in x.pattern for x in findings)


def test_lagged_rolling_is_clean(tmp_path):
    f = _write(tmp_path, "m = s.rolling(12).mean().shift(1)\n")
    findings = _scan_file(f)
    assert not any("rolling" in x.pattern for x in findings)


def test_full_series_normalization_is_warning(tmp_path):
    f = _write(tmp_path, "z = (col - col.mean()) / col.std()\n")
    findings = _scan_file(f)
    assert any("normalization" in x.pattern for x in findings)


def test_bfill_is_warning(tmp_path):
    f = _write(tmp_path, "labels = labels.bfill()\n")
    findings = _scan_file(f)
    assert any("bfill" in x.pattern for x in findings)


def test_noqa_marker_suppresses(tmp_path):
    f = _write(tmp_path, "m = s.rolling(12).mean()  # noqa: lookahead\n")
    findings = _scan_file(f)
    assert findings == []


def test_codebase_is_clean():
    """Regression guard: the project must stay free of WARNING+ findings.

    If you legitimately need a flagged pattern, add a `# noqa: lookahead`
    marker to the line and document why in a comment.
    """
    from scripts.scan_lookahead import _iter_python_files, _scan_file as scan
    findings = []
    for f in _iter_python_files():
        findings.extend(scan(f))
    bad = [x for x in findings if x.severity in ("CRITICAL", "WARNING")]
    assert not bad, "Look-ahead findings:\n" + "\n".join(
        f"  {os.path.basename(b.file)}:{b.line} [{b.severity}] {b.pattern}" for b in bad
    )
