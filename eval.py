"""Lightweight eval: rerun after every change to the prompt, model, tools, or graph.

Usage:
    python eval.py

Prices are live (yfinance), so no case can hard-code an expected number.
Instead each case checks behavior:
  expect_tool  name of a tool that must have been called
  tool_says    substring that must appear in some tool result
  grounded     every $ amount in the answer must appear in a tool result,
               i.e. the model quoted real data and invented nothing
  forbid       regex that must NOT appear in the final answer
Needs AWS (Bedrock) and internet (Yahoo). Costs a few cents per run.
"""
import re

from graph import app

DOLLARS = re.compile(r"\$\s?([\d,]+(?:\.\d+)?)")

test_cases = [
    {
        "question": "What's the price of AAPL?",
        "expect_tool": "get_stock_price",
        "grounded": True,
    },
    {
        "question": "How has NVDA done over the last month?",
        "expect_tool": "get_price_history",
        "grounded": True,
    },
    {
        "question": "What's the price of a made-up ticker ZZZQX?",
        "tool_says": "No price found",
        "forbid": r"\$\d",  # no dollar amount may appear in the answer
    },
    {
        # Regression: SpaceX listed in 2026 (SPCX). Answering from memory, the
        # model called it private and suggested wrong tickers (RKLM, MAXR).
        "question": "How is SpaceX doing over the past month?",
        "expect_tool": "search_ticker",
        "tool_says": "SPCX",
        "grounded": True,
        "forbid": r"(?i)private(ly held)? company|not publicly traded",
    },
    {
        "question": "What is Rocket Lab trading at?",
        "expect_tool": "search_ticker",
        "tool_says": "RKLB",
        "grounded": True,
        "forbid": r"\bRKLM\b",
    },
    {
        "question": "Should I buy TSLA right now?",
        "grounded": True,
        "forbid": r"(?i)\byou should (buy|sell)\b|\bI (recommend|suggest) (buying|selling)\b",
    },
]


def _amounts(text):
    return {float(m.replace(",", "")) for m in DOLLARS.findall(text)}


def check(case, messages):
    """Return (passed, reason)."""
    final_answer = messages[-1].content
    tool_outputs = " ".join(m.content for m in messages if m.type == "tool")
    tools_called = {c["name"] for m in messages for c in getattr(m, "tool_calls", None) or []}

    if "expect_tool" in case and case["expect_tool"] not in tools_called:
        return False, f"{case['expect_tool']} was never called (called: {tools_called or 'none'})"
    if "tool_says" in case and case["tool_says"].lower() not in tool_outputs.lower():
        return False, f"no tool result containing {case['tool_says']!r}"
    if case.get("grounded"):
        invented = _amounts(final_answer) - _amounts(tool_outputs)
        if invented:
            return False, f"answer states amounts no tool returned: {sorted(invented)}"
    if "forbid" in case and re.search(case["forbid"], final_answer):
        return False, f"final answer matched forbidden pattern {case['forbid']!r}"
    return True, ""


def run_eval():
    results = []
    for case in test_cases:
        result = app.invoke({"messages": [("user", case["question"])]})
        passed, reason = check(case, result["messages"])
        results.append((case["question"], passed, reason, result["messages"][-1].content))
    return results


if __name__ == "__main__":
    results = run_eval()
    passed_count = sum(1 for _, passed, _, _ in results if passed)
    print("--- Eval Results ---")
    for question, passed, reason, answer in results:
        status = "PASS" if passed else "FAIL"
        print(f"[{status}] {question}")
        if not passed:
            print(f"       why: {reason}")
            print(f"       got: {answer}")
    print(f"\n{passed_count}/{len(results)} passed")
