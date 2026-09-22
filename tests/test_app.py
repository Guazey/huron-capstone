"""Unit tests for the AgentCore entrypoint. The graph is faked: no model, no AWS, no network."""
import asyncio
import os
from types import SimpleNamespace

# app -> graph -> model builds a Bedrock client at import; see test_nodes.py.
os.environ.setdefault("AWS_REGION", "us-west-2")
os.environ.setdefault("BEDROCK_MODEL_ID", "placeholder-model-id")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")

import pytest  # noqa: E402
from langchain_core.messages import AIMessageChunk  # noqa: E402

import app  # noqa: E402

CONTEXT = SimpleNamespace(session_id="test-session-00000000000000000000001", request_headers=None)


def run(payload):
    async def collect():
        return [e async for e in app.invoke(payload, CONTEXT)]
    return asyncio.run(collect())


seen_configs = []


def fake_graph(monkeypatch, chunks):
    """Replace the graph with one that streams the given (chunk, node) pairs."""
    async def astream(inputs, config, stream_mode):
        seen_configs.append(config)
        for chunk, node in chunks:
            if node == "update":
                yield "updates", chunk
            else:
                yield "messages", (chunk, {"langgraph_node": node})
    monkeypatch.setattr(app, "graph", SimpleNamespace(astream=astream))


@pytest.mark.parametrize("payload", [{}, {"prompt": ""}, {"prompt": "   "}, {"prompt": 42}, ["hi"]])
def test_validate_rejects_bad_payloads(payload):
    with pytest.raises(app.BadRequest):
        app.validate(payload)


def test_validate_rejects_long_prompt():
    with pytest.raises(app.BadRequest, match="too long"):
        app.validate({"prompt": "x" * (app.MAX_PROMPT_CHARS + 1)})


def test_validate_strips():
    assert app.validate({"prompt": "  AAPL?  "}) == "AAPL?"


def test_events_from_block_content():
    chunk = AIMessageChunk(content=[{"type": "text", "text": "AAPL is", "index": 0}])
    assert app.events_from_chunk(chunk) == [{"type": "text", "text": "AAPL is"}]


def test_events_from_tool_call_start_only():
    start = AIMessageChunk(content=[], tool_call_chunks=[
        {"name": "get_stock_price", "args": None, "id": "t1", "index": 0}])
    args = AIMessageChunk(content=[], tool_call_chunks=[
        {"name": None, "args": '{"ticker": "AA', "id": None, "index": 0}])
    assert app.events_from_chunk(start) == [{"type": "tool", "name": "get_stock_price"}]
    assert app.events_from_chunk(args) == []


def test_events_skip_empty_and_non_text():
    assert app.events_from_chunk(AIMessageChunk(content="")) == []
    assert app.events_from_chunk(AIMessageChunk(content=[{"type": "tool_use", "input": "{"}])) == []
    assert app.events_from_chunk(AIMessageChunk(content="hi")) == [{"type": "text", "text": "hi"}]


def test_stream_emits_tool_text_then_done(monkeypatch):
    fake_graph(monkeypatch, [
        (AIMessageChunk(content=[], tool_call_chunks=[
            {"name": "get_stock_price", "args": None, "id": "t1", "index": 0}]), "agent"),
        (AIMessageChunk(content="ignored: not from the agent node"), "tools"),
        (AIMessageChunk(content=[{"type": "text", "text": "AAPL is $1.00", "index": 0}]), "agent"),
    ])
    events = run({"prompt": "AAPL?"})
    assert [e["type"] for e in events] == ["tool", "text", "done"]
    assert events[1]["text"] == "AAPL is $1.00"
    assert events[-1]["request_id"]


def test_bad_request_is_one_error_event():
    events = run({"question": "wrong key"})
    assert len(events) == 1
    assert events[0]["type"] == "error"
    assert "prompt" in events[0]["message"]


def test_internal_failure_does_not_leak(monkeypatch):
    async def astream(inputs, config, stream_mode):
        raise RuntimeError("arn:aws:bedrock:secret-internal-detail")
        yield  # makes this an async generator

    monkeypatch.setattr(app, "graph", SimpleNamespace(astream=astream))
    events = run({"prompt": "AAPL?"})
    assert events[-1]["type"] == "error"
    assert "arn:aws" not in events[-1]["message"]


def test_log_line_has_usage_and_no_prompt_text(monkeypatch, caplog):
    chunk = AIMessageChunk(content="ok", usage_metadata={
        "input_tokens": 10, "output_tokens": 3, "total_tokens": 13})
    fake_graph(monkeypatch, [(chunk, "agent")])
    with caplog.at_level("INFO", logger="capstone"):
        run({"prompt": "my account number is 12345, price of AAPL?"})
    line = next(r.getMessage() for r in caplog.records if '"event": "invocation"' in r.getMessage())
    assert '"input_tokens": 10' in line and '"output_tokens": 3' in line
    assert "12345" not in line
    assert CONTEXT.session_id in line


def bearer(claims: dict) -> str:
    import base64
    import json

    body = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"Bearer header.{body}.signature"


def test_actor_is_the_signed_in_user():
    assert app.actor_id({"Authorization": bearer({"sub": "user-123"})}) == "user-123"


def test_actor_without_token_is_local():
    assert app.actor_id(None) == "local"
    assert app.actor_id({}) == "local"


def test_actor_with_malformed_token_is_unknown():
    assert app.actor_id({"Authorization": "Bearer not-a-jwt"}) == "unknown"
    assert app.actor_id({"Authorization": bearer({"no_sub": 1})}) == "unknown"


def test_conversation_is_keyed_by_session_and_user(monkeypatch):
    fake_graph(monkeypatch, [(AIMessageChunk(content="ok"), "agent")])
    seen_configs.clear()
    context = SimpleNamespace(
        session_id="sess-000000000000000000000000000001",
        request_headers={"Authorization": bearer({"sub": "user-9"})},
    )

    async def collect():
        return [e async for e in app.invoke({"prompt": "and MSFT?"}, context)]

    asyncio.run(collect())
    assert seen_configs[-1] == {"configurable": {
        "thread_id": "sess-000000000000000000000000000001", "actor_id": "user-9"}}


def test_follow_up_sees_earlier_turns():
    """The real graph + in-process memory: turn two receives turn one's messages."""
    from langchain_core.messages import AIMessage
    from langgraph.checkpoint.memory import InMemorySaver

    import nodes
    from graph import with_memory

    seen = []

    class FakeChain:
        def invoke(self, inputs):
            seen.append([m.content for m in inputs["messages"]])
            return AIMessage(content=f"answer {len(seen)}")

    original = nodes.agent_chain
    nodes.agent_chain = FakeChain()
    try:
        g = with_memory(InMemorySaver())
        cfg = {"configurable": {"thread_id": "t1", "actor_id": "u1"}}
        g.invoke({"messages": [("user", "How has TSLA moved?")]}, cfg)
        g.invoke({"messages": [("user", "why?")]}, cfg)
        other = {"configurable": {"thread_id": "t2", "actor_id": "u1"}}
        g.invoke({"messages": [("user", "fresh chat")]}, other)
    finally:
        nodes.agent_chain = original
    assert seen[1] == ["How has TSLA moved?", "answer 1", "why?"]
    assert seen[2] == ["fresh chat"]


def test_tool_urls_are_streamed_once_as_sources(monkeypatch):
    update = {"tools": {"messages": [
        {"role": "tool", "content": "TSLA: $1\nSource: <https://finance.yahoo.com/quote/TSLA/>"},
        {"role": "tool", "content": '- "Headline" (AP) <https://apnews.com/a> and <http://insecure.example>'},
    ]}}
    repeat = {"tools": {"messages": [{"role": "tool", "content": "<https://apnews.com/a>"}]}}
    fake_graph(monkeypatch, [
        (update, "update"),
        (repeat, "update"),
        ({"agent": {"messages": []}}, "update"),
        (AIMessageChunk(content="answer"), "agent"),
    ])
    events = run({"prompt": "TSLA news?"})
    assert [e for e in events if e["type"] == "sources"] == [
        {"type": "sources", "urls": ["https://finance.yahoo.com/quote/TSLA/", "https://apnews.com/a"]},
    ]
