"""AgentCore Runtime entrypoint: serves the graph over HTTP and streams the answer.

Run locally (same contract AgentCore uses in the cloud):
    python app.py                         # listens on 127.0.0.1:8080
    curl -N localhost:8080/invocations \\
      -H 'Content-Type: application/json' \\
      -H 'X-Amzn-Bedrock-AgentCore-Runtime-Session-Id: local-session-0000000000000000001' \\
      -d '{"prompt": "How is NVDA doing today?"}'

Request body:  {"prompt": "<question>"}
Memory: messages sent with the same session ID (and the same signed-in user)
form one conversation, so follow-ups like "why did it drop?" work.
Response: Server-Sent Events, one JSON object per `data:` line:
    {"type": "tool", "name": "get_stock_price"}   the agent started a lookup
    {"type": "text", "text": "AAPL is"}           a piece of the answer
    {"type": "sources", "urls": ["https://..."]}  pages the tools used; the
                                                  only links the panel will open
    {"type": "done", "request_id": "..."}         the answer is complete
    {"type": "error", "message": "...", "request_id": "..."}
"""
import base64
import json
import logging
import os
import re
import time
import uuid

from bedrock_agentcore.runtime import BedrockAgentCoreApp
from bedrock_agentcore.runtime.context import BedrockAgentCoreContext

from langgraph.checkpoint.memory import InMemorySaver

from graph import with_memory

MAX_PROMPT_CHARS = 2000

app = BedrockAgentCoreApp()
log = logging.getLogger("capstone")


def _checkpointer():
    """AgentCore Memory when deployed (MEMORY_ID is set); in-process memory locally."""
    memory_id = os.environ.get("MEMORY_ID")
    if not memory_id:
        return InMemorySaver()
    from langgraph_checkpoint_aws import AgentCoreMemorySaver

    return AgentCoreMemorySaver(memory_id, region_name=os.environ.get("AWS_REGION"))


graph = with_memory(_checkpointer())


def actor_id(headers: dict | None) -> str:
    """Who is asking: the Cognito user ID (sub) from the bearer token.

    AgentCore's JWT authorizer has already verified this token before the
    request reaches us, so reading the claim without re-verifying is safe.
    Keying memory by user means one person can't load another's chat, even
    with a guessed session ID. Local runs have no token and share "local".
    """
    auth = (headers or {}).get("Authorization", "")
    if not auth.startswith("Bearer "):
        return "local"
    try:
        payload = auth.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        return str(claims["sub"])
    except (IndexError, KeyError, ValueError):
        return "unknown"


class BadRequest(ValueError):
    """A problem with the caller's input; the message is safe to show them."""


def validate(payload) -> str:
    """Return the prompt, or raise BadRequest."""
    prompt = payload.get("prompt") if isinstance(payload, dict) else None
    if not isinstance(prompt, str) or not prompt.strip():
        raise BadRequest('Send JSON like {"prompt": "What is AAPL trading at?"}')
    if len(prompt) > MAX_PROMPT_CHARS:
        raise BadRequest(f"Question is too long (max {MAX_PROMPT_CHARS} characters).")
    return prompt.strip()


def events_from_chunk(chunk) -> list[dict]:
    """Turn one streamed model chunk into the events the side panel renders.

    Bedrock chunks carry content either as a plain string or as a list of
    typed blocks. Only text blocks become answer text; a tool_call_chunk with
    a name marks the start of a lookup (its args arrive later in pieces and
    aren't useful to show).
    """
    events = []
    for call in getattr(chunk, "tool_call_chunks", None) or []:
        if call.get("name"):
            events.append({"type": "tool", "name": call["name"]})
    content = chunk.content
    if isinstance(content, str):
        if content:
            events.append({"type": "text", "text": content})
    else:
        for block in content:
            if block.get("type") == "text" and block.get("text"):
                events.append({"type": "text", "text": block["text"]})
    return events


URL_IN_ANGLES = re.compile(r"<(https://[^>\s]+)>")


def urls_from_update(update: dict) -> list[str]:
    """URLs in the tool results of one graph update (tools write them as <https://...>)."""
    found = []
    for message in (update.get("tools") or {}).get("messages", []):
        content = message["content"] if isinstance(message, dict) else message.content
        found += URL_IN_ANGLES.findall(str(content))
    return found


@app.entrypoint
async def invoke(payload, context):
    request_id = BedrockAgentCoreContext.get_request_id() or str(uuid.uuid4())
    started = time.monotonic()
    usage = {"input_tokens": 0, "output_tokens": 0}
    tools_called = []
    outcome = "ok"
    prompt = ""

    # Without a session ID there's no conversation to continue: use a
    # throwaway thread so the question is still answered.
    thread_id = context.session_id or f"no-session-{request_id}"
    config = {"configurable": {
        "thread_id": thread_id,
        "actor_id": actor_id(context.request_headers),
    }}

    try:
        prompt = validate(payload)
        sent_urls: set[str] = set()
        async for mode, data in graph.astream(
            {"messages": [("user", prompt)]}, config, stream_mode=["messages", "updates"]
        ):
            if mode == "updates":
                # Tool results just landed: tell the panel which URLs they
                # contain. The panel only makes these clickable, so a link the
                # model invents can never send the user anywhere.
                urls = [u for u in urls_from_update(data) if u not in sent_urls]
                if urls:
                    sent_urls.update(urls)
                    yield {"type": "sources", "urls": urls}
                continue
            chunk, meta = data
            if meta.get("langgraph_node") != "agent":
                continue
            if chunk.usage_metadata:
                usage["input_tokens"] += chunk.usage_metadata["input_tokens"]
                usage["output_tokens"] += chunk.usage_metadata["output_tokens"]
            for event in events_from_chunk(chunk):
                if event["type"] == "tool":
                    tools_called.append(event["name"])
                yield event
        yield {"type": "done", "request_id": request_id}
    except BadRequest as e:
        outcome = "rejected"
        yield {"type": "error", "message": str(e), "request_id": request_id}
    except Exception:  # noqa: BLE001 - never leak internals to the client
        outcome = "error"
        log.exception("request_id=%s failed", request_id)
        yield {
            "type": "error",
            "message": "Something went wrong answering that. Try again in a moment.",
            "request_id": request_id,
        }
    finally:
        # One structured line per request. The prompt text is never logged,
        # only its length: users type whatever they want into a chat box.
        log.info(json.dumps({
            "event": "invocation",
            "request_id": request_id,
            "session_id": context.session_id,
            "outcome": outcome,
            "model": os.environ.get("BEDROCK_MODEL_ID"),
            "prompt_chars": len(prompt),
            "tools_called": tools_called,
            **usage,
            "latency_ms": round((time.monotonic() - started) * 1000),
        }))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    app.run()
