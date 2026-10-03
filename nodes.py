import logging

from langchain_core.messages import HumanMessage, RemoveMessage, trim_messages

import verify
from model import model_with_tools
from prompts import prompt
from state import AgentState
from tools import TOOLS


log = logging.getLogger("capstone")

# LCEL: the prompt renders the system message plus the conversation, and
# pipes the result straight into the tool-bound model.
agent_chain = prompt | model_with_tools


# A long chat would otherwise resend every earlier turn, tool results
# included, on every call. Keep the most recent messages, cut at a user turn
# so a tool call is never separated from its result.
MAX_HISTORY_MESSAGES = 30


def recent_history(messages):
    return trim_messages(
        messages,
        max_tokens=MAX_HISTORY_MESSAGES,
        token_counter=len,
        strategy="last",
        start_on="human",
        include_system=False,
    )


def without_text(response):
    """A tool-calling reply with its text removed, keeping only the tool calls.

    Text next to a tool call is narration ("let me check") or the start of an
    answer the model means to finish after the tool returns. The panel throws
    it away, and if it stayed in the conversation the model would carry on
    mid-sentence after the result, leaving the user half an answer. With it
    gone, the answer after the last tool call is always complete.
    """
    if not response.tool_calls:
        return response
    content = response.content
    if isinstance(content, str):
        content = ""
    else:
        content = [b for b in content if not (isinstance(b, dict) and b.get("type") == "text")]
    return response.model_copy(update={"content": content})


def agent_node(state: AgentState):
    """Call the model with the system prompt + recent conversation; add its reply to state."""
    response = agent_chain.invoke({"messages": recent_history(state["messages"])})
    return {"messages": [without_text(response)]}


TOOLS_BY_NAME = {t.name: t for t in TOOLS}


def _error_result(call, message):
    """A tool result that reports a failure instead of the tool's output."""
    return {
        "role": "tool",
        "content": message,
        "tool_call_id": call["id"],
        "status": "error",
    }


def tools_node(state: AgentState):
    """Run every tool the last message asked for; add the results to state.

    Every tool_call gets exactly one tool result, even when the tool is
    unknown or raises. Bedrock rejects the next turn if a tool_use block has
    no matching tool_result, so an error is returned as a result the model
    can read and recover from, never dropped.
    """
    last_message = state["messages"][-1]
    results = []
    for call in last_message.tool_calls:
        tool = TOOLS_BY_NAME.get(call["name"])
        if tool is None:
            results.append(_error_result(call, f"Unknown tool: {call['name']}"))
            continue
        try:
            content = tool.invoke(call["args"])
            results.append(
                {"role": "tool", "content": content, "tool_call_id": call["id"]}
            )
        except Exception:  # noqa: BLE001 - any tool failure must become a result
            log.exception("tool %s failed", call["name"])
            results.append(_error_result(
                call, f"Tool {call['name']} failed: the data source is unavailable right now."
            ))
    return {"messages": results}


# The verify node's note to the agent is a user-role message with this name,
# so it can be told apart from what the user actually typed.
VERIFIER = "verifier"
MAX_REWRITES = 1


def text_of(message) -> str:
    """A message's text, whether its content is a string or Bedrock's list of blocks."""
    content = message.content
    if isinstance(content, str):
        return content
    return "".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")


def _is_user(message) -> bool:
    return message.type == "human" and message.name != VERIFIER


def verify_node(state: AgentState):
    """Check the final answer's figures against the tool results before it's final.

    A figure no tool returned sends the answer back to the agent once, with
    the list of figures to fix. Once the answer passes (or the rewrite is
    spent), the rejected draft and the note are removed from the
    conversation, so memory keeps only the answer the user ended up with.
    """
    messages = state["messages"]
    turn_start = max(i for i, m in enumerate(messages) if _is_user(m))
    turn = messages[turn_start:]
    answer = text_of(messages[-1])
    sources = [text_of(m) for m in messages if m.type == "tool" or _is_user(m)]
    missing = verify.unverified(answer, sources)
    notes = [i for i, m in enumerate(turn) if m.type == "human" and m.name == VERIFIER]

    if missing and len(notes) < MAX_REWRITES:
        return {"messages": [HumanMessage(name=VERIFIER, content=(
            "Automatic check: these figures in your answer don't appear in any tool "
            f"result: {', '.join(missing)}. Every figure must come from a tool result; "
            "work out growth rates, margins, and differences with the calculate tool. "
            "Rewrite the full answer with those figures fixed or removed. Don't mention this check."
        ))]}

    rejected = [turn[i - 1] for i in notes] + [turn[i] for i in notes]
    return {
        "messages": [RemoveMessage(id=m.id) for m in rejected],
        "number_check": {
            "figures": len(verify.claims(answer)),
            "unverified": missing,
            "rewrites": len(notes),
        },
    }


def after_verify(state: AgentState):
    """Back to the agent if the verify node asked for a rewrite, otherwise done."""
    last = state["messages"][-1]
    return "agent" if last.type == "human" and last.name == VERIFIER else "end"


if __name__ == "__main__":
    from langgraph.graph.message import add_messages

    # Drive the two nodes by hand, merging each output into state with the
    # same reducer the graph will use. This is one full loop, done manually.
    state: AgentState = {"messages": [HumanMessage("What's NVDA trading at?")]}

    print("---agent_node, first pass---")
    out = agent_node(state)
    print("tool_calls:", out["messages"][0].tool_calls)
    state = {"messages": add_messages(state["messages"], out["messages"])}

    print("---tools_node---")
    out = tools_node(state)
    print("returned:", out["messages"])
    state = {"messages": add_messages(state["messages"], out["messages"])}

    print("---agent_node, second pass---")
    out = agent_node(state)
    print("tool_calls:", out["messages"][0].tool_calls)
    print("content:   ", out["messages"][0].content)
    state = {"messages": add_messages(state["messages"], out["messages"])}

    print("---final state---")
    for m in state["messages"]:
        print(f"[{m.type}] {m.content}")
