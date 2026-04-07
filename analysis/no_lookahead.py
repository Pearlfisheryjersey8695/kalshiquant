"""Runtime decorator that asserts a feature function did not peek at the future.

Why this exists
---------------
The static scanner in scripts/scan_lookahead.py catches *syntactic* leaks
(`.shift(-1)`, unlagged rolling, etc.) but it can't catch the semantic case:
a function that takes a full DataFrame of history and an `as_of` timestamp,
then accidentally uses rows newer than `as_of`.

This decorator wraps a feature function and verifies, at runtime, that:

  1. If the function takes an `as_of` (or `t0`, `now`, `cutoff`) parameter,
     and a DataFrame argument with a recognised timestamp column, then the
     DataFrame contains no rows with timestamp > as_of.
  2. The function's return value (if it's a DataFrame or Series with the
     same timestamp index) does not contain any rows with timestamp > as_of.

If either check fails it raises LookAheadError. There is also an
"observe-only" mode that logs warnings instead — useful for legacy code
you can't fix immediately but want to monitor.

Usage
-----
    from analysis.no_lookahead import no_lookahead

    @no_lookahead(timestamp_col="ts")
    def make_features(df: pd.DataFrame, as_of: pd.Timestamp) -> pd.DataFrame:
        ...

    # Or observe-only:
    @no_lookahead(timestamp_col="ts", strict=False)
    def legacy_make_features(df, as_of):
        ...
"""

from __future__ import annotations

import functools
import inspect
import logging
from typing import Any, Callable

logger = logging.getLogger("kalshi.no_lookahead")

# Parameter names we accept as the "current time" cutoff
_AS_OF_NAMES = ("as_of", "t0", "now", "cutoff", "asof")


class LookAheadError(AssertionError):
    """Raised when a feature function is detected using future data."""


def _find_as_of(args: tuple, kwargs: dict, sig: inspect.Signature) -> Any | None:
    # Bind for explicit names
    try:
        bound = sig.bind_partial(*args, **kwargs)
    except TypeError:
        return None
    for name in _AS_OF_NAMES:
        if name in bound.arguments:
            return bound.arguments[name]
    return None


def _find_dataframe(args: tuple, kwargs: dict) -> Any | None:
    # First positional or keyword argument that looks like a DataFrame
    for v in list(args) + list(kwargs.values()):
        if hasattr(v, "columns") and hasattr(v, "index"):
            return v
    return None


def _max_timestamp(df: Any, timestamp_col: str | None) -> Any | None:
    """Return the maximum timestamp in the DataFrame, or None if not found."""
    if df is None:
        return None
    # Try the explicit column first
    if timestamp_col and timestamp_col in getattr(df, "columns", []):
        col = df[timestamp_col]
        if len(col) == 0:
            return None
        try:
            return col.max()
        except (TypeError, ValueError):
            return None
    # Fall back to the index if it's a DatetimeIndex
    idx = getattr(df, "index", None)
    if idx is not None and len(idx) > 0:
        try:
            # Only use index if it looks time-like
            import pandas as pd
            if isinstance(idx, pd.DatetimeIndex):
                return idx.max()
        except Exception:
            pass
    return None


def no_lookahead(
    timestamp_col: str | None = "ts",
    strict: bool = True,
) -> Callable:
    """Decorator factory that enforces no future-data access.

    Parameters
    ----------
    timestamp_col : str | None
        Name of the timestamp column to inspect on input/output DataFrames.
        Defaults to "ts". Set to None to use the DataFrame's index instead.
    strict : bool
        If True (default), raise LookAheadError on violation. If False, log
        a warning and return normally — use for legacy code under remediation.
    """

    def decorator(fn: Callable) -> Callable:
        sig = inspect.signature(fn)

        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            as_of = _find_as_of(args, kwargs, sig)

            # If there's no as_of parameter, the decorator is a no-op
            # (we have nothing to check against). This is intentional —
            # users opt-in by giving their function an as_of arg.
            if as_of is None:
                return fn(*args, **kwargs)

            df = _find_dataframe(args, kwargs)
            if df is not None:
                max_ts = _max_timestamp(df, timestamp_col)
                if max_ts is not None and max_ts > as_of:
                    msg = (
                        f"{fn.__qualname__}: input DataFrame contains rows with "
                        f"timestamp {max_ts} > as_of {as_of} (look-ahead bias)"
                    )
                    if strict:
                        raise LookAheadError(msg)
                    logger.warning(msg)

            result = fn(*args, **kwargs)

            # Check the return value too
            if hasattr(result, "columns") or hasattr(result, "index"):
                max_out = _max_timestamp(result, timestamp_col)
                if max_out is not None and max_out > as_of:
                    msg = (
                        f"{fn.__qualname__}: return value contains rows with "
                        f"timestamp {max_out} > as_of {as_of} (look-ahead bias)"
                    )
                    if strict:
                        raise LookAheadError(msg)
                    logger.warning(msg)

            return result

        wrapper.__no_lookahead__ = True  # marker for introspection
        return wrapper

    return decorator
