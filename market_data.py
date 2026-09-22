"""The only file that talks to the market data provider (yfinance today).

Everything else asks this module for plain dicts, so swapping yfinance for a
paid provider (Polygon, Alpaca, Finnhub) means rewriting this file only.

yfinance is unofficial: it scrapes Yahoo, can be rate-limited, and some
quotes are delayed. Results are cached briefly so a chat that asks about the
same ticker three times makes one upstream call, not three.
"""
import re
import time

import yfinance as yf

CACHE_TTL_SECONDS = 60
REQUEST_TIMEOUT_SECONDS = 10

# Periods the history tool accepts. Kept small so the model can't request a
# huge series and so every value is one yfinance understands.
PERIODS = ("5d", "1mo", "3mo", "6mo", "1y", "5y")

# Tickers come from the model, so treat them as untrusted input. Letters,
# digits, and the few symbols real tickers use: BRK-B, ^GSPC, EURUSD=X, RY.TO
_TICKER_RE = re.compile(r"^[A-Z0-9^][A-Z0-9.\-=^]{0,11}$")

_cache: dict[tuple, tuple[float, object]] = {}


def normalize_ticker(ticker: str) -> str | None:
    """Uppercase and validate a ticker; None if it can't be a real symbol."""
    symbol = ticker.strip().upper()
    return symbol if _TICKER_RE.match(symbol) else None


def _cached(key, fetch, now=time.monotonic):
    hit = _cache.get(key)
    if hit and now() - hit[0] < CACHE_TTL_SECONDS:
        return hit[1]
    value = fetch()
    _cache[key] = (now(), value)
    return value


def _history(symbol: str, period: str):
    return yf.Ticker(symbol).history(
        period=period, interval="1d", timeout=REQUEST_TIMEOUT_SECONDS
    )


def get_quote(symbol: str) -> dict | None:
    """Latest price and previous close, or None if the symbol has no data.

    During market hours the last daily bar's close is the current (possibly
    delayed) price; after hours it is that day's close.
    """
    def fetch():
        bars = _history(symbol, "5d")
        if bars.empty:
            return None
        closes = bars["Close"]
        return {
            "symbol": symbol,
            "price": float(closes.iloc[-1]),
            "previous_close": float(closes.iloc[-2]) if len(closes) > 1 else None,
            "as_of": bars.index[-1].date().isoformat(),
        }

    return _cached(("quote", symbol), fetch)


def get_history(symbol: str, period: str) -> dict | None:
    """Summary of daily closes over a period, or None if no data.

    Returns a summary, not the raw series: the model needs the shape of the
    move, and a year of daily rows would cost tokens for nothing.
    """
    if period not in PERIODS:
        raise ValueError(f"period must be one of {PERIODS}, got {period!r}")

    def fetch():
        bars = _history(symbol, period)
        if bars.empty:
            return None
        closes = bars["Close"]
        return {
            "symbol": symbol,
            "period": period,
            "start_date": bars.index[0].date().isoformat(),
            "end_date": bars.index[-1].date().isoformat(),
            "start_close": float(closes.iloc[0]),
            "end_close": float(closes.iloc[-1]),
            "high": float(bars["High"].max()),
            "low": float(bars["Low"].min()),
        }

    return _cached(("history", symbol, period), fetch)


# Search result types worth showing. Futures, options, and tokenized crypto
# copies of stocks match company names too, but aren't what people mean.
SEARCH_TYPES = {"EQUITY", "ETF", "INDEX", "MUTUALFUND"}
MAX_QUERY_CHARS = 60


def search(query: str, limit: int = 5) -> list[dict]:
    """Listed securities matching a company or fund name, most relevant first.

    This is how the agent learns tickers: its own knowledge of which companies
    are public, and under what symbol, stops at its training date.
    """
    q = " ".join(query.split())[:MAX_QUERY_CHARS]
    if not q:
        return []

    def fetch():
        found = yf.Search(
            q, max_results=10, news_count=0, lists_count=0,
            timeout=REQUEST_TIMEOUT_SECONDS,
        ).quotes
        matches = []
        for item in found:
            if item.get("quoteType") not in SEARCH_TYPES or not item.get("symbol"):
                continue
            matches.append({
                "symbol": item["symbol"],
                "name": (item.get("longname") or item.get("shortname") or "").strip(),
                "type": item.get("typeDisp") or item["quoteType"],
                "exchange": item.get("exchDisp") or item.get("exchange") or "",
            })
        return matches

    return _cached(("search", q.lower()), fetch)[:limit]


if __name__ == "__main__":
    # Live check against Yahoo: needs internet, no AWS.
    print(get_quote("AAPL"))
    print(get_quote("ZZZQX"))
    print(get_history("NVDA", "1mo"))
    print(search("SpaceX"))
    print(normalize_ticker(" brk-b "), normalize_ticker("DROP TABLE"))
