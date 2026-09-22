"""Unit tests for the tools. market_data is stubbed: no network, no model, no AWS."""
import pytest

import market_data
from tools import get_price_history, get_stock_price


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
    assert out == "AAPL: $227.14 as of 2026-09-22 (previous close $225.00, +0.95%)"


def test_ticker_is_normalized(quotes):
    assert get_stock_price.invoke({"ticker": " aapl "}).startswith("AAPL: $227.14")


def test_first_day_of_trading_has_no_change(quotes):
    assert get_stock_price.invoke({"ticker": "NEWCO"}) == "NEWCO: $10.00 as of 2026-09-22"


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
        "high $230.50, low $195.25"
    )


def test_history_not_found(monkeypatch):
    monkeypatch.setattr(market_data, "get_history", lambda s, p: None)
    assert get_price_history.invoke({"ticker": "XYZ", "period": "5d"}) == "No history found for XYZ"


def test_schemas_the_model_sees():
    assert get_stock_price.name == "get_stock_price"
    assert "ticker" in get_stock_price.args
    assert get_price_history.args["period"]["enum"] == list(market_data.PERIODS)
