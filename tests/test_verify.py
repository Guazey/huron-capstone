"""Unit tests for the figure check (verify.py) and the verify node's rewrite loop. No model."""
import os

# nodes -> model builds a Bedrock client at import; see test_nodes.py.
os.environ.setdefault("AWS_REGION", "us-west-2")
os.environ.setdefault("BEDROCK_MODEL_ID", "placeholder-model-id")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage  # noqa: E402

import nodes  # noqa: E402
import verify  # noqa: E402

TOOL = ("Revenue FY ended 2025-09-27: $416,161,000,000 ($416.16B), 10-K filed 2025-10-31\n"
        "AAPL: $254.63 as of 2026-10-02 (previous close $250.10, +1.81%)\n"
        "(416161000000 - 391035000000) / 391035000000 * 100 = 6.4255")


def test_claims_are_money_percents_and_scaled_numbers_only():
    answer = "In FY2025 (10-K, Q4, 3 months) revenue hit $416.2 billion, up 6.43%, P/E 31.2, 14.6B shares"
    assert [c[0] for c in verify.claims(answer)] == ["$416.2 billion", "6.43%"]


def test_percentage_points_are_checked():
    answer = "a 14.8-percentage-point gap, or 14.8 percentage points, vs 3 points"
    assert [c[0] for c in verify.claims(answer)] == ["14.8-percentage-point", "14.8 percentage points"]


def test_honest_rounding_and_rescaling_pass():
    for answer in ("$416.2B", "$416 billion", "$416,161 million", "$416.16B",
                   "$254.63", "$255", "up 1.81%", "up 1.8%", "grew 6.4%", "grew 6.43%"):
        assert verify.unverified(answer, [TOOL]) == [], answer


def test_wrong_figures_are_caught():
    assert verify.unverified("revenue $417B, up 6.5%, price $254.36", [TOOL]) == ["$417B", "6.5%", "$254.36"]


def test_sign_words_dont_matter_and_duplicates_listed_once():
    assert verify.unverified("down -1.81%; it rose 9.9% then 9.9%", [TOOL]) == ["9.9%"]


def test_user_typed_numbers_count_as_sources():
    assert verify.unverified("If it falls to $200, that's 21.5% lower", ["what if it falls to $200", TOOL]) == ["21.5%"]


# ------------------------------------------------------------------ the node

def turn(answer, *, tool=TOOL, extra=()):
    return [
        HumanMessage("How did Apple's revenue do?", id="u1"),
        AIMessage("", id="a1", tool_calls=[{"name": "get_financials", "args": {}, "id": "c1"}]),
        ToolMessage(tool, tool_call_id="c1", id="t1"),
        *extra,
        AIMessage(answer, id="a2"),
    ]


def test_grounded_answer_passes_with_a_count():
    out = nodes.verify_node({"messages": turn("Revenue was $416.2B, up 6.4%.")})
    assert out["messages"] == []
    assert out["number_check"] == {"figures": 2, "unverified": [], "rewrites": 0}
    assert nodes.after_verify({"messages": turn("x")}) == "end"


def test_invented_figure_asks_for_one_rewrite():
    out = nodes.verify_node({"messages": turn("Revenue was $420B.")})
    [note] = out["messages"]
    assert note.type == "human" and note.name == nodes.VERIFIER
    assert "$420B" in note.content
    assert "number_check" not in out
    assert nodes.after_verify({"messages": [note]}) == "agent"


def test_after_the_rewrite_the_draft_and_note_are_removed():
    note = HumanMessage("Automatic check: ...", name=nodes.VERIFIER, id="n1")
    draft = AIMessage("Revenue was $420B.", id="a2")
    messages = turn("Revenue was $416.2B.", extra=[draft, note])
    messages[-1].id = "a3"
    out = nodes.verify_node({"messages": messages})
    assert sorted(m.id for m in out["messages"]) == ["a2", "n1"]
    assert out["number_check"] == {"figures": 1, "unverified": [], "rewrites": 1}


def test_still_wrong_after_rewrite_is_flagged_not_looped():
    note = HumanMessage("Automatic check: ...", name=nodes.VERIFIER, id="n1")
    messages = turn("Revenue was $421B.", extra=[AIMessage("Revenue was $420B.", id="a2"), note])
    messages[-1].id = "a3"
    out = nodes.verify_node({"messages": messages})
    assert out["number_check"]["unverified"] == ["$421B"]
    assert all(m.type == "remove" for m in out["messages"])


def test_note_from_an_earlier_turn_does_not_use_up_this_turns_rewrite():
    earlier = [HumanMessage("old", id="u0"), AIMessage("old answer", id="a0"),
               HumanMessage("note", name=nodes.VERIFIER, id="n0")]
    out = nodes.verify_node({"messages": earlier + turn("Revenue was $420B.")})
    assert out["messages"][0].name == nodes.VERIFIER


def test_graph_rewrites_then_keeps_only_the_corrected_answer():
    """The real graph with a fake model: a wrong draft, the check, the corrected answer."""
    from graph import graph as builder

    replies = iter([
        AIMessage("", tool_calls=[{"name": "calculate", "args": {"expression": "1 + 1"}, "id": "c1"}]),
        AIMessage("It's $3."),
        AIMessage("It's $2."),
    ])
    seen = []

    class FakeChain:
        def invoke(self, inputs):
            seen.append([m.type for m in inputs["messages"]])
            return next(replies)

    original = nodes.agent_chain
    nodes.agent_chain = FakeChain()
    try:
        result = builder.compile().invoke({"messages": [("user", "What is 1 + 1 dollars?")]})
    finally:
        nodes.agent_chain = original
    assert [m.type for m in result["messages"]] == ["human", "ai", "tool", "ai"]
    assert result["messages"][-1].content == "It's $2."
    assert result["number_check"] == {"figures": 1, "unverified": [], "rewrites": 1}
    # The rewrite saw the draft and the note.
    assert seen[-1][-2:] == ["ai", "human"]
