from typing import Annotated

from langgraph.graph.message import add_messages
from typing_extensions import TypedDict


class AgentState(TypedDict):
    # add_messages is a reducer: when a node returns {"messages": [...]},
    # LangGraph appends to the existing list instead of replacing it.
    messages: Annotated[list, add_messages]


if __name__ == "__main__":
    from langchain_core.messages import AIMessage, HumanMessage

    # Standalone test: call the reducer directly, no graph or model involved.
    existing = [HumanMessage("What is AAPL trading at?")]
    update = [AIMessage("Let me check that for you.")]

    merged = add_messages(existing, update)
    print("---with the add_messages reducer---")
    for m in merged:
        print(f"[{m.type}] {m.content}")

    print("---what a plain dict update would have done---")
    plain = {"messages": existing}
    plain.update({"messages": update})
    for m in plain["messages"]:
        print(f"[{m.type}] {m.content}")

    # The reducer also assigns ids, which is how it can later replace a
    # message in place if a node returns one with a matching id.
    print("---ids assigned by the reducer---")
    print([m.id is not None for m in merged])
