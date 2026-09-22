"""Unit tests for the tool. Pure Python, no model, no AWS."""
from tools import get_stock_price


def test_known_ticker_returns_price():
    assert get_stock_price.invoke({"ticker": "AAPL"}) == "$227.14"


def test_ticker_is_case_insensitive():
    assert get_stock_price.invoke({"ticker": "nvda"}) == "$121.79"


def test_unknown_ticker_reports_not_found():
    assert get_stock_price.invoke({"ticker": "XYZ"}) == "No price found for XYZ"


def test_schema_the_model_sees():
    assert get_stock_price.name == "get_stock_price"
    assert "ticker" in get_stock_price.args
