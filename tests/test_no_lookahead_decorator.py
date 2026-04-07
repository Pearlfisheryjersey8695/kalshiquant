"""Tests for the runtime @no_lookahead decorator."""

import logging

import pandas as pd
import pytest

from analysis.no_lookahead import LookAheadError, no_lookahead


def _make_df(start="2026-01-01", periods=5):
    return pd.DataFrame({
        "ts": pd.date_range(start, periods=periods, freq="D"),
        "value": range(periods),
    })


class TestStrictMode:
    def test_clean_call_passes(self):
        @no_lookahead(timestamp_col="ts")
        def f(df, as_of):
            return df[df["ts"] <= as_of]

        df = _make_df(periods=5)
        result = f(df[df["ts"] <= "2026-01-03"], as_of=pd.Timestamp("2026-01-03"))
        assert len(result) == 3

    def test_input_with_future_rows_raises(self):
        @no_lookahead(timestamp_col="ts")
        def f(df, as_of):
            return df

        df = _make_df(periods=10)  # has rows up to day 10
        with pytest.raises(LookAheadError, match="look-ahead"):
            f(df, as_of=pd.Timestamp("2026-01-03"))

    def test_output_with_future_rows_raises(self):
        @no_lookahead(timestamp_col="ts")
        def naughty(df, as_of):
            # Returns ALL rows including future ones — that's the bug
            return df.copy()

        df = _make_df(periods=10)
        with pytest.raises(LookAheadError):
            naughty(df, as_of=pd.Timestamp("2026-01-04"))

    def test_no_as_of_argument_is_noop(self):
        @no_lookahead()
        def f(df):
            return df

        df = _make_df(periods=10)
        # No as_of -> nothing to check against -> must not raise
        assert f(df) is df


class TestObserveMode:
    def test_warns_but_does_not_raise(self, caplog):
        @no_lookahead(timestamp_col="ts", strict=False)
        def f(df, as_of):
            return df

        df = _make_df(periods=10)
        with caplog.at_level(logging.WARNING, logger="kalshi.no_lookahead"):
            result = f(df, as_of=pd.Timestamp("2026-01-03"))
        assert result is df  # didn't raise
        assert any("look-ahead" in r.message for r in caplog.records)


class TestParameterDetection:
    @pytest.mark.parametrize("name", ["as_of", "t0", "now", "cutoff", "asof"])
    def test_recognised_parameter_names(self, name):
        # Build the function dynamically so the parameter name is the variable
        def make():
            src = (
                f"def f(df, {name}):\n"
                f"    return df\n"
            )
            ns = {}
            exec(src, ns)
            return ns["f"]

        decorated = no_lookahead(timestamp_col="ts")(make())
        df = _make_df(periods=10)
        with pytest.raises(LookAheadError):
            decorated(df, **{name: pd.Timestamp("2026-01-03")})


class TestEdgeCases:
    def test_empty_dataframe_passes(self):
        @no_lookahead(timestamp_col="ts")
        def f(df, as_of):
            return df

        empty = pd.DataFrame({"ts": pd.to_datetime([]), "value": []})
        f(empty, as_of=pd.Timestamp("2026-01-01"))

    def test_index_based_check(self):
        @no_lookahead(timestamp_col=None)  # use index
        def f(df, as_of):
            return df

        df = _make_df(periods=10).set_index("ts")
        with pytest.raises(LookAheadError):
            f(df, as_of=pd.Timestamp("2026-01-03"))

    def test_marker_attribute_set(self):
        @no_lookahead()
        def f(df):
            return df
        assert getattr(f, "__no_lookahead__", False) is True
