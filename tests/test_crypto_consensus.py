"""Tests for the multi-source BTC consensus logic.

These tests stub out network calls so they're fully offline.
"""

import pytest

from data.external_feeds import CryptoFeed


def test_uses_median_when_both_sources_alive(monkeypatch):
    monkeypatch.setattr(CryptoFeed, "_fetch_kraken_btc", staticmethod(lambda: 70_500.0))
    price, sources = CryptoFeed._consensus_btc(coingecko_price=70_000.0)
    assert sources == ["coingecko", "kraken"]
    # 2-element median = average
    assert price == pytest.approx(70_250.0)


def test_falls_back_to_kraken_when_coingecko_dead(monkeypatch):
    monkeypatch.setattr(CryptoFeed, "_fetch_kraken_btc", staticmethod(lambda: 71_000.0))
    price, sources = CryptoFeed._consensus_btc(coingecko_price=0.0)
    assert sources == ["kraken"]
    assert price == 71_000.0


def test_falls_back_to_coingecko_when_kraken_dead(monkeypatch):
    monkeypatch.setattr(CryptoFeed, "_fetch_kraken_btc", staticmethod(lambda: 0.0))
    price, sources = CryptoFeed._consensus_btc(coingecko_price=69_500.0)
    assert sources == ["coingecko"]
    assert price == 69_500.0


def test_returns_zero_when_both_dead(monkeypatch):
    monkeypatch.setattr(CryptoFeed, "_fetch_kraken_btc", staticmethod(lambda: 0.0))
    price, sources = CryptoFeed._consensus_btc(coingecko_price=0.0)
    assert price == 0.0
    assert sources == []


def test_disagreement_warning_still_returns_median(monkeypatch, caplog):
    # 10% spread — well over the 3% sanity threshold
    monkeypatch.setattr(CryptoFeed, "_fetch_kraken_btc", staticmethod(lambda: 77_000.0))
    with caplog.at_level("WARNING"):
        price, sources = CryptoFeed._consensus_btc(coingecko_price=70_000.0)
    assert price == pytest.approx(73_500.0)
    assert any("disagreement" in r.message for r in caplog.records)
