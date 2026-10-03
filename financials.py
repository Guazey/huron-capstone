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
    "revenue", "cost_of_revenue", "gross_profit", "operating_income", "net_income", "eps_diluted",
    "research_and_development", "operating_cash_flow", "capex", "depreciation_amortization",
    "dividends_paid", "cash", "accounts_receivable", "inventory", "current_assets", "ppe_net",
    "total_assets", "accounts_payable", "current_liabilities", "total_liabilities",
    "long_term_debt", "stockholders_equity",
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
    "cost_of_revenue": ("CostOfGoodsAndServicesSold", "CostOfRevenue", "CostOfGoodsSold",
                        "CostOfGoodsAndServiceExcludingDepreciationDepletionAndAmortization"),
    "gross_profit": ("GrossProfit",),
    "operating_income": ("OperatingIncomeLoss",),
    "net_income": ("NetIncomeLoss", "ProfitLoss"),
    "eps_diluted": ("EarningsPerShareDiluted",),
    "operating_cash_flow": ("NetCashProvidedByUsedInOperatingActivities",),
    "capex": ("PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets"),
    "research_and_development": ("ResearchAndDevelopmentExpense",),
    "depreciation_amortization": ("DepreciationDepletionAndAmortization", "DepreciationAndAmortization",
                                  "DepreciationAmortizationAndAccretionNet", "Depreciation"),
    "dividends_paid": ("PaymentsOfDividends", "PaymentsOfDividendsCommonStock"),
    "cash": ("CashAndCashEquivalentsAtCarryingValue",),
    "accounts_receivable": ("AccountsReceivableNetCurrent", "ReceivablesNetCurrent"),
    "inventory": ("InventoryNet", "InventoryFinishedGoodsNetOfReserves"),
    "current_assets": ("AssetsCurrent",),
    "ppe_net": ("PropertyPlantAndEquipmentNet",
                "PropertyPlantAndEquipmentAndFinanceLeaseRightOfUseAssetAfterAccumulatedDepreciationAndAmortization"),
    "total_assets": ("Assets",),
    "accounts_payable": ("AccountsPayableCurrent", "AccountsPayableTradeCurrent"),
    "current_liabilities": ("LiabilitiesCurrent",),
    "total_liabilities": ("Liabilities",),
    "stockholders_equity": ("StockholdersEquity",),
    "long_term_debt": ("LongTermDebtNoncurrent", "LongTermDebt"),
}
LABELS = {
    "revenue": "Revenue", "cost_of_revenue": "Cost of revenue (COGS)", "gross_profit": "Gross profit",
    "operating_income": "Operating income", "net_income": "Net income", "eps_diluted": "EPS (diluted)",
    "research_and_development": "R&D expense", "operating_cash_flow": "Operating cash flow",
    "capex": "Capital expenditures", "depreciation_amortization": "Depreciation and amortization (cash flow)",
    "dividends_paid": "Dividends paid", "cash": "Cash and equivalents",
    "accounts_receivable": "Accounts receivable, net", "inventory": "Inventory",
    "current_assets": "Total current assets", "ppe_net": "Property, plant and equipment, net",
    "total_assets": "Total assets", "accounts_payable": "Accounts payable",
    "current_liabilities": "Total current liabilities", "total_liabilities": "Total liabilities",
    "long_term_debt": "Long-term debt", "stockholders_equity": "Stockholders' equity",
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


def first_reports(us_gaap: dict) -> dict[tuple, dict]:
    """(start, end) -> the earliest-filed fact for that period, across every tag the company files.

    Fiscal labels come from here, not from one metric's facts: when a company
    switches tags, a metric's first fact for an old period sits in a later
    filing, but some other tag was reported for it in the period's own filing.
    """
    first: dict[tuple, dict] = {}
    for concept in us_gaap.values():
        for facts in (concept.get("units") or {}).values():
            for f in facts:
                key = (f.get("start"), f["end"])
                if key not in first or f["filed"] < first[key]["filed"]:
                    first[key] = f
    return first


# Financial statements compare three years (income, cash flow) or two
# (balance sheet), so a period stays in the statements of the next two or
# three 10-Ks. A restatement there is real. A value tagged for the period in
# a filing years later comes from some other schedule, and has been wrong:
# General Mills' 2026 10-K tagged FY2022 net income with the figure that
# includes noncontrolling interests.
STATEMENT_WINDOW_DAYS = 3 * 365 + 120  # three fiscal years, plus the 10-K filing lag


def _in_statements(f: dict) -> bool:
    return (date.fromisoformat(f["filed"]) - date.fromisoformat(f["end"])).days <= STATEMENT_WINDOW_DAYS


def select_periods(facts: list[dict], period: PeriodType, count: int,
                   through_fy: int | None = None, labels: dict[tuple, dict] | None = None) -> list[dict]:
    """The latest `count` distinct periods, newest first, optionally ending at a fiscal year.

    The same period is reported again in later filings (as the prior-year
    comparison, or restated); the most recently filed value wins, and a
    restated value keeps the one first reported as "original".
    `labels` is first_reports() for the whole company; without it, fiscal
    labels come from these facts alone.
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
        if key not in first or f["filed"] < first[key]["filed"]:
            first[key] = f
        if not _in_statements(f):
            continue
        if key not in latest or f["filed"] > latest[key]["filed"]:
            latest[key] = f
    # A period whose only facts are from distant filings still gets a value.
    for key, f in first.items():
        latest.setdefault(key, f)
    periods = []
    for key, f in latest.items():
        fy, label = fiscal_label((labels or first).get(key, first[key]), period)
        if through_fy is None or (fy or int(f["end"][:4])) <= through_fy:
            original = first[key] if first[key]["val"] != f["val"] else None
            periods.append({**f, "fiscal": label, "original": original})
    return sorted(periods, key=lambda f: f["end"], reverse=True)[:count]


def fiscal_label(first_report: dict, period: PeriodType) -> tuple[int | None, str | None]:
    """The company's own fiscal year and name for a period (2027, "Q1 FY2027"), or (None, None).

    A fact's fy/fp describe the filing it appeared in, not the period: a
    10-K's prior-year comparison carries the new year's fy. The first filing
    to report a period is that period's own report, so its fy/fp name it.
    Fiscal years don't follow the calendar (NVIDIA's FY2027 began in
    January 2026), which is why this is read from the filing, never guessed.
    Periods from before a company filed XBRL (about 2009-2011) first appear
    as comparisons in a later filing; a label more than a year from the
    period's end date is one of those, so it is dropped rather than shown.
    """
    fy, fp, form = first_report.get("fy"), first_report.get("fp") or "", first_report.get("form")
    if not isinstance(fy, int) or abs(fy - int(first_report["end"][:4])) > 1:
        return None, None
    instant = "start" not in first_report
    if period == "annual" and fp == "FY" and form in ANNUAL_FORMS:
        return fy, f"FY{fy} year end" if instant else f"FY{fy}"
    if period == "quarterly" and fp.startswith("Q") and form == "10-Q":
        return fy, f"{fp} FY{fy} end" if instant else f"{fp} FY{fy}"
    return None, None


def _tags_for(us_gaap: dict, metric: str) -> list[tuple[str, str, list[dict]]]:
    """(tag, unit, facts) for each of a metric's tags the company uses, in CONCEPTS priority order.

    Order matters when a company reports two of them for the same period:
    AMD files both DepreciationDepletionAndAmortization ($167M, the cash
    flow line) and Depreciation ($94M, one part of it). The more complete
    concept is listed first and wins; later tags only fill periods it lacks.
    """
    found = []
    for tag in CONCEPTS[metric]:
        units = (us_gaap.get(tag) or {}).get("units") or {}
        for unit in UNITS:
            facts = units.get(unit)
            if facts:
                found.append((tag, unit, facts))
                break
    return found


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


def get_financials(symbol: str, metrics, period: PeriodType = "annual", count: int = 4,
                   through_fy: int | None = None) -> dict | None:
    """Reported values per metric, newest period first; None if the company doesn't file XBRL.

    through_fy limits the periods to that fiscal year and earlier, for history.

    {"symbol", "name", "cik", "metrics": {metric: {"tag", "unit", "values": [
        {"start", "end", "value", "fiscal", "tag", "unit", "form", "filed", "url",
         "original": None or {"value", "form", "filed", "url"}}, ...]}}}
    A metric the company doesn't report is left out.
    """
    company = lookup_cik(symbol)
    if company is None:
        return None
    us_gaap = (company_facts(company["cik"]).get("facts") or {}).get("us-gaap")
    if not us_gaap:
        return None
    labels = first_reports(us_gaap)
    found = {}
    for metric in metrics:
        tags = _tags_for(us_gaap, metric)
        if not tags:
            continue
        # Companies switch tags over time (Apple's revenue was "Revenues"
        # until 2018). Each period takes the first tag, in priority order,
        # that has it; each value names its tag.
        by_period: dict[tuple, dict] = {}
        for tag, unit, facts in tags:
            for f in select_periods(facts, period, count, through_fy, labels):
                by_period.setdefault((f.get("start"), f["end"]), {**f, "tag": tag, "unit": unit})
        picked = sorted(by_period.values(), key=lambda f: f["end"], reverse=True)[:count]
        if picked:
            found[metric] = {"tag": picked[0]["tag"], "unit": picked[0]["unit"], "values": [
                {"start": f.get("start"), "end": f["end"], "value": f["val"], "fiscal": f["fiscal"],
                 "tag": f["tag"], "unit": f["unit"], "form": f["form"], "filed": f["filed"],
                 "url": filing_index_url(company["cik"], f["accn"]),
                 "original": f["original"] and {
                     "value": f["original"]["val"], "form": f["original"]["form"],
                     "filed": f["original"]["filed"],
                     "url": filing_index_url(company["cik"], f["original"]["accn"])}}
                for f in picked
            ]}
    return {"symbol": symbol.upper(), "name": company["name"], "cik": company["cik"], "metrics": found}


if __name__ == "__main__":
    # Live check against the SEC: needs internet and SEC_USER_AGENT, no AWS.
    from dotenv import load_dotenv

    load_dotenv()
    checks: tuple[tuple[str, PeriodType], ...] = (("AAPL", "annual"), ("NVDA", "quarterly"), ("SPY", "annual"))
    for sym, per in checks:
        result = get_financials(sym, DEFAULT_METRICS + ("total_assets",), per, 2)
        print(sym, per, result and {m: v["values"] for m, v in result["metrics"].items()})
