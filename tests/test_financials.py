"""Unit tests for the SEC XBRL layer and the get_financials tool. No network."""
import pytest
import requests

import financials
from tools import get_financials

CIK = 320193


def fact(start, end, val, form="10-K", filed="2025-10-31", accn="0000320193-25-000079", fy=None, fp=None):
    f = {"end": end, "val": val, "accn": accn, "form": form, "filed": filed}
    if fy:
        f["fy"], f["fp"] = fy, fp
    if start:
        f["start"] = start
    return f


REVENUE = [
    fact("2023-10-01", "2024-09-28", 391035000000, filed="2024-11-01", accn="0000320193-24-000123", fy=2024, fp="FY"),
    # Restated as the prior-year comparison in the next 10-K, which tags it FY2025.
    fact("2023-10-01", "2024-09-28", 391000000000, filed="2025-10-31", fy=2025, fp="FY"),
    fact("2024-09-29", "2025-09-27", 416161000000, fy=2025, fp="FY"),
    fact("2025-09-28", "2025-12-27", 143756000000, form="10-Q", filed="2026-01-30", fy=2026, fp="Q1"),
    fact("2025-09-28", "2026-03-28", 254940000000, form="10-Q", filed="2026-05-01", fy=2026, fp="Q2"),  # 6-month YTD
    fact("2025-12-28", "2026-03-28", 111184000000, form="10-Q", filed="2026-05-01", fy=2026, fp="Q2"),
]


def test_annual_periods_newest_first_latest_filing_wins():
    picked = financials.select_periods(REVENUE, "annual", 4)
    assert [(f["end"], f["val"], f["fiscal"]) for f in picked] == [
        ("2025-09-27", 416161000000, "FY2025"),
        # The restated value, but named by its own 10-K, not the later one's FY2025.
        ("2024-09-28", 391000000000, "FY2024"),
    ]


def test_quarterly_skips_year_to_date_figures():
    picked = financials.select_periods(REVENUE, "quarterly", 4)
    assert [(f["val"], f["fiscal"]) for f in picked] == [(111184000000, "Q2 FY2026"), (143756000000, "Q1 FY2026")]


def test_no_fiscal_label_when_first_reported_in_an_annual_report():
    # NVIDIA's older quarters first appear in a 10-K, tagged fp=FY: no quarter name.
    quarter = [fact("2019-01-28", "2019-04-28", 1, fy=2020, fp="FY")]
    assert financials.select_periods(quarter, "quarterly", 1)[0]["fiscal"] is None


def test_balance_sheet_dates_annual_only_from_10k():
    assets = [fact(None, "2025-09-27", 1), fact(None, "2025-12-27", 2, form="10-Q")]
    assert [f["val"] for f in financials.select_periods(assets, "annual", 4)] == [1]
    assert [f["val"] for f in financials.select_periods(assets, "quarterly", 4)] == [2, 1]


def test_tags_in_priority_order():
    us_gaap = {
        "Revenues": {"units": {"USD": [fact("2017-10-01", "2018-09-29", 265595000000)]}},
        "RevenueFromContractWithCustomerExcludingAssessedTax": {"units": {"USD": REVENUE}},
    }
    tags = financials._tags_for(us_gaap, "revenue")
    assert [(t, u) for t, u, _ in tags] == [
        ("RevenueFromContractWithCustomerExcludingAssessedTax", "USD"), ("Revenues", "USD"),
    ]


@pytest.fixture
def apple(monkeypatch):
    monkeypatch.setattr(financials, "lookup_cik", lambda s: {"cik": CIK, "name": "Apple Inc."} if s == "AAPL" else None)
    facts = {"facts": {"us-gaap": {
        "RevenueFromContractWithCustomerExcludingAssessedTax": {"units": {"USD": REVENUE}},
        "EarningsPerShareDiluted": {"units": {"USD/shares": [fact("2024-09-29", "2025-09-27", 7.46)]}},
        "NetIncomeLoss": {"units": {"USD": [fact("2024-09-29", "2025-09-27", -1234000000)]}},
    }}}
    monkeypatch.setattr(financials, "company_facts", lambda cik: facts)


def test_tool_shows_exact_value_period_form_and_filing_link(apple):
    out = get_financials.invoke({"ticker": "aapl", "metrics": ["revenue", "eps_diluted", "net_income", "cash"]})
    lines = out.splitlines()
    assert lines[0] == "Apple Inc. (AAPL) annual figures as reported to the SEC (XBRL), newest first:"
    assert lines[2] == ("- FY2025 (ended 2025-09-27): $416,161,000,000 ($416.16B), 10-K filed 2025-10-31 "
                        "<https://www.sec.gov/Archives/edgar/data/320193/000032019325000079/>")
    # EPS facts carry no fiscal tags here; the label comes from revenue's fact for the same period.
    assert "- FY2025 (ended 2025-09-27): $7.46 per share, 10-K filed 2025-10-31" in out
    assert "-$1,234,000,000 (-$1.23B)" in out
    assert lines[-1] == "Not reported under a standard tag: cash"


def test_restated_value_shows_what_was_first_reported(apple):
    out = get_financials.invoke({"ticker": "AAPL", "metrics": ["revenue"]})
    assert ("- FY2024 (ended 2024-09-28): $391,000,000,000 ($391.00B), 10-K filed 2025-10-31 "
            "<https://www.sec.gov/Archives/edgar/data/320193/000032019325000079/>; restated: originally "
            "$391,035,000,000 ($391.04B) in the 10-K filed 2024-11-01 "
            "<https://www.sec.gov/Archives/edgar/data/320193/000032019324000123/>") in out


def test_fiscal_year_limits_history(apple):
    out = get_financials.invoke({"ticker": "AAPL", "metrics": ["revenue"], "fiscal_year": 2024})
    assert out.splitlines()[0] == "Apple Inc. (AAPL) annual figures through FY2024 as reported to the SEC (XBRL), newest first:"
    assert "FY2025" not in out and "FY2024 (ended 2024-09-28)" in out


def test_label_more_than_a_year_off_is_dropped():
    # A pre-XBRL year first appears as a comparison in a filing two years later.
    old = [fact("2008-09-28", "2009-09-26", 1, fy=2011, fp="FY")]
    assert financials.select_periods(old, "annual", 1)[0]["fiscal"] is None


def test_labels_come_from_any_tag_for_the_period():
    new_tag = [fact("2015-07-01", "2016-06-30", 5, filed="2018-08-03", fy=2018, fp="FY")]
    other = {"Revenues": {"units": {"USD": [fact("2015-07-01", "2016-06-30", 9, filed="2016-07-28", fy=2016, fp="FY")]}}}
    labels = financials.first_reports(other)
    assert financials.select_periods(new_tag, "annual", 1)[0]["fiscal"] is None
    assert financials.select_periods(new_tag, "annual", 1, labels=labels)[0]["fiscal"] == "FY2016"


def test_tool_quarterly_explains_missing_q4(apple):
    out = get_financials.invoke({"ticker": "AAPL", "metrics": ["revenue"], "period": "quarterly"})
    assert "- Q2 FY2026 (2025-12-28 to 2026-03-28): $111,184,000,000" in out
    assert out.endswith("Fiscal Q4 has no 10-Q: it is the annual figure minus the first three quarters.")


def test_tool_non_filer_and_bad_ticker(apple):
    assert get_financials.invoke({"ticker": "SPY"}).startswith("No SEC financial data for SPY")
    assert "not a valid ticker" in get_financials.invoke({"ticker": "DROP TABLE"})


def test_no_xbrl_facts_is_empty_not_an_error(monkeypatch):
    financials._cache.clear()

    def not_found(url):
        response = requests.Response()
        response.status_code = 404
        raise requests.HTTPError(response=response)

    monkeypatch.setattr(financials, "_get_json", not_found)
    assert financials.company_facts(884394) == {}


def test_missing_user_agent_refuses_to_call_the_sec(monkeypatch):
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    with pytest.raises(RuntimeError):
        financials._get_json("https://data.sec.gov/x")


def test_more_complete_tag_wins_for_a_period_even_if_a_partial_one_is_newer(monkeypatch):
    # AMD: DepreciationDepletionAndAmortization $167M vs Depreciation $94M for FY2015.
    da = [fact("2014-12-28", "2015-12-26", 167000000, filed="2016-02-18", fy=2015, fp="FY")]
    dep = [fact("2014-12-28", "2015-12-26", 94000000, filed="2016-02-18", fy=2015, fp="FY"),
           fact("2025-12-28", "2026-12-26", 1, filed="2027-02-01", fy=2026, fp="FY")]
    monkeypatch.setattr(financials, "lookup_cik", lambda s: {"cik": 2488, "name": "AMD"})
    monkeypatch.setattr(financials, "company_facts", lambda cik: {"facts": {"us-gaap": {
        "DepreciationDepletionAndAmortization": {"units": {"USD": da}},
        "Depreciation": {"units": {"USD": dep}},
    }}})
    values = financials.get_financials("AMD", ["depreciation_amortization"], through_fy=2015)["metrics"][
        "depreciation_amortization"]["values"]
    assert [(v["value"], v["tag"]) for v in values] == [(167000000, "DepreciationDepletionAndAmortization")]


def test_a_value_tagged_years_later_does_not_override_the_statements():
    # General Mills: FY2022 net income $2,707.3M in the 10-Ks that show FY2022;
    # a 2026 filing tagged it with the figure including noncontrolling interests.
    facts = [fact("2021-05-31", "2022-05-29", 2707300000, filed="2022-06-30", fy=2022, fp="FY"),
             fact("2021-05-31", "2022-05-29", 2707300000, filed="2024-06-26", fy=2024, fp="FY"),
             fact("2021-05-31", "2022-05-29", 2735000000, filed="2026-08-03", fy=2026, fp="FY")]
    [picked] = financials.select_periods(facts, "annual", 1)
    assert picked["val"] == 2707300000 and picked["original"] is None


@pytest.mark.parametrize("revenues, contract, want", [
    (194579000000, 193919000000, 194579000000),   # CVS: Revenues is the total
    (2040000000, 16865000000, 16865000000),       # General Mills: Revenues is a sub-figure
])
def test_total_revenue_is_the_largest_revenue_tag(monkeypatch, revenues, contract, want):
    monkeypatch.setattr(financials, "lookup_cik", lambda s: {"cik": 1, "name": "Co"})
    monkeypatch.setattr(financials, "company_facts", lambda cik: {"facts": {"us-gaap": {
        "RevenueFromContractWithCustomerExcludingAssessedTax": {"units": {"USD": [
            fact("2018-01-01", "2018-12-31", contract, filed="2019-02-28", fy=2018, fp="FY")]}},
        "Revenues": {"units": {"USD": [fact("2018-01-01", "2018-12-31", revenues, filed="2019-02-28", fy=2018, fp="FY")]}},
    }}})
    [v] = financials.get_financials("CO", ["revenue"], count=1)["metrics"]["revenue"]["values"]
    assert v["value"] == want


def test_net_income_keeps_priority_order_even_when_profitloss_is_larger(monkeypatch):
    monkeypatch.setattr(financials, "lookup_cik", lambda s: {"cik": 1, "name": "Co"})
    monkeypatch.setattr(financials, "company_facts", lambda cik: {"facts": {"us-gaap": {
        "NetIncomeLoss": {"units": {"USD": [fact("2021-05-31", "2022-05-29", 2707300000, filed="2022-06-30", fy=2022, fp="FY")]}},
        "ProfitLoss": {"units": {"USD": [fact("2021-05-31", "2022-05-29", 2735000000, filed="2022-06-30", fy=2022, fp="FY")]}},
    }}})
    [v] = financials.get_financials("CO", ["net_income"], count=1)["metrics"]["net_income"]["values"]
    assert v["value"] == 2707300000
