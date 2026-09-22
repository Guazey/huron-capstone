"""Lightweight eval: rerun after every change to the prompt, model, or graph.

Usage:
    python eval.py

Each case can check three things:
  expect       substring that must appear in the final answer (case-insensitive)
  expect_tool  substring that must appear in some tool result along the way
  forbid       regex that must NOT appear in the final answer
Checking the failure path via the tool result plus a forbid pattern is
deliberate: the model paraphrases "not found" differently every run, so a
wording check there is flaky. What matters is that the tool reported no
price and the model didn't invent one.
"""
import re

from graph import app

test_cases = [
    {"question": "What's the price of AAPL?", "expect": "227.14"},
    {"question": "What's NVDA trading at?", "expect": "121.79"},
    {
        "question": "What's the price of a made-up ticker XYZ?",
        "expect_tool": "No price found",
        "forbid": r"\$\d",  # no dollar amount may appear in the answer
    },
]


def check(case, messages):
    """Return (passed, reason)."""
    final_answer = messages[-1].content
    tool_outputs = " ".join(m.content for m in messages if m.type == "tool")

    if "expect" in case and case["expect"].lower() not in final_answer.lower():
        return False, f"final answer missing {case['expect']!r}"
    if "expect_tool" in case and case["expect_tool"].lower() not in tool_outputs.lower():
        return False, f"no tool result containing {case['expect_tool']!r}"
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
