from langchain_core.prompts import ChatPromptTemplate

prompt = ChatPromptTemplate.from_messages([
    (
        "system",
        "You are a financial research assistant. Use the get_stock_price tool "
        "when the user asks about a specific stock's price.",
    ),
    ("placeholder", "{messages}"),
])


if __name__ == "__main__":
    from langchain_core.messages import HumanMessage

    # Standalone test: render the template with no model involved.
    rendered = prompt.invoke({"messages": [HumanMessage("What is AAPL trading at?")]})
    print("---rendered messages---")
    for m in rendered.to_messages():
        print(f"[{m.type}] {m.content}")

    # The placeholder is optional: an empty conversation still renders.
    print("---with no messages---")
    print(prompt.invoke({}).to_messages())

    # Validation: a variable the template doesn't know about is rejected.
    print("---bad input---")
    try:
        ChatPromptTemplate.from_messages([("system", "Hello {name}")]).invoke({})
    except KeyError as e:
        print("KeyError:", e)
