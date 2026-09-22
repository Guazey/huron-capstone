import os

from dotenv import load_dotenv
from langchain_aws import ChatBedrockConverse

from tools import get_stock_price

load_dotenv()

model = ChatBedrockConverse(
    model=os.environ["BEDROCK_MODEL_ID"],
    region_name=os.environ["AWS_REGION"],
)

# Attach the tool's schema to every request. The model can now *ask* for
# get_stock_price, but nothing in this file ever runs it.
model_with_tools = model.bind_tools([get_stock_price])


if __name__ == "__main__":
    print("---question a tool can answer---")
    response = model_with_tools.invoke("What's the price of AAPL right now?")
    print("content:   ", repr(response.content))
    print("tool_calls:", response.tool_calls)

    print("---question no tool can answer---")
    response = model_with_tools.invoke("In one sentence, what is a P/E ratio?")
    print("content:   ", repr(response.content))
    print("tool_calls:", response.tool_calls)
