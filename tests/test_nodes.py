"""Unit tests for tools_node. Feeds it hand-built tool calls, never a model."""
import os

# nodes -> model builds a Bedrock client at import, and ChatBedrockConverse
# checks that *some* AWS credentials resolve at construction time. Give it
# config and obviously fake credentials so the import works in CI with no
# .env and no ~/.aws. Nothing here ever calls AWS.
os.environ.setdefault("AWS_REGION", "us-west-2")
os.environ.setdefault("BEDROCK_MODEL_ID", "placeholder-model-id")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")

from langchain_core.messages import AIMessage  # noqa: E402

from nodes import tools_node  # noqa: E402


def ai_with_calls(*calls):
    return AIMessage(content="", tool_calls=[
        {"name": name, "args": args, "id": f"call-{i}", "type": "tool_call"}
        for i, (name, args) in enumerate(calls)
    ])


def test_known_tool_returns_result(monkeypatch):
    import market_data

    monkeypatch.setattr(market_data, "get_quote", lambda s: {
        "symbol": s, "price": 248.5, "previous_close": None, "as_of": "2026-09-22",
    })
    state = {"messages": [ai_with_calls(("get_stock_price", {"ticker": "TSLA"}))]}
    out = tools_node(state)["messages"]
    assert len(out) == 1
    assert out[0]["content"].splitlines()[0] == "TSLA: $248.50 as of 2026-09-22"
    assert out[0]["tool_call_id"] == "call-0"
    assert "status" not in out[0]


def test_unknown_tool_returns_error_result_not_nothing():
    state = {"messages": [ai_with_calls(("delete_everything", {}))]}
    out = tools_node(state)["messages"]
    assert len(out) == 1
    assert out[0]["status"] == "error"
    assert "Unknown tool" in out[0]["content"]
    assert out[0]["tool_call_id"] == "call-0"


def test_raising_tool_returns_error_result(monkeypatch):
    import nodes

    class Boom:
        def invoke(self, args):
            raise RuntimeError("upstream down")

    monkeypatch.setitem(nodes.TOOLS_BY_NAME, "get_stock_price", Boom())
    state = {"messages": [ai_with_calls(("get_stock_price", {"ticker": "AAPL"}))]}
    out = tools_node(state)["messages"]
    assert out[0]["status"] == "error"
    # Exception text can carry ARNs/account IDs: logged, never shown to the model.
    assert "upstream down" not in out[0]["content"]
    assert "unavailable" in out[0]["content"]


def test_every_call_gets_exactly_one_result(monkeypatch):
    import market_data

    monkeypatch.setattr(market_data, "get_quote", lambda s: None)
    state = {"messages": [ai_with_calls(
        ("get_stock_price", {"ticker": "AAPL"}),
        ("nope", {}),
        ("get_stock_price", {"ticker": "NVDA"}),
    )]}
    out = tools_node(state)["messages"]
    assert [r["tool_call_id"] for r in out] == ["call-0", "call-1", "call-2"]


def test_history_is_trimmed_at_a_user_turn():
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

    from nodes import MAX_HISTORY_MESSAGES, recent_history

    msgs = []
    for i in range(20):
        msgs += [
            HumanMessage(f"q{i}"),
            AIMessage(content="", tool_calls=[{"name": "get_stock_price", "args": {}, "id": f"c{i}"}]),
            ToolMessage(content="$1", tool_call_id=f"c{i}"),
            AIMessage(content=f"a{i}"),
        ]
    kept = recent_history(msgs)
    assert len(kept) <= MAX_HISTORY_MESSAGES
    assert isinstance(kept[0], HumanMessage)
    assert kept[-1].content == "a19"
