"""The only file that talks to the SEC's XBRL API: financial statement figures as reported.

yfinance's fundamentals are Yahoo's derived numbers with no period or filing
attached. These come straight from the XBRL data companies file with the SEC,
so every value carries the period it covers, the form it was reported on, the
filing date, and a link to the filing itself:
https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data

Covers any US company that files 10-Ks, not just the filings-search watchlist.
Foreign filers that report under IFRS (20-F) have no us-gaap facts and return None.
"""
import os
import time
from datetime import date
from typing import Literal, get_args

import requests

SEC = "https://www.sec.gov"
DATA = "https://data.sec.gov"
REQUEST_TIMEOUT_SECONDS = 10
# Reported figures only change when a company files, so cache for an hour.
# The ticker list barely changes; a day is fine.
FACTS_TTL_SECONDS = 3600
TICKERS_TTL_SECONDS = 86400

Metric = Literal[
    "revenue", "gross_profit", "operating_income", "net_income", "eps_diluted",
    "operating_cash_flow", "capex", "research_and_development",
    "cash", "total_assets", "total_liabilities", "stockholders_equity", "long_term_debt",
]
METRICS = get_args(Metric)
DEFAULT_METRICS: tuple[Metric, ...] = ("revenue", "net_income", "eps_diluted", "operating_cash_flow")
PeriodType = Literal["annual", "quarterly"]

# Each metric's us-gaap tags, most common first. Companies switch tags over
# time (Apple reported "Revenues" until 2018), so the tag with the most recent
# fact wins, not the first one listed.
CONCEPTS: dict[str, tuple[str, ...]] = {
    "revenue": ("RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues",
                "SalesRevenueNet", "RevenueFromContractWithCustomerIncludingAssessedTax"),
    "gross_profit": ("GrossProfit",),
    "operating_income": ("OperatingIncomeLoss",),
    "net_income": ("NetIncomeLoss", "ProfitLoss"),
    "eps_diluted": ("EarningsPerShareDiluted",),
    "operating_cash_flow": ("NetCashProvidedByUsedInOperatingActivities",),
    "capex": ("PaymentsToAcquirePropertyPlantAndEquipment",),
    "research_and_development": ("ResearchAndDevelopmentExpense",),
    "cash": ("CashAndCashEquivalentsAtCarryingValue",),
    "total_assets": ("Assets",),
    "total_liabilities": ("Liabilities",),
    "stockholders_equity": ("StockholdersEquity",),
    "long_term_debt": ("LongTermDebtNoncurrent", "LongTermDebt"),
}
LABELS = {
    "revenue": "Revenue", "gross_profit": "Gross profit", "operating_income": "Operating income",
    "net_income": "Net income", "eps_diluted": "EPS (diluted)",
    "operating_cash_flow": "Operating cash flow", "capex": "Capital expenditures",
    "research_and_development": "R&D expense", "cash": "Cash and equivalents",
    "total_assets": "Total assets", "total_liabilities": "Total liabilities",
    "stockholders_equity": "Stockholders' equity", "long_term_debt": "Long-term debt",
}
UNITS = ("USD", "USD/shares")

# A fact covers a duration (start..end, e.g. revenue) or a single day (end
# only, e.g. total assets). Durations are bucketed by length, so a 10-Q's
# year-to-date figure (6 or 9 months) is never mistaken for a quarter.
QUARTER_DAYS = range(80, 101)
YEAR_DAYS = range(350, 381)
ANNUAL_FORMS = {"10-K", "10-K/A", "10-KT"}

_cache: dict[tuple, tuple[float, object]] = {}


def _cached(key, ttl, fetch, now=time.monotonic):
    hit = _cache.get(key)
    if hit and now() - hit[0] < ttl:
        return hit[1]
    value = fetch()
    _cache[key] = (now(), value)
    return value


def _get_json(url: str):
    # The SEC requires a User-Agent naming a real contact, or it returns 403.
    agent = os.environ.get("SEC_USER_AGENT", "").strip()
    if "@" not in agent:
        raise RuntimeError("SEC_USER_AGENT is not set")
    response = requests.get(url, timeout=REQUEST_TIMEOUT_SECONDS, headers={
        "User-Agent": agent, "Accept-Encoding": "gzip, deflate",
    })
    response.raise_for_status()
    return response.json()


def lookup_cik(symbol: str) -> dict | None:
    """Ticker -> {"cik", "name"} from the SEC's own ticker list; None if it doesn't file."""
    def fetch():
        rows = _get_json(f"{SEC}/files/company_tickers.json").values()
        return {r["ticker"].upper(): {"cik": int(r["cik_str"]), "name": r["title"]} for r in rows}

    # The SEC writes share classes with a dash (BRK-B); Yahoo tickers match.
    return _cached(("tickers",), TICKERS_TTL_SECONDS, fetch).get(symbol.upper())


def filing_index_url(cik: int, accession: str) -> str:
    return f"{SEC}/Archives/edgar/data/{cik}/{accession.replace('-', '')}/"


def _days(fact: dict) -> int | None:
    if "start" not in fact:
        return None
    return (date.fromisoformat(fact["end"]) - date.fromisoformat(fact["start"])).days


def select_periods(facts: list[dict], period: PeriodType, count: int) -> list[dict]:
    """The latest `count` distinct periods, newest first.

    The same period is reported again in later filings (as the prior-year
    comparison, or restated); the most recently filed value wins.
    """
    def wanted(f):
        days = _days(f)
        if days is None:  # a balance-sheet date
            return period == "quarterly" or f.get("form") in ANNUAL_FORMS
        return days in (YEAR_DAYS if period == "annual" else QUARTER_DAYS)

    latest: dict[tuple, dict] = {}
    first: dict[tuple, dict] = {}
    for f in facts:
        if not wanted(f) or not isinstance(f.get("val"), (int, float)):
            continue
        key = (f.get("start"), f["end"])
        if key not in latest or f["filed"] > latest[key]["filed"]:
            latest[key] = f
        if key not in first or f["filed"] < first[key]["filed"]:
            first[key] = f
    picked = sorted(latest.values(), key=lambda f: f["end"], reverse=True)[:count]
    return [{**f, "fiscal": fiscal_label(first[(f.get("start"), f["end"])], period)} for f in picked]


def fiscal_label(first_report: dict, period: PeriodType) -> str | None:
    """The company's own name for a period ("Q1 FY2027"), or None if unknown.

    A fact's fy/fp describe the filing it appeared in, not the period: a
    10-K's prior-year comparison carries the new year's fy. The first filing
    to report a period is that period's own report, so its fy/fp name it.
    Fiscal years don't follow the calendar (NVIDIA's FY2027 began in
    January 2026), which is why this is read from the filing, never guessed.
    """
    fy, fp, form = first_report.get("fy"), first_report.get("fp") or "", first_report.get("form")
    if not fy or "start" not in first_report:
        return None
    if period == "annual" and fp == "FY" and form in ANNUAL_FORMS:
        return f"FY{fy}"
    if period == "quarterly" and fp.startswith("Q") and form == "10-Q":
        return f"{fp} FY{fy}"
    return None


def _facts_for(us_gaap: dict, metric: str) -> tuple[str, str, list[dict]] | None:
    """(tag, unit, facts) for the tag with the most recent fact, or None."""
    best = None
    for tag in CONCEPTS[metric]:
        units = (us_gaap.get(tag) or {}).get("units") or {}
        for unit in UNITS:
            facts = units.get(unit)
            if facts:
                newest = max(f["end"] for f in facts)
                if best is None or newest > best[0]:
                    best = (newest, tag, unit, facts)
    return best[1:] if best else None


def company_facts(cik: int) -> dict:
    """Every XBRL fact a company has filed; {} for filers with none (funds, trusts)."""
    def fetch():
        try:
            return _get_json(f"{DATA}/api/xbrl/companyfacts/CIK{cik:010d}.json")
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code == 404:
                return {}
            raise

    return _cached(("facts", cik), FACTS_TTL_SECONDS, fetch)


def get_financials(symbol: str, metrics, period: PeriodType = "annual", count: int = 4) -> dict | None:
    """Reported values per metric, newest period first; None if the company doesn't file XBRL.

    {"symbol", "name", "cik", "metrics": {metric: {"tag", "unit", "values": [
        {"start", "end", "value", "fiscal", "form", "filed", "url"}, ...]}}}
    A metric the company doesn't report is left out.
    """
    company = lookup_cik(symbol)
    if company is None:
        return None
    us_gaap = (company_facts(company["cik"]).get("facts") or {}).get("us-gaap")
    if not us_gaap:
        return None
    found = {}
    for metric in metrics:
        hit = _facts_for(us_gaap, metric)
        if hit is None:
            continue
        tag, unit, facts = hit
        values = [
            {"start": f.get("start"), "end": f["end"], "value": f["val"], "fiscal": f["fiscal"],
             "form": f["form"], "filed": f["filed"], "url": filing_index_url(company["cik"], f["accn"])}
            for f in select_periods(facts, period, count)
        ]
        if values:
            found[metric] = {"tag": tag, "unit": unit, "values": values}
    return {"symbol": symbol.upper(), "name": company["name"], "cik": company["cik"], "metrics": found}


if __name__ == "__main__":
    # Live check against the SEC: needs internet and SEC_USER_AGENT, no AWS.
    from dotenv import load_dotenv

    load_dotenv()
    checks: tuple[tuple[str, PeriodType], ...] = (("AAPL", "annual"), ("NVDA", "quarterly"), ("SPY", "annual"))
    for sym, per in checks:
        result = get_financials(sym, DEFAULT_METRICS + ("total_assets",), per, 2)
        print(sym, per, result and {m: v["values"] for m, v in result["metrics"].items()})
