from langchain_core.tools import tool


@tool
def get_stock_price(ticker: str) -> str:
    """Look up the current price for a stock ticker."""
    fake_prices = {"AAPL": "$227.14", "NVDA": "$121.79", "TSLA": "$248.50"}
    return fake_prices.get(ticker.upper(), f"No price found for {ticker}")


if __name__ == "__main__":
    # Standalone test: no model involved at all.
    print(get_stock_price.invoke({"ticker": "AAPL"}))
    print(get_stock_price.invoke({"ticker": "nvda"}))
    print(get_stock_price.invoke({"ticker": "MSFT"}))
    print("---schema the model will see---")
    print("name:", get_stock_price.name)
    print("description:", get_stock_price.description)
    print("args:", get_stock_price.args)
