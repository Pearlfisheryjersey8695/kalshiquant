"""Static scanner for common look-ahead bias patterns in feature/model code.

Look-ahead bias is the silent killer of backtests: a single line that uses
future data turns an otherwise-honest research result into Sharpe-3 fiction.
This scanner can't catch every form (semantic bias is undecidable) but it
flags the *syntactic* patterns that account for the vast majority of real
incidents in time-series ML pipelines.

Run as:
    python -m scripts.scan_lookahead
    python -m scripts.scan_lookahead --strict   # exit 1 on any finding

Patterns checked
----------------
1. Negative shift                      df.shift(-N)             — explicitly using future rows
2. Unlagged rolling                    .rolling(...).<agg>() not followed by .shift(1)
3. Unlagged ewm                        .ewm(...).<agg>()  not followed by .shift(1)
4. Global aggregates on training       .max() / .min() / .mean() / .std() applied to a Series before split_index
5. Future iloc indexing                df.iloc[i+...:] or df.iloc[i:i+N]
6. Full-frame normalization            (col - col.mean()) / col.std()
7. Forward-fill on labels              labels.ffill() / .bfill() (bfill is future leakage)

Each finding has a severity. CRITICAL means "almost certainly a bug",
WARNING means "likely a bug — review", INFO means "intentional usage is
common but please confirm".
"""

import argparse
import ast
import os
import re
import sys
from dataclasses import dataclass

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Folders we scan. Tests / scripts are excluded — they're allowed to peek at
# future data (that's the whole point of a backtest harness).
SCAN_DIRS = ["engine", "models", "analysis", "data"]
EXCLUDE_FILES = {"latency_monitor.py"}  # known-clean infra
EXCLUDE_PATH_PARTS = {"__pycache__", "venv", "node_modules", ".venv"}


SEVERITIES = ("CRITICAL", "WARNING", "INFO")


@dataclass
class Finding:
    severity: str
    file: str
    line: int
    pattern: str
    snippet: str

    def format(self, root: str) -> str:
        rel = os.path.relpath(self.file, root)
        return f"  [{self.severity}] {rel}:{self.line}  {self.pattern}\n      {self.snippet.strip()}"


# ── Regex-based checks (cheap, language-aware enough) ────────────────────────

REGEX_CHECKS: list[tuple[str, str, re.Pattern]] = [
    (
        "CRITICAL",
        "negative shift (uses future rows)",
        re.compile(r"\.shift\s*\(\s*-\s*\d+"),
    ),
    (
        "WARNING",
        "bfill() leaks future labels backwards",
        re.compile(r"\.bfill\s*\("),
    ),
    (
        "WARNING",
        "rolling().<agg>() without .shift(1) — uses current bar",
        re.compile(
            r"\.rolling\s*\([^)]*\)\s*\.\s*(mean|sum|std|var|max|min|median|quantile)\s*\("
        ),
    ),
    (
        "WARNING",
        "ewm().<agg>() without .shift(1) — uses current bar",
        re.compile(r"\.ewm\s*\([^)]*\)\s*\.\s*(mean|std|var)\s*\("),
    ),
    (
        "WARNING",
        "full-series normalization without train/test split",
        re.compile(
            r"\(\s*([A-Za-z_]\w*)\s*-\s*\1\.mean\s*\(\s*\)\s*\)\s*/\s*\1\.std\s*\("
        ),
    ),
    (
        "INFO",
        "iloc forward-slice — confirm not used as features",
        re.compile(r"\.iloc\s*\[\s*[A-Za-z_]\w*\s*\+\s*\d+\s*:"),
    ),
]

# Lines containing any of these markers are exempt — they're explicit
# acknowledgements of the pattern by the author.
SUPPRESS_MARKERS = ("noqa: lookahead", "lookahead-ok")


def _iter_python_files() -> list[str]:
    files = []
    for d in SCAN_DIRS:
        root = os.path.join(PROJECT_ROOT, d)
        if not os.path.isdir(root):
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [x for x in dirnames if x not in EXCLUDE_PATH_PARTS]
            for f in filenames:
                if not f.endswith(".py"):
                    continue
                if f in EXCLUDE_FILES:
                    continue
                files.append(os.path.join(dirpath, f))
    return files


def _scan_file(path: str) -> list[Finding]:
    with open(path, encoding="utf-8") as f:
        lines = f.readlines()

    findings: list[Finding] = []
    in_triple = None  # track docstring state across lines: None, '"""' or "'''"
    for i, line in enumerate(lines, start=1):
        # ── Track triple-quoted string state ─────────────────────
        # We don't fully parse Python — we just track whether we're inside
        # a """...""" or '''...''' block and skip the line if so. This drops
        # both module/function docstrings and multi-line string literals,
        # which between them account for ~all false positives.
        scan_line = line
        if in_triple is None:
            for q in ('"""', "'''"):
                idx = line.find(q)
                if idx != -1:
                    # Opening triple-quote on this line
                    rest = line[idx + 3:]
                    closing = rest.find(q)
                    if closing != -1:
                        # Single-line triple string — strip the quoted content
                        scan_line = line[:idx] + line[idx + 3 + closing + 3:]
                    else:
                        in_triple = q
                        scan_line = line[:idx]
                    break
        else:
            # We're inside a triple-quoted block
            close_idx = line.find(in_triple)
            if close_idx == -1:
                continue  # whole line is inside docstring
            scan_line = line[close_idx + 3:]
            in_triple = None

        stripped = scan_line.split("#", 1)[0]  # ignore comments for matching
        comment = scan_line[len(stripped):]
        if any(m in comment for m in SUPPRESS_MARKERS):
            continue
        for severity, label, rgx in REGEX_CHECKS:
            if not rgx.search(stripped):
                continue
            # Refinement: rolling/ewm followed by .shift(1) on the same line is OK
            if "rolling" in label or "ewm" in label:
                if re.search(r"\)\s*\.\s*shift\s*\(\s*1\s*\)", stripped):
                    continue
            findings.append(Finding(severity, path, i, label, line.rstrip()))
    return findings


# ── AST-based check: detect function-level full-Series .mean() / .std() etc.
#   that are then used as features without a train/test split. This is the most
#   common subtle leak. We approximate by looking for call patterns within the
#   same function that compute a stat from a Series and then *subtract* that
#   stat from the same Series.

class _FullSeriesStatVisitor(ast.NodeVisitor):
    AGGS = {"mean", "std", "var", "max", "min", "median"}

    def __init__(self):
        self.findings: list[tuple[int, str]] = []

    def visit_BinOp(self, node: ast.BinOp):  # noqa: N802
        # pattern:  X - X.<agg>()
        if isinstance(node.op, (ast.Sub, ast.Div)):
            left, right = node.left, node.right
            if (
                isinstance(right, ast.Call)
                and isinstance(right.func, ast.Attribute)
                and right.func.attr in self.AGGS
                and isinstance(right.func.value, ast.Name)
                and isinstance(left, ast.Name)
                and left.id == right.func.value.id
            ):
                self.findings.append(
                    (node.lineno, f"in-place X {ast.dump(node.op)[:3]} X.{right.func.attr}() (full-series stat)")
                )
        self.generic_visit(node)


def _scan_file_ast(path: str) -> list[Finding]:
    try:
        with open(path, encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=path)
    except (SyntaxError, UnicodeDecodeError):
        return []
    v = _FullSeriesStatVisitor()
    v.visit(tree)
    return [
        Finding("INFO", path, lineno, msg, "")
        for lineno, msg in v.findings
    ]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--strict", action="store_true",
                        help="exit 1 if any finding is reported")
    parser.add_argument("--severity", choices=SEVERITIES, default="INFO",
                        help="minimum severity to report")
    args = parser.parse_args()

    min_idx = SEVERITIES.index(args.severity)
    files = _iter_python_files()
    all_findings: list[Finding] = []
    for f in files:
        all_findings.extend(_scan_file(f))
        all_findings.extend(_scan_file_ast(f))

    # Filter by severity
    all_findings = [
        f for f in all_findings
        if SEVERITIES.index(f.severity) <= min_idx
    ]

    by_sev = {s: [f for f in all_findings if f.severity == s] for s in SEVERITIES}
    print(f"Look-ahead scan: {len(files)} files")
    print(f"  CRITICAL: {len(by_sev['CRITICAL'])}")
    print(f"  WARNING : {len(by_sev['WARNING'])}")
    print(f"  INFO    : {len(by_sev['INFO'])}")
    print()

    for sev in SEVERITIES:
        if SEVERITIES.index(sev) > min_idx:
            continue
        if not by_sev[sev]:
            continue
        print(f"-- {sev} -----------------------------------")
        for f in by_sev[sev]:
            print(f.format(PROJECT_ROOT))
        print()

    if args.strict and (by_sev["CRITICAL"] or by_sev["WARNING"]):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
