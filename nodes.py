import logging

from langchain_core.messages import trim_messages

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


def agent_node(state: AgentState):
    """Call the model with the system prompt + recent conversation; add its reply to state."""
    response = agent_chain.invoke({"messages": recent_history(state["messages"])})
    return {"messages": [response]}


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


if __name__ == "__main__":
    from langchain_core.messages import HumanMessage
    from langgraph.graph.message import add_messages

    # Drive the two nodes by hand, merging each output into state with the
    # same reducer the graph will use. This is one full loop, done manually.
    state = {"messages": [HumanMessage("What's NVDA trading at?")]}

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
