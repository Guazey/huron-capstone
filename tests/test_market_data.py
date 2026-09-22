"""Unit tests for market_data. yfinance is replaced with canned DataFrames."""
import pandas as pd
import pytest

import market_data


@pytest.fixture(autouse=True)
def empty_cache():
    market_data._cache.clear()


def bars(closes, start="2026-09-18"):
    index = pd.date_range(start, periods=len(closes), freq="D", tz="America/New_York")
    return pd.DataFrame(
        {"Close": closes, "High": [c + 1 for c in closes], "Low": [c - 1 for c in closes]},
        index=index,
    )


@pytest.mark.parametrize("raw,expected", [
    ("aapl", "AAPL"), (" BRK-B ", "BRK-B"), ("^gspc", "^GSPC"), ("eurusd=x", "EURUSD=X"),
    ("RY.TO", "RY.TO"), ("", None), ("AAPL MSFT", None), ("A" * 13, None), ("$(rm)", None),
])
def test_normalize_ticker(raw, expected):
    assert market_data.normalize_ticker(raw) == expected


def test_quote_uses_last_two_closes(monkeypatch):
    monkeypatch.setattr(market_data, "_history", lambda s, p: bars([100.0, 101.0, 102.5]))
    q = market_data.get_quote("AAPL")
    assert q == {"symbol": "AAPL", "price": 102.5, "previous_close": 101.0, "as_of": "2026-09-20"}


def test_empty_history_means_not_found(monkeypatch):
    monkeypatch.setattr(market_data, "_history", lambda s, p: pd.DataFrame())
    assert market_data.get_quote("ZZZQX") is None
    assert market_data.get_history("ZZZQX", "1mo") is None


def test_history_summary(monkeypatch):
    monkeypatch.setattr(market_data, "_history", lambda s, p: bars([10.0, 14.0, 12.0]))
    h = market_data.get_history("NVDA", "5d")
    assert (h["start_close"], h["end_close"], h["high"], h["low"]) == (10.0, 12.0, 15.0, 9.0)
    assert (h["start_date"], h["end_date"]) == ("2026-09-18", "2026-09-20")


def test_history_rejects_unknown_period():
    with pytest.raises(ValueError):
        market_data.get_history("NVDA", "10y")


def test_cache_hits_within_ttl_then_refetches(monkeypatch):
    calls = []
    monkeypatch.setattr(market_data, "_history", lambda s, p: calls.append(s) or bars([1.0, 2.0]))
    # now() is read when storing and when checking a hit: store@0, check@30, check@61, store@61
    clock = iter([0.0, 30.0, 61.0, 61.0])
    now = lambda: next(clock)  # noqa: E731

    fetch = lambda: market_data._history("AAPL", "5d")  # noqa: E731
    market_data._cached(("q", "AAPL"), fetch, now)   # miss at t=0
    market_data._cached(("q", "AAPL"), fetch, now)   # hit at t=30
    market_data._cached(("q", "AAPL"), fetch, now)   # expired at t=61
    assert len(calls) == 2


class FakeSearch:
    def __init__(self, query, **kwargs):
        self.query = query
        self.quotes = [
            {"symbol": "SPCX", "longname": "Space Exploration Technologies Corp.", "quoteType": "EQUITY",
             "typeDisp": "Equity", "exchDisp": "NASDAQ"},
            {"symbol": "SSPCX=F", "shortname": "Space X Futures", "quoteType": "FUTURE"},
            {"symbol": "SPCEX-USD", "shortname": "tokenized", "quoteType": "CRYPTOCURRENCY"},
            {"symbol": "SPCF", "shortname": "ProShares Ultra SpaceX ", "quoteType": "EQUITY",
             "exchange": "PCX"},
        ]


def test_search_keeps_listed_securities_only(monkeypatch):
    monkeypatch.setattr(market_data.yf, "Search", FakeSearch)
    assert market_data.search("SpaceX") == [
        {"symbol": "SPCX", "name": "Space Exploration Technologies Corp.", "type": "Equity", "exchange": "NASDAQ"},
        {"symbol": "SPCF", "name": "ProShares Ultra SpaceX", "type": "EQUITY", "exchange": "PCX"},
    ]


def test_search_normalizes_and_caps_query(monkeypatch):
    seen = []

    class Recording(FakeSearch):
        def __init__(self, query, **kwargs):
            seen.append(query)
            super().__init__(query, **kwargs)

    monkeypatch.setattr(market_data.yf, "Search", Recording)
    market_data.search("  space   x  " + "y" * 100)
    assert seen[0].startswith("space x y") and len(seen[0]) == market_data.MAX_QUERY_CHARS


def test_search_blank_query_skips_provider(monkeypatch):
    monkeypatch.setattr(market_data.yf, "Search", lambda *a, **k: 1 / 0)
    assert market_data.search("   ") == []
