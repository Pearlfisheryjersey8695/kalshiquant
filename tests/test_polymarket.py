"""Tests for the Polymarket adapter — fully offline, network is stubbed."""

import pytest

from data.polymarket import (
    PolymarketAdapter,
    PolymarketContract,
    CrossVenueQuote,
)


@pytest.fixture
def adapter():
    return PolymarketAdapter()


@pytest.fixture
def fake_markets():
    return [
        PolymarketContract(
            market_id="poly_btc_175k",
            slug="will-bitcoin-reach-175k-by-2026",
            question="Will Bitcoin reach $175,000 by December 31, 2026?",
            yes_price=0.18,
            no_price=0.82,
            volume_24h=250_000,
            end_date="2026-12-31",
        ),
        PolymarketContract(
            market_id="poly_fed_jun",
            slug="fed-rate-cut-june",
            question="Will the Fed cut rates at the June 2026 FOMC?",
            yes_price=0.45,
            no_price=0.55,
            volume_24h=100_000,
            end_date="2026-06-17",
        ),
        PolymarketContract(
            market_id="poly_random",
            slug="some-random-event",
            question="Will Italy win Eurovision 2026?",
            yes_price=0.12,
            no_price=0.88,
            volume_24h=5_000,
            end_date="2026-05-15",
        ),
    ]


class TestMatching:
    def test_btc_kalshi_matches_btc_polymarket(self, adapter, fake_markets, monkeypatch):
        monkeypatch.setattr(adapter, "fetch_active_markets", lambda: fake_markets)
        match = adapter.find_match(
            "KXBTCMAX-26DEC31-T175000",
            "Bitcoin price above $175,000 on Dec 31 2026",
            min_confidence=0.10,
        )
        assert match is not None
        contract, conf = match
        assert contract.market_id == "poly_btc_175k"
        assert conf >= 0.10

    def test_fed_kalshi_matches_fed_polymarket(self, adapter, fake_markets, monkeypatch):
        monkeypatch.setattr(adapter, "fetch_active_markets", lambda: fake_markets)
        match = adapter.find_match(
            "KXFED-26JUN-T450",
            "Fed rate at 4.50% after June FOMC",
            min_confidence=0.10,
        )
        assert match is not None
        contract, conf = match
        assert contract.market_id == "poly_fed_jun"

    def test_no_match_below_threshold(self, adapter, fake_markets, monkeypatch):
        monkeypatch.setattr(adapter, "fetch_active_markets", lambda: fake_markets)
        match = adapter.find_match(
            "KXNFL-W12-DAL",
            "Will the Cowboys cover the spread in Week 12?",
            min_confidence=0.5,  # high bar — nothing in the catalog matches
        )
        assert match is None


class TestArbDetection:
    def test_no_arb_when_prices_align(self, adapter, fake_markets, monkeypatch):
        monkeypatch.setattr(adapter, "fetch_active_markets", lambda: fake_markets)
        # Kalshi at 0.20, Polymarket at 0.18 — 2c gap, below threshold
        quote = adapter.detect_arb(
            "KXBTCMAX-26DEC31-T175000",
            "Bitcoin price above $175,000 on Dec 31 2026",
            kalshi_yes_price=0.20,
        )
        assert quote is not None
        assert quote.arb_direction is None  # too small for arb

    def test_arb_when_kalshi_cheaper(self, adapter, fake_markets, monkeypatch):
        monkeypatch.setattr(adapter, "fetch_active_markets", lambda: fake_markets)
        # Kalshi at 0.10, Poly at 0.18 — 8c gap, above threshold
        quote = adapter.detect_arb(
            "KXBTCMAX-26DEC31-T175000",
            "Bitcoin price above $175,000 on Dec 31 2026",
            kalshi_yes_price=0.10,
        )
        assert quote is not None
        assert quote.arb_direction == "BUY_KALSHI_YES"
        assert quote.edge < 0  # negative edge -> Kalshi is cheaper

    def test_arb_when_poly_cheaper(self, adapter, fake_markets, monkeypatch):
        monkeypatch.setattr(adapter, "fetch_active_markets", lambda: fake_markets)
        quote = adapter.detect_arb(
            "KXBTCMAX-26DEC31-T175000",
            "Bitcoin price above $175,000 on Dec 31 2026",
            kalshi_yes_price=0.30,
        )
        assert quote is not None
        assert quote.arb_direction == "BUY_POLY_YES"
        assert quote.edge > 0


class TestNetworkRobustness:
    def test_returns_empty_list_on_http_failure(self, adapter, monkeypatch):
        monkeypatch.setattr(PolymarketAdapter, "_http_get", staticmethod(lambda url, timeout=10: None))
        # Wipe cache so the failure path is exercised
        adapter._markets_cache = []
        adapter._cache_ts = 0
        markets = adapter.fetch_active_markets()
        assert markets == []

    def test_handles_malformed_market_rows(self, adapter, monkeypatch):
        bad_data = [
            {"id": 1},  # missing prices
            {"id": 2, "outcomePrices": "not json"},
            {"id": 3, "outcomePrices": '["0.55","0.45"]', "question": "Good?"},
        ]
        monkeypatch.setattr(PolymarketAdapter, "_http_get", staticmethod(lambda url, timeout=10: bad_data))
        adapter._markets_cache = []
        adapter._cache_ts = 0
        markets = adapter.fetch_active_markets()
        # Only the well-formed row should survive
        assert len(markets) == 1
        assert markets[0].yes_price == 0.55


class TestScanArbs:
    def test_scans_multiple_kalshi_markets(self, adapter, fake_markets, monkeypatch):
        monkeypatch.setattr(adapter, "fetch_active_markets", lambda: fake_markets)
        kalshi = [
            {"ticker": "KXBTCMAX-26DEC31-T175000",
             "title": "Bitcoin above $175,000 by Dec 31 2026",
             "yes_ask": 0.10},  # cheap on Kalshi -> arb
            {"ticker": "KXFED-26JUN-T450",
             "title": "Fed cut at June 2026 FOMC",
             "yes_ask": 0.45},  # aligned -> no arb
        ]
        arbs = adapter.scan_arbs(kalshi)
        assert len(arbs) == 1
        assert arbs[0].arb_direction == "BUY_KALSHI_YES"
