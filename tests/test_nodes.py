"""Unit tests for tools_node. Feeds it hand-built tool calls, never a model."""
import os

# nodes -> model builds a Bedrock client at import; give it config so the
# import works in CI with no .env and no AWS credentials. Nothing is called.
os.environ.setdefault("AWS_REGION", "us-west-2")
os.environ.setdefault("BEDROCK_MODEL_ID", "placeholder-model-id")

from langchain_core.messages import AIMessage  # noqa: E402

from nodes import tools_node  # noqa: E402


def ai_with_calls(*calls):
    return AIMessage(content="", tool_calls=[
        {"name": name, "args": args, "id": f"call-{i}", "type": "tool_call"}
        for i, (name, args) in enumerate(calls)
    ])


def test_known_tool_returns_result():
    state = {"messages": [ai_with_calls(("get_stock_price", {"ticker": "TSLA"}))]}
    out = tools_node(state)["messages"]
    assert len(out) == 1
    assert out[0]["content"] == "$248.50"
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

    monkeypatch.setitem(nodes.TOOLS, "get_stock_price", Boom())
    state = {"messages": [ai_with_calls(("get_stock_price", {"ticker": "AAPL"}))]}
    out = tools_node(state)["messages"]
    assert out[0]["status"] == "error"
    assert "upstream down" in out[0]["content"]


def test_every_call_gets_exactly_one_result():
    state = {"messages": [ai_with_calls(
        ("get_stock_price", {"ticker": "AAPL"}),
        ("nope", {}),
        ("get_stock_price", {"ticker": "NVDA"}),
    )]}
    out = tools_node(state)["messages"]
    assert [r["tool_call_id"] for r in out] == ["call-0", "call-1", "call-2"]
