"""The only file that talks to the market data provider (yfinance today).

Everything else asks this module for plain dicts, so swapping yfinance for a
paid provider (Polygon, Alpaca, Finnhub) means rewriting this file only.

yfinance is unofficial: it scrapes Yahoo, can be rate-limited, and some
quotes are delayed. Results are cached briefly so a chat that asks about the
same ticker three times makes one upstream call, not three.
"""
import re
import time
from datetime import datetime, timezone

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
                "name": _clean_text(item.get("longname") or item.get("shortname")),
                "type": item.get("typeDisp") or item["quoteType"],
                "exchange": item.get("exchDisp") or item.get("exchange") or "",
            })
        return matches

    return _cached(("search", q.lower()), fetch)[:limit]


MOVER_SCREENS = {"gainers": "day_gainers", "losers": "day_losers", "most_active": "most_actives"}
MAX_HEADLINE_CHARS = 200
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]+")


def _clean_text(text, limit: int = MAX_HEADLINE_CHARS) -> str:
    """Third-party text: flatten to one short line of plain text.

    Angle brackets are removed because tools mark their own source URLs as
    <https://...>, and app.py turns exactly those into clickable links; a
    headline must not be able to forge one.
    """
    text = _CONTROL_CHARS.sub(" ", str(text or "")).replace("<", " ").replace(">", " ")
    return " ".join(text.split())[:limit]


def _https_or_none(url) -> str | None:
    return url if isinstance(url, str) and url.startswith("https://") else None


def _utc(seconds_or_iso) -> str | None:
    """Epoch seconds or an ISO string -> 'YYYY-MM-DD HH:MM UTC'."""
    try:
        if isinstance(seconds_or_iso, (int, float)):
            moment = datetime.fromtimestamp(seconds_or_iso, tz=timezone.utc)
        else:
            moment = datetime.fromisoformat(str(seconds_or_iso).replace("Z", "+00:00"))
        return moment.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def get_market_overview(movers_per_list: int = 5) -> dict:
    """US market status, major indexes, and today's biggest movers."""
    def fetch():
        market = yf.Market("US", timeout=REQUEST_TIMEOUT_SECONDS)
        status = market.status or {}
        indexes = [
            {
                "name": v.get("shortName") or k,
                "symbol": v.get("symbol"),
                "price": v.get("regularMarketPrice"),
                "change_pct": v.get("regularMarketChangePercent"),
            }
            for k, v in (market.summary or {}).items()
            if v.get("regularMarketPrice") is not None
        ]
        movers = {}
        for label, screen in MOVER_SCREENS.items():
            quotes = yf.screen(screen, count=movers_per_list).get("quotes", [])
            movers[label] = [
                {
                    "symbol": q.get("symbol"),
                    "name": _clean_text(q.get("shortName") or q.get("longName")),
                    "price": q.get("regularMarketPrice"),
                    "change_pct": q.get("regularMarketChangePercent"),
                }
                for q in quotes
                if q.get("symbol")
            ]
        return {
            "status": status.get("status"),
            "message": _clean_text(status.get("message")),
            "indexes": indexes,
            "movers": movers,
        }

    return _cached(("overview", movers_per_list), fetch)


def _headline_from_ticker_news(item: dict) -> dict | None:
    c = item.get("content") or {}
    title = _clean_text(c.get("title"))
    if not title or c.get("contentType") not in (None, "STORY", "VIDEO"):
        return None
    return {
        "title": title,
        "publisher": _clean_text((c.get("provider") or {}).get("displayName")),
        "published": _utc(c.get("pubDate")),
        "url": _https_or_none((c.get("canonicalUrl") or {}).get("url"))
        or _https_or_none((c.get("clickThroughUrl") or {}).get("url")),
    }


def _headline_from_search_news(item: dict) -> dict | None:
    title = _clean_text(item.get("title"))
    if not title:
        return None
    return {
        "title": title,
        "publisher": _clean_text(item.get("publisher")),
        "published": _utc(item.get("providerPublishTime")),
        "url": _https_or_none(item.get("link")),
    }


def get_news(symbol: str | None = None, limit: int = 8) -> list[dict]:
    """Latest headlines, newest first: for one ticker, or market-wide if symbol is None."""
    def fetch():
        if symbol:
            raw = [_headline_from_ticker_news(n) for n in yf.Ticker(symbol).get_news(count=limit * 2)]
        else:
            # No single market-news feed: combine broad-market search results
            # with S&P 500 news, then de-duplicate by title.
            broad = yf.Search("stock market", max_results=0, news_count=limit,
                              timeout=REQUEST_TIMEOUT_SECONDS).news
            raw = [_headline_from_search_news(n) for n in broad]
            raw += [_headline_from_ticker_news(n) for n in yf.Ticker("^GSPC").get_news(count=limit)]
        seen, headlines = set(), []
        for h in raw:
            if h and h["title"].lower() not in seen:
                seen.add(h["title"].lower())
                headlines.append(h)
        headlines.sort(key=lambda h: h["published"] or "", reverse=True)
        return headlines

    return _cached(("news", symbol), fetch)[:limit]


MAX_SUMMARY_CHARS = 400
PROFILE_FIELDS = {
    "sector": "sector", "industry": "industry", "market_cap": "marketCap",
    "employees": "fullTimeEmployees", "trailing_pe": "trailingPE", "forward_pe": "forwardPE",
    "dividend_yield_pct": "dividendYield", "beta": "beta",
    "week52_high": "fiftyTwoWeekHigh", "week52_low": "fiftyTwoWeekLow",
    "revenue": "totalRevenue", "revenue_growth": "revenueGrowth",
    "earnings_growth": "earningsGrowth", "profit_margin": "profitMargins",
    "analyst_rating": "recommendationKey", "analyst_count": "numberOfAnalystOpinions",
    "target_mean": "targetMeanPrice", "target_high": "targetHighPrice", "target_low": "targetLowPrice",
}


def _number_or_none(value):
    """yfinance mixes None, NaN, and numbers; keep real numbers only."""
    return value if isinstance(value, (int, float)) and value == value else None


def get_profile(symbol: str) -> dict | None:
    """Company profile, valuation, growth, and analyst consensus, or None if unknown."""
    def fetch():
        info = yf.Ticker(symbol).info or {}
        name = info.get("longName") or info.get("shortName")
        if not name:
            return None
        profile = {"symbol": symbol, "name": _clean_text(name)}
        for key, field in PROFILE_FIELDS.items():
            value = info.get(field)
            profile[key] = _clean_text(value) if isinstance(value, str) else _number_or_none(value)
        profile["summary"] = _clean_text(info.get("longBusinessSummary"), MAX_SUMMARY_CHARS)
        return profile

    return _cached(("profile", symbol), fetch)


def today():
    return datetime.now(timezone.utc).date()


def get_earnings(symbol: str, quarters: int = 4) -> dict | None:
    """Next earnings date with consensus, plus recent quarters' EPS vs estimate."""
    def fetch():
        t = yf.Ticker(symbol)
        try:
            dates = t.get_earnings_dates(limit=quarters + 4)
        except Exception:  # noqa: BLE001 - yfinance raises assorted errors for no data
            dates = None
        if dates is None or dates.empty:
            return None
        upcoming, reported = None, []
        for when, row in dates.sort_index().iterrows():
            eps_actual = _number_or_none(row.get("Reported EPS"))
            entry = {
                "date": when.date().isoformat(),
                "eps_estimate": _number_or_none(row.get("EPS Estimate")),
                "eps_actual": eps_actual,
                "surprise_pct": _number_or_none(row.get("Surprise(%)")),
            }
            if eps_actual is not None:
                reported.append(entry)
            elif when.date() >= today() and upcoming is None:
                upcoming = entry  # earliest future date
        return {"symbol": symbol, "next": upcoming, "recent": reported[-quarters:][::-1]}

    return _cached(("earnings", symbol, quarters), fetch)


if __name__ == "__main__":
    # Live check against Yahoo: needs internet, no AWS.
    print(get_quote("AAPL"))
    print(get_quote("ZZZQX"))
    print(get_history("NVDA", "1mo"))
    print(search("SpaceX"))
    print(get_market_overview())
    for h in get_news():
        print(h)
    print(get_news("NVDA", limit=3))
    print(normalize_ticker(" brk-b "), normalize_ticker("DROP TABLE"))
