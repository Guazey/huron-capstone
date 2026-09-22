"""Entry point: run the agent once and print the full message trace.

Usage:
    python main.py                       # asks the default question
    python main.py "Compare AAPL and NVDA"
"""
import sys

from graph import app


def main(question: str) -> None:
    result = app.invoke({"messages": [("user", question)]})
    for m in result["messages"]:
        print(f"{m.type}: {m.content}")


if __name__ == "__main__":
    main(" ".join(sys.argv[1:]) or "What's the price of AAPL?")
