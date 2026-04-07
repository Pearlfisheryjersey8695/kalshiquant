"""Tests for the CME FedWatch ZQ-implied probability feed.

The math being tested:
  implied_avg_rate = 100 - ZQ_price
  post_meeting_rate solves:
      implied_avg = ((d-1)/dim)*current + ((dim-d+1)/dim)*post

These tests stub the network so they're fully offline.
"""

from datetime import datetime, timezone

import pytest

from data.external_feeds import CMEFedWatchFeed


@pytest.fixture(autouse=True)
def clear_cache():
    """Wipe the module-level cache before each test so stubbed prices stick."""
    from data import external_feeds
    external_feeds._cache.clear()
    yield
    external_feeds._cache.clear()


@pytest.fixture
def feed():
    return CMEFedWatchFeed()


class TestZqMath:
    def test_zq_at_par_implies_zero_rate(self, feed, monkeypatch):
        # Force the front-month meeting to be early in the month so n2 dominates
        monkeypatch.setattr(CMEFedWatchFeed, "_fetch_zq_price", lambda self: 100.0)
        monkeypatch.setattr(
            CMEFedWatchFeed, "_next_fomc_date",
            lambda self, today=None: datetime(2026, 6, 17, tzinfo=timezone.utc),
        )
        # Force fallback current rate to a known value
        monkeypatch.setattr(
            feed._fred_fallback, "fetch",
            lambda: {"target_rate_mid": 4.375},
        )
        data = feed.fetch()
        assert data["implied_avg_rate"] == 0.0
        # Since current is 4.375 but implied avg is 0, post-meeting rate is
        # very negative — confirms the math is being applied (not capped here).
        assert data["implied_post_meeting_rate"] < 0

    def test_zq_implies_no_change_when_avg_equals_current(self, feed, monkeypatch):
        # If implied_avg == current_rate, post_meeting_rate must == current_rate
        current = 4.375
        monkeypatch.setattr(CMEFedWatchFeed, "_fetch_zq_price",
                            lambda self: 100.0 - current)
        monkeypatch.setattr(
            CMEFedWatchFeed, "_next_fomc_date",
            lambda self, today=None: datetime(2026, 6, 17, tzinfo=timezone.utc),
        )
        monkeypatch.setattr(
            feed._fred_fallback, "fetch",
            lambda: {"target_rate_mid": current},
        )
        data = feed.fetch()
        assert data["implied_post_meeting_rate"] == pytest.approx(current, abs=1e-6)
        assert data["implied_move_bps"] == 0.0

    def test_implied_cut_lowers_post_rate(self, feed, monkeypatch):
        current = 4.375
        # If avg rate over the month is 25bps below current, the market is
        # pricing a cut at the meeting
        avg = current - 0.10
        monkeypatch.setattr(CMEFedWatchFeed, "_fetch_zq_price",
                            lambda self: 100.0 - avg)
        monkeypatch.setattr(
            CMEFedWatchFeed, "_next_fomc_date",
            lambda self, today=None: datetime(2026, 6, 17, tzinfo=timezone.utc),
        )
        monkeypatch.setattr(
            feed._fred_fallback, "fetch",
            lambda: {"target_rate_mid": current},
        )
        data = feed.fetch()
        assert data["implied_post_meeting_rate"] < current
        assert data["implied_move_bps"] < 0


class TestProbabilityMapping:
    def test_target_at_implied_gives_50_percent(self, feed, monkeypatch):
        current = 4.375
        monkeypatch.setattr(CMEFedWatchFeed, "_fetch_zq_price",
                            lambda self: 100.0 - current)
        monkeypatch.setattr(
            CMEFedWatchFeed, "_next_fomc_date",
            lambda self, today=None: datetime(2026, 6, 17, tzinfo=timezone.utc),
        )
        monkeypatch.setattr(
            feed._fred_fallback, "fetch",
            lambda: {"target_rate_mid": current},
        )
        result = feed.get_probability(target_rate=current, hours_to_expiry=24, direction="above")
        # When target == implied, P(rate >= target) should be ~50%
        assert 0.45 <= result["probability"] <= 0.55

    def test_target_far_above_implied_low_probability(self, feed, monkeypatch):
        current = 4.375
        monkeypatch.setattr(CMEFedWatchFeed, "_fetch_zq_price",
                            lambda self: 100.0 - current)
        monkeypatch.setattr(
            CMEFedWatchFeed, "_next_fomc_date",
            lambda self, today=None: datetime(2026, 6, 17, tzinfo=timezone.utc),
        )
        monkeypatch.setattr(
            feed._fred_fallback, "fetch",
            lambda: {"target_rate_mid": current},
        )
        # 50bps above implied is ~4 sigma → P should be tiny
        result = feed.get_probability(target_rate=current + 0.5, hours_to_expiry=24, direction="above")
        assert result["probability"] < 0.05

    def test_above_below_complement(self, feed, monkeypatch):
        current = 4.375
        monkeypatch.setattr(CMEFedWatchFeed, "_fetch_zq_price",
                            lambda self: 100.0 - current)
        monkeypatch.setattr(
            CMEFedWatchFeed, "_next_fomc_date",
            lambda self, today=None: datetime(2026, 6, 17, tzinfo=timezone.utc),
        )
        monkeypatch.setattr(
            feed._fred_fallback, "fetch",
            lambda: {"target_rate_mid": current},
        )
        above = feed.get_probability(target_rate=current + 0.10, direction="above", hours_to_expiry=24)
        below = feed.get_probability(target_rate=current + 0.10, direction="below", hours_to_expiry=24)
        # P(above) + P(below) should sum to ~1 (modulo the [0.01, 0.99] clip)
        assert abs(above["probability"] + below["probability"] - 1.0) < 0.02


class TestFallback:
    def test_zq_dead_falls_back_to_fred(self, feed, monkeypatch):
        monkeypatch.setattr(CMEFedWatchFeed, "_fetch_zq_price", lambda self: 0.0)
        monkeypatch.setattr(
            feed._fred_fallback, "fetch",
            lambda: {"target_rate_mid": 4.50},
        )
        data = feed.fetch()
        assert data["source"] == "fred_fallback"
        assert data["current_rate"] == 4.50
        assert data["implied_move_bps"] == 0


class TestFomcSchedule:
    def test_next_meeting_after_today(self, feed):
        # Pin "today" to a date well before the schedule end
        today = datetime(2026, 1, 1, tzinfo=timezone.utc)
        nxt = feed._next_fomc_date(today)
        assert nxt > today
        # Should be the first 2026 meeting
        assert nxt.month == 1 and nxt.day == 28

    def test_after_last_meeting_returns_last(self, feed):
        today = datetime(2027, 1, 1, tzinfo=timezone.utc)
        nxt = feed._next_fomc_date(today)
        # End of schedule — returns the final meeting in our list
        assert nxt.year == 2026 and nxt.month == 12
