from model import model_with_tools
from prompts import prompt
from state import AgentState
from tools import get_stock_price


# LCEL: the prompt renders the system message plus the conversation, and
# pipes the result straight into the tool-bound model.
agent_chain = prompt | model_with_tools


def agent_node(state: AgentState):
    """Call the model with the system prompt + whole conversation; add its reply to state."""
    response = agent_chain.invoke({"messages": state["messages"]})
    return {"messages": [response]}


def tools_node(state: AgentState):
    """Run every tool the last message asked for; add the results to state."""
    last_message = state["messages"][-1]
    results = []
    for call in last_message.tool_calls:
        if call["name"] == "get_stock_price":
            result = get_stock_price.invoke(call["args"])
            results.append(
                {"role": "tool", "content": result, "tool_call_id": call["id"]}
            )
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
