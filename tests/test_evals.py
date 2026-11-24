"""Unit tests for eval scoring: the FinanceBench grader, run summaries, the trend report, and checks."""
import json
import os

# eval -> graph -> model builds a Bedrock client at import; see test_nodes.py.
os.environ.setdefault("AWS_REGION", "us-west-2")
os.environ.setdefault("BEDROCK_MODEL_ID", "placeholder-model-id")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")

import pytest  # noqa: E402
from langchain_core.messages import AIMessage, ToolMessage  # noqa: E402

import eval as behavior  # noqa: E402
from evals import financebench, results  # noqa: E402


@pytest.mark.parametrize("text, parsed", [
    ("$1577.00", (1577.0, 0)), ("24.26", (24.26, 2)), ("0.40", (0.4, 1)),
    ("-0.02", (-0.02, 2)), ("65.4%", (65.4, 1)), ("$11,588.00", (11588.0, 0)), ("no number", None),
])
def test_parse_expected_answers(text, parsed):
    assert financebench.parse_number(text) == parsed


@pytest.mark.parametrize("answer, expected, passed", [
    ("Capex was $1,577 million.\nAnswer: 1577", "$1577.00", True),
    ("Answer: $1.577 billion", "$1577.00", True),        # units forgiven
    ("Answer: 0.389", "$0.40", True),                    # the dataset's 0.4, at its real precision
    ("Answer: 24.26", "24.26", True),
    ("Answer: 24.27", "24.26", False),                   # precision is not forgiven
    ("Answer: -0.02", "-0.02", True),
    ("Answer: 0.02", "-0.02", False),
    ("**Answer:** 16.5%", "16.5%", True),
    ("Answer: 0.165", "16.5%", True),
    ("Answer: 10\nAnswer: 1577", "$1577.00", True),      # the last Answer line counts
    ("The capex was 1577.", "$1577.00", False),
])
def test_grade(answer, expected, passed):
    assert financebench.grade(answer, expected)[0] is passed


def test_cases_skip_delisted_and_use_numeric_questions(monkeypatch):
    rows = [
        {"financebench_id": "a", "company": "3M", "question_type": "metrics-generated",
         "question": "FY2018 capex?", "answer": "$1577.00"},
        {"financebench_id": "b", "company": "Activision Blizzard", "question_type": "metrics-generated",
         "question": "q", "answer": "1.9%"},
        {"financebench_id": "c", "company": "3M", "question_type": "novel-generated", "question": "q", "answer": "1"},
    ]
    monkeypatch.setattr(financebench, "load", lambda: rows)
    [case] = financebench.cases()
    assert case["id"] == "a" and case["expected"] == "$1577.00"
    assert case["question"].endswith("'Answer: <number>' in the units the question asks for.")


def result(category, passed, latency=1000, tokens=(1000, 100), rewrites=0):
    return {"id": f"{category}-{passed}-{latency}", "category": category, "passed": passed, "reason": "" if passed else "x",
            "latency_ms": latency, "input_tokens": tokens[0], "output_tokens": tokens[1], "tools": [],
            "figures_checked": 1, "figures_unverified": 0, "rewrites": rewrites, "answer": ""}


def test_summary_scores_by_category_with_cost():
    s = results.summarize([result("market", True), result("market", False, 3000), result("safety", True, rewrites=1)])
    assert (s["passed"], s["total"], s["pass_rate"]) == (2, 3, 0.6667)
    assert s["by_category"] == {"market": {"passed": 1, "total": 2}, "safety": {"passed": 1, "total": 1}}
    assert s["latency_ms_p50"] == 1000
    assert s["est_cost_usd"] == round(3 * (1000 * 1.0 + 100 * 5.0) / 1e6, 4)
    assert s["rewrites"] == 1


def test_usage_counts_ai_tokens_and_tools():
    msgs = [
        AIMessage("", tool_calls=[{"name": "calculate", "args": {}, "id": "1"}],
                  usage_metadata={"input_tokens": 10, "output_tokens": 2, "total_tokens": 12}),
        ToolMessage("1", tool_call_id="1"),
        AIMessage("done", usage_metadata={"input_tokens": 20, "output_tokens": 5, "total_tokens": 25}),
    ]
    assert results.usage(msgs) == {"input_tokens": 30, "output_tokens": 7, "cache_read_tokens": 0,
                                   "cache_write_tokens": 0, "tools": ["calculate"]}


def test_usage_and_cost_price_cached_tokens():
    msgs = [AIMessage("done", usage_metadata={
        "input_tokens": 5000, "output_tokens": 200, "total_tokens": 5200,
        "input_token_details": {"cache_read": 4000, "cache_creation": 0, "ephemeral_5m_input_tokens": 800}})]
    u = results.usage(msgs)
    assert (u["cache_read_tokens"], u["cache_write_tokens"]) == (4000, 800)
    # 200 uncached + 4000 * 0.1 + 800 * 1.25 = 1600 input-priced tokens.
    assert results.cost_usd(5000, 200, 4000, 800) == (1600 * 1.0 + 200 * 5.0) / 1e6


def test_save_load_and_report(tmp_path):
    results.save("behavior", "haiku", [result("market", True), result("safety", False)], tmp_path)
    [saved] = list(tmp_path.glob("*-behavior.json"))
    run = json.loads(saved.read_text())
    assert run["summary"]["passed"] == 1 and run["model"] == "haiku"
    text = results.report(results.load_history(tmp_path))
    assert "| behavior | 1/2 (50%) |" in text
    assert "FAIL `safety-False-1000` (safety): x" in text
    assert results.report([]).startswith("No eval runs")


def test_bare_url_no_tool_returned_fails_the_case():
    tool = ToolMessage("Source: <https://www.sec.gov/Archives/edgar/data/1318605/x/>", tool_call_id="1")
    good = AIMessage("See [10-K](https://www.sec.gov/Archives/edgar/data/1318605/x/) or "
                     "https://www.sec.gov/Archives/edgar/data/1318605/x/.")
    bad = AIMessage("Per the [10-K](https://www.sec.gov/Archives/edgar/data/1318605/x/), or visit "
                    "https://www.sec.gov/cgi-bin/browse-edgar?CIK=0001018724 for more.")
    assert behavior.check({}, [tool, good]) == (True, "")
    passed, reason = behavior.check({}, [tool, bad])
    assert not passed and "writes out URLs" in reason


def test_a_crashing_case_is_recorded_as_a_failure_not_raised(monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("Bedrock is down")

    monkeypatch.setattr(behavior, "with_memory", broken)
    out = behavior.run_case({"question": "What's the price of AAPL?", "category": "market"})
    assert out["passed"] is False
    assert out["reason"] == "error: RuntimeError: Bedrock is down"
    assert out["category"] == "market" and out["input_tokens"] == 0


# --- Upstream outages: scored apart from failures, never hiding a real bug ---

import requests  # noqa: E402
from botocore.exceptions import ClientError  # noqa: E402
from curl_cffi.requests import exceptions as curl_errors  # noqa: E402
from langchain_core.messages import HumanMessage  # noqa: E402
from yfinance.exceptions import YFDataException, YFRateLimitError  # noqa: E402

import nodes  # noqa: E402

YAHOO_DOWN = "*** YAHOO! FINANCE IS CURRENTLY DOWN! ***"


def http_error(status, url="https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json"):
    response = requests.Response()
    response.status_code, response.url = status, url
    return requests.HTTPError(response=response)


def chained(outer, cause):
    outer.__cause__ = cause
    return outer


@pytest.mark.parametrize("exc, reason", [
    (YFDataException(YAHOO_DOWN), "Yahoo Finance down"),
    (YFRateLimitError(), "Yahoo Finance rate limit"),
    (ClientError({"Error": {"Code": "ThrottlingException"}}, "Converse"), "Bedrock ThrottlingException"),
    (http_error(503), "HTTP 503 from data.sec.gov"),
    (http_error(429), "HTTP 429 from data.sec.gov"),
    (requests.ConnectionError("reset"), "network ConnectionError"),
    (chained(RuntimeError("wrapped"), YFDataException(YAHOO_DOWN)), "Yahoo Finance down"),
    # Our bugs or config, which must still fail the case:
    (ClientError({"Error": {"Code": "AccessDeniedException"}}, "Rerank"), None),
    (YFDataException("Failed to parse json response from Yahoo Finance: {}"), None),
    (http_error(404), None),
    (KeyError("price"), None),
    (None, None),
])
def test_upstream_reason_names_outages_only(exc, reason):
    assert behavior.upstream_reason(exc) == reason


def fake_graph():
    """A graph whose one turn runs the real tools_node on a get_news call (patch the tool to raise)."""
    class Graph:
        def invoke(self, state, config):
            call = AIMessage("", tool_calls=[{"name": "get_news", "args": {}, "id": "1", "type": "tool_call"}])
            [result] = nodes.tools_node({"messages": [call]})["messages"]
            tool = ToolMessage(result["content"], tool_call_id="1", status="error")
            return {"messages": [HumanMessage(state["messages"][0][1]), call, tool,
                                 AIMessage("Headlines are unavailable right now.")]}
    return Graph()


@pytest.mark.parametrize("tool_error, upstream", [
    (YFDataException(YAHOO_DOWN), "Yahoo Finance down"),
    (RuntimeError("bug in get_news"), None),
])
def test_a_case_failed_by_an_outage_is_upstream_not_failed(monkeypatch, tool_error, upstream):
    class Raises:
        def invoke(self, args):
            raise tool_error

    monkeypatch.setitem(nodes.TOOLS_BY_NAME, "get_news", Raises())
    monkeypatch.setattr(behavior, "with_memory", lambda saver: fake_graph())
    monkeypatch.setattr(behavior.time, "sleep", lambda s: None)
    case = {"category": "news", "question": "What is the biggest news today?",
            "expect_tool": "get_news", "tool_says": "Recent headlines"}
    out = behavior.run_case(case)
    assert out["passed"] is False and out["upstream"] == upstream
    # The handler is removed after the case, so outages can't leak into the next one.
    assert not any(isinstance(h, behavior._OutageLog) for h in nodes.log.handlers)


def test_a_throttled_crash_is_upstream(monkeypatch):
    def throttled(*args, **kwargs):
        raise ClientError({"Error": {"Code": "ThrottlingException"}}, "Converse")

    monkeypatch.setattr(behavior, "with_memory", throttled)
    out = behavior.run_case({"question": "FY2020 revenue?", "category": "financebench"})
    assert out["passed"] is False and out["upstream"] == "Bedrock ThrottlingException"


def outage(category):
    return {**result(category, False), "upstream": "Yahoo Finance down"}


def test_upstream_cases_are_left_out_of_the_pass_rate():
    s = results.summarize([result("news", True), result("news", False), outage("news"), outage("page")])
    assert (s["passed"], s["scored"], s["upstream"], s["total"]) == (1, 2, 2, 4)
    assert s["pass_rate"] == 0.5
    assert s["by_category"] == {"news": {"passed": 1, "total": 2, "upstream": 1},
                                "page": {"passed": 0, "total": 0, "upstream": 1}}
    # An outage on a case that still passed doesn't matter: it's a pass.
    assert results.summarize([{**result("news", True), "upstream": "x"}])["upstream"] == 0


def test_bar_fails_low_scores_and_runs_that_outages_left_unmeasured():
    healthy = results.summarize([result("m", True)] * 14 + [outage("m")] * 3 + [result("m", False)])
    assert behavior.below_bar(healthy, 0.8) is None  # 14/15 scored
    low = results.summarize([result("m", True)] * 3 + [result("m", False)] * 2)
    assert "below 80%" in behavior.below_bar(low, 0.8)
    mostly_down = results.summarize([result("m", True)] * 2 + [outage("m")] * 3)
    assert behavior.below_bar(mostly_down, 0.8).startswith("inconclusive")
    assert behavior.below_bar(mostly_down, 0.0) is None  # no bar outside CI


def test_report_shows_upstream_cases_and_reads_old_runs(tmp_path):
    results.save("behavior", "haiku", [result("news", True), outage("news")], tmp_path)
    text = results.report(results.load_history(tmp_path))
    assert "| behavior | 1/1 (100%) +1 upstream |" in text
    assert "UPSTREAM `news-False-1000` (news): x [outage: Yahoo Finance down, not scored]" in text
    old = {"run_at": "2026-11-09T23:14:05+00:00", "commit": "e7fc029", "suite": "behavior",
           "summary": {"passed": 1, "total": 2, "latency_ms_p50": 1000, "est_cost_usd": 0.1,
                       "rewrites": 0, "figures_unverified": 0,
                       "by_category": {"news": {"passed": 1, "total": 2}}},
           "cases": [result("news", True), result("news", False)]}
    assert "| behavior | 1/2 (50%) |" in results.report([old])


def implicit(outer, context):
    outer.__context__ = context
    return outer


@pytest.mark.parametrize("exc, reason", [
    (curl_errors.Timeout("Yahoo timed out"), "network Timeout"),
    (requests.exceptions.SSLError("certificate verify failed"), None),
    (curl_errors.SSLError("certificate verify failed"), None),
    # A bug raised while handling an outage (no `from`) is still a bug.
    (implicit(KeyError("price"), ClientError({"Error": {"Code": "ThrottlingException"}}, "Converse")), None),
])
def test_upstream_reason_covers_yahoo_transport_but_not_bugs_in_handlers(exc, reason):
    assert behavior.upstream_reason(exc) == reason


def test_a_crash_after_a_logged_outage_is_still_our_bug(monkeypatch):
    class YahooDown:
        def invoke(self, args):
            raise YFDataException(YAHOO_DOWN)

    class Graph:
        def invoke(self, state, config):
            call = AIMessage("", tool_calls=[{"name": "get_news", "args": {}, "id": "1", "type": "tool_call"}])
            nodes.tools_node({"messages": [call]})  # the real node logs the outage
            raise KeyError("price")  # then our code crashes

    monkeypatch.setitem(nodes.TOOLS_BY_NAME, "get_news", YahooDown())
    monkeypatch.setattr(behavior, "with_memory", lambda saver: Graph())
    monkeypatch.setattr(behavior.time, "sleep", lambda s: None)
    out = behavior.run_case({"question": "Any news?", "category": "news"})
    assert out["passed"] is False and out["upstream"] is None


def test_only_tools_node_failures_count_as_outages():
    import logging

    outages = behavior._OutageLog()
    down = YFDataException(YAHOO_DOWN)
    for func in ("embed_filings", "tools_node"):  # a recovered fallback, then a failed tool
        record = logging.LogRecord("capstone", logging.ERROR, "x.py", 1, "failed", None,
                                   (type(down), down, None), func=func)
        outages.emit(record)
        assert outages.reasons == (set() if func == "embed_filings" else {"Yahoo Finance down"})
