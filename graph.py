from langgraph.graph import END, START, StateGraph

from nodes import agent_node, tools_node
from state import AgentState


def should_continue(state: AgentState):
    """The decision edge: loop back through tools, or stop."""
    last_message = state["messages"][-1]
    if getattr(last_message, "tool_calls", None):
        return "tools"
    return END


graph = StateGraph(AgentState)
graph.add_node("agent", agent_node)
graph.add_node("tools", tools_node)
graph.add_edge(START, "agent")
graph.add_conditional_edges("agent", should_continue, {"tools": "tools", END: END})
graph.add_edge("tools", "agent")

# Stateless: each invoke starts from the messages it's given (CLI, eval).
app = graph.compile()


def with_memory(checkpointer):
    """The same graph, but each thread_id keeps its conversation between calls."""
    return graph.compile(checkpointer=checkpointer)


if __name__ == "__main__":
    from langchain_core.messages import HumanMessage

    print("---graph structure---")
    print(app.get_graph().draw_mermaid())

    def run(question):
        print(f"\n=== {question} ===")
        # stream() yields one dict per node execution, so the path is visible.
        for step in app.stream({"messages": [HumanMessage(question)]}):
            for node_name, update in step.items():
                for m in update["messages"]:
                    calls = getattr(m, "tool_calls", None)
                    content = m["content"] if isinstance(m, dict) else m.content
                    print(f"[{node_name}] tool_calls={calls or []} content={content!r}")

    run("What's TSLA trading at?")
    run("In one sentence, what does a market cap measure?")
    run("Compare the prices of AAPL and NVDA.")
