from typing import Literal

from langchain_core.tools import tool

import market_data


def _pct(new: float, old: float) -> str:
    return f"{(new - old) / old * 100:+.2f}%"


@tool
def get_stock_price(ticker: str) -> str:
    """Look up the latest price for a stock, ETF, or index ticker (e.g. AAPL, SPY, ^GSPC)."""
    symbol = market_data.normalize_ticker(ticker)
    if symbol is None:
        return f"No price found for {ticker}: not a valid ticker symbol"
    quote = market_data.get_quote(symbol)
    if quote is None:
        return f"No price found for {symbol}"
    line = f"{symbol}: ${quote['price']:.2f} as of {quote['as_of']}"
    if quote["previous_close"]:
        line += (
            f" (previous close ${quote['previous_close']:.2f}, "
            f"{_pct(quote['price'], quote['previous_close'])})"
        )
    return line


@tool
def get_price_history(
    ticker: str, period: Literal["5d", "1mo", "3mo", "6mo", "1y", "5y"]
) -> str:
    """Summarize how a ticker's price moved over a period: start, end, % change, high, low."""
    symbol = market_data.normalize_ticker(ticker)
    if symbol is None:
        return f"No history found for {ticker}: not a valid ticker symbol"
    h = market_data.get_history(symbol, period)
    if h is None:
        return f"No history found for {symbol}"
    return (
        f"{symbol} over {period} ({h['start_date']} to {h['end_date']}): "
        f"${h['start_close']:.2f} -> ${h['end_close']:.2f} "
        f"({_pct(h['end_close'], h['start_close'])}), "
        f"high ${h['high']:.2f}, low ${h['low']:.2f}"
    )


@tool
def search_ticker(query: str) -> str:
    """Find the ticker for a company, fund, or index by name (e.g. "SpaceX", "Rocket Lab", "S&P 500").

    Use this whenever the user names a company instead of a ticker, or to check
    whether a company is publicly traded at all.
    """
    matches = market_data.search(query)
    if not matches:
        return f"No listed securities found for {query!r}"
    lines = [f"Listed securities matching {query!r}, most relevant first:"]
    for m in matches:
        lines.append(f"- {m['symbol']}: {m['name']} ({m['type']}, {m['exchange']})")
    return "\n".join(lines)


TOOLS = [search_ticker, get_stock_price, get_price_history]


if __name__ == "__main__":
    # Live check: calls Yahoo through market_data, no model involved.
    print(get_stock_price.invoke({"ticker": "AAPL"}))
    print(get_stock_price.invoke({"ticker": "nvda"}))
    print(get_stock_price.invoke({"ticker": "ZZZQX"}))
    print(get_price_history.invoke({"ticker": "TSLA", "period": "1mo"}))
    print("---schemas the model will see---")
    for t in TOOLS:
        print(t.name, "|", t.description, "|", t.args)
