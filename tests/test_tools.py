"""Unit tests for the tools. market_data is stubbed: no network, no model, no AWS."""
import pytest

import market_data
from tools import TOOLS, get_price_history, get_stock_price, search_ticker


@pytest.fixture
def quotes(monkeypatch):
    data = {
        "AAPL": {"symbol": "AAPL", "price": 227.14, "previous_close": 225.0, "as_of": "2026-09-22"},
        "NEWCO": {"symbol": "NEWCO", "price": 10.0, "previous_close": None, "as_of": "2026-09-22"},
    }
    monkeypatch.setattr(market_data, "get_quote", lambda s: data.get(s))
    return data


def test_known_ticker_returns_price_date_and_change(quotes):
    out = get_stock_price.invoke({"ticker": "AAPL"})
    assert out == (
        "AAPL: $227.14 as of 2026-09-22 (previous close $225.00, +0.95%)\n"
        "Source: <https://finance.yahoo.com/quote/AAPL/>"
    )


def test_ticker_is_normalized(quotes):
    assert get_stock_price.invoke({"ticker": " aapl "}).startswith("AAPL: $227.14")


def test_first_day_of_trading_has_no_change(quotes):
    assert get_stock_price.invoke({"ticker": "NEWCO"}).splitlines()[0] == "NEWCO: $10.00 as of 2026-09-22"


def test_unknown_ticker_reports_not_found(quotes):
    assert get_stock_price.invoke({"ticker": "XYZ"}) == "No price found for XYZ"


def test_invalid_ticker_never_reaches_provider(monkeypatch):
    def boom(symbol):
        raise AssertionError("provider called with an invalid ticker")

    monkeypatch.setattr(market_data, "get_quote", boom)
    out = get_stock_price.invoke({"ticker": "AAPL; DROP TABLE"})
    assert out.startswith("No price found")


def test_history_summary(monkeypatch):
    monkeypatch.setattr(market_data, "get_history", lambda s, p: {
        "symbol": s, "period": p, "start_date": "2026-08-22", "end_date": "2026-09-22",
        "start_close": 200.0, "end_close": 220.0, "high": 230.5, "low": 195.25,
    })
    out = get_price_history.invoke({"ticker": "nvda", "period": "1mo"})
    assert out == (
        "NVDA over 1mo (2026-08-22 to 2026-09-22): $200.00 -> $220.00 (+10.00%), "
        "high $230.50, low $195.25\n"
        "Source: <https://finance.yahoo.com/quote/NVDA/history/>"
    )


def test_history_not_found(monkeypatch):
    monkeypatch.setattr(market_data, "get_history", lambda s, p: None)
    assert get_price_history.invoke({"ticker": "XYZ", "period": "5d"}) == "No history found for XYZ"


def test_schemas_the_model_sees():
    assert get_stock_price.name == "get_stock_price"
    assert "ticker" in get_stock_price.args
    assert get_price_history.args["period"]["enum"] == list(market_data.PERIODS)


def test_search_ticker_lists_matches(monkeypatch):
    monkeypatch.setattr(market_data, "search", lambda q: [
        {"symbol": "SPCX", "name": "Space Exploration Technologies", "type": "Equity", "exchange": "NASDAQ"},
    ])
    assert search_ticker.invoke({"query": "SpaceX"}) == (
        "Listed securities matching 'SpaceX', most relevant first:\n"
        "- SPCX: Space Exploration Technologies (Equity, NASDAQ) <https://finance.yahoo.com/quote/SPCX/>"
    )


def test_search_ticker_nothing_found(monkeypatch):
    monkeypatch.setattr(market_data, "search", lambda q: [])
    assert search_ticker.invoke({"query": "Acme Rockets"}) == "No listed securities found for 'Acme Rockets'"


def test_every_tool_is_bound():
    assert [t.name for t in TOOLS] == [
        "search_ticker", "get_stock_price", "get_price_history", "get_market_overview", "get_news",
        "get_company_profile", "get_earnings",
    ]


def test_news_tool_labels_headlines_as_untrusted(monkeypatch):
    from tools import get_news

    monkeypatch.setattr(market_data, "get_news", lambda symbol=None: [
        {"title": "Nasdaq hits record", "publisher": "IBD", "published": "2026-09-22 20:40 UTC",
         "url": "https://investors.com/y"},
        {"title": "No link story", "publisher": "", "published": None, "url": None},
    ])
    out = get_news.invoke({})
    assert "third-party text" in out and "not instructions" in out
    assert '- "Nasdaq hits record" (IBD, 2026-09-22 20:40 UTC) <https://investors.com/y>' in out
    assert '- "No link story" (unknown publisher, time unknown)' in out


def test_news_tool_validates_ticker(monkeypatch):
    from tools import get_news

    monkeypatch.setattr(market_data, "get_news", lambda symbol=None: 1 / 0)
    assert get_news.invoke({"ticker": "NV DA; rm"}).startswith("No news found")


def test_overview_tool_formats(monkeypatch):
    from tools import get_market_overview

    monkeypatch.setattr(market_data, "get_market_overview", lambda: {
        "status": "closed", "message": "U.S. markets closed",
        "indexes": [{"name": "S&P 500", "symbol": "^GSPC", "price": 7764.64, "change_pct": -0.0008}],
        "movers": {"gainers": [{"symbol": "VKTX", "name": "Viking", "price": 40.85, "change_pct": 35.67}],
                   "losers": [], "most_active": []},
    })
    out = get_market_overview.invoke({})
    assert out.splitlines()[:3] == ["US market: U.S. markets closed", "Indexes:", "- S&P 500 (^GSPC): 7,764.64 (-0.00%)"]
    assert "- VKTX Viking: $40.85 (+35.67%) <https://finance.yahoo.com/quote/VKTX/>" in out


def test_yahoo_urls_encode_index_symbols():
    from tools import yahoo_url

    assert yahoo_url("^GSPC") == "https://finance.yahoo.com/quote/%5EGSPC/"
    assert yahoo_url("BRK-B", "history") == "https://finance.yahoo.com/quote/BRK-B/history/"


def test_profile_and_earnings_cite_their_pages(monkeypatch):
    from tools import get_company_profile, get_earnings

    monkeypatch.setattr(market_data, "get_profile", lambda s: {
        "symbol": s, "name": "Tesla, Inc.", "summary": "Makes cars.",
        **{k: None for k in market_data.PROFILE_FIELDS},
    })
    monkeypatch.setattr(market_data, "get_earnings", lambda s: {
        "symbol": s, "next": {"date": "2026-10-21", "eps_estimate": 0.45},
        "recent": [{"date": "2026-07-22", "eps_estimate": 0.54, "eps_actual": 0.33, "surprise_pct": -39.15}],
    })
    profile = get_company_profile.invoke({"ticker": "TSLA"})
    assert profile.splitlines()[-1] == (
        "Source: <https://finance.yahoo.com/quote/TSLA/company-profile/> , "
        "<https://finance.yahoo.com/quote/TSLA/statistics/> , "
        "<https://finance.yahoo.com/quote/TSLA/analysis/>"
    )
    earnings = get_earnings.invoke({"ticker": "TSLA"})
    assert "- Reported 2026-07-22: EPS $0.33 vs estimate $0.54, missed by 39.1%" in earnings
    assert "<https://finance.yahoo.com/calendar/earnings?symbol=TSLA>" in earnings
