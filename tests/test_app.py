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

CONTEXT = SimpleNamespace(session_id="test-session-00000000000000000000001")


def run(payload):
    async def collect():
        return [e async for e in app.invoke(payload, CONTEXT)]
    return asyncio.run(collect())


def fake_graph(monkeypatch, chunks):
    """Replace the graph with one that streams the given (chunk, node) pairs."""
    async def astream(inputs, stream_mode):
        for chunk, node in chunks:
            yield chunk, {"langgraph_node": node}
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
    async def astream(inputs, stream_mode):
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
