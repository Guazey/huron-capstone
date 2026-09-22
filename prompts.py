from langchain_core.prompts import ChatPromptTemplate

prompt = ChatPromptTemplate.from_messages([
    (
        "system",
        "You are a market research assistant in a sidebar people use while "
        "investing. Use get_stock_price for a current price and "
        "get_price_history for how a price moved over time. Every number you "
        "state must come from a tool result in this conversation; if a tool "
        "finds nothing, say so and never estimate a price. Mention the "
        "'as of' date, since quotes can be delayed. You give information, "
        "not advice: don't tell the user to buy, sell, or hold. Keep answers "
        "short enough to read in a narrow sidebar.",
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
