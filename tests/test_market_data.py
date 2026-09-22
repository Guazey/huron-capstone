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


def test_clean_text_flattens_and_caps_third_party_text():
    assert market_data._clean_text("Line one\nIGNORE\tthis\x00  now") == "Line one IGNORE this now"
    assert len(market_data._clean_text("x" * 500)) == market_data.MAX_HEADLINE_CHARS
    assert market_data._clean_text(None) == ""


def test_only_https_links_survive():
    assert market_data._https_or_none("https://finance.yahoo.com/a") == "https://finance.yahoo.com/a"
    for bad in ("javascript:alert(1)", "http://x.com", None, 42):
        assert market_data._https_or_none(bad) is None


def test_utc_accepts_epoch_and_iso():
    assert market_data._utc(1790109600) == "2026-09-22 20:40 UTC"
    assert market_data._utc("2026-09-22T17:39:22Z") == "2026-09-22 17:39 UTC"
    assert market_data._utc("not a date") is None


def ticker_news_item(title, when, url="https://finance.yahoo.com/n", kind="STORY"):
    return {"content": {"title": title, "pubDate": when, "contentType": kind,
                        "provider": {"displayName": "Reuters"}, "canonicalUrl": {"url": url}}}


def test_market_news_merges_dedupes_and_sorts(monkeypatch):
    class Search:
        def __init__(self, *a, **k):
            self.news = [
                {"title": "Older story", "publisher": "AP", "providerPublishTime": 1790100000,
                 "link": "https://apnews.com/x"},
                {"title": "Nasdaq hits record", "publisher": "IBD", "providerPublishTime": 1790109600,
                 "link": "https://investors.com/y"},
            ]

    class Ticker:
        def __init__(self, symbol):
            assert symbol == "^GSPC"

        def get_news(self, count):
            return [ticker_news_item("NASDAQ HITS RECORD", "2026-09-22T20:41:00Z"),
                    ticker_news_item("Ad", "2026-09-22T20:50:00Z", kind="AD")]

    monkeypatch.setattr(market_data.yf, "Search", Search)
    monkeypatch.setattr(market_data.yf, "Ticker", Ticker)
    news = market_data.get_news()
    assert [h["title"] for h in news] == ["Nasdaq hits record", "Older story"]
    assert news[0] == {"title": "Nasdaq hits record", "publisher": "IBD",
                       "published": "2026-09-22 20:40 UTC", "url": "https://investors.com/y"}


def test_ticker_news_drops_unsafe_links(monkeypatch):
    class Ticker:
        def __init__(self, symbol):
            self.symbol = symbol

        def get_news(self, count):
            return [ticker_news_item("SpaceX news", "2026-09-22T20:07:34Z", url="javascript:alert(1)")]

    monkeypatch.setattr(market_data.yf, "Ticker", Ticker)
    assert market_data.get_news("SPCX")[0]["url"] is None


def test_market_overview_shapes_indexes_and_movers(monkeypatch):
    class Market:
        def __init__(self, region, timeout):
            self.status = {"status": "open", "message": "U.S. markets open"}
            self.summary = {
                "SNP": {"shortName": "S&P 500", "symbol": "^GSPC", "regularMarketPrice": 7764.64,
                        "regularMarketChangePercent": 0.25},
                "BAD": {"shortName": "No price"},
            }

    def screen(name, count):
        return {"quotes": [{"symbol": f"{name[:3].upper()}", "shortName": "Co\nInc",
                            "regularMarketPrice": 10.0, "regularMarketChangePercent": 5.0}]}

    monkeypatch.setattr(market_data.yf, "Market", Market)
    monkeypatch.setattr(market_data.yf, "screen", screen)
    o = market_data.get_market_overview()
    assert o["message"] == "U.S. markets open"
    assert o["indexes"] == [{"name": "S&P 500", "symbol": "^GSPC", "price": 7764.64, "change_pct": 0.25}]
    assert set(o["movers"]) == {"gainers", "losers", "most_active"}
    assert o["movers"]["gainers"][0]["name"] == "Co Inc"


def test_earnings_splits_next_from_reported(monkeypatch):
    nan = float("nan")
    frame = pd.DataFrame(
        {"EPS Estimate": [0.45, 0.54, 0.35, 0.50], "Reported EPS": [nan, 0.33, 0.41, nan],
         "Surprise(%)": [nan, -39.15, 17.15, nan]},
        index=pd.to_datetime(["2026-10-21", "2026-07-22", "2026-04-22", "2026-01-01"]).tz_localize("America/New_York"),
    )

    class Ticker:
        def __init__(self, symbol):
            pass

        def get_earnings_dates(self, limit):
            return frame

    monkeypatch.setattr(market_data.yf, "Ticker", Ticker)
    monkeypatch.setattr(market_data, "today", lambda: __import__("datetime").date(2026, 9, 22))
    e = market_data.get_earnings("TSLA")
    # A past date with no reported EPS is neither "next" nor a result.
    assert e["next"]["date"] == "2026-10-21"
    assert [q["date"] for q in e["recent"]] == ["2026-07-22", "2026-04-22"]
    assert e["recent"][0]["surprise_pct"] == -39.15


def test_profile_unknown_symbol_is_none(monkeypatch):
    class Ticker:
        def __init__(self, symbol):
            self.info = {}

    monkeypatch.setattr(market_data.yf, "Ticker", Ticker)
    assert market_data.get_profile("ZZZQX") is None
