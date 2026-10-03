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
    assert results.usage(msgs) == {"input_tokens": 30, "output_tokens": 7, "tools": ["calculate"]}


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
