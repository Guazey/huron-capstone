import os

from botocore.config import Config
from dotenv import load_dotenv
from langchain_aws import ChatBedrockConverse

from tools import get_stock_price

load_dotenv()

# Bedrock throttles and can hang; bound every call so a bad request fails
# fast and a transient error is retried instead of killing the run.
bedrock_config = Config(
    connect_timeout=5,
    read_timeout=30,
    retries={"max_attempts": 3, "mode": "adaptive"},
)

model = ChatBedrockConverse(
    model=os.environ["BEDROCK_MODEL_ID"],
    region_name=os.environ["AWS_REGION"],
    max_tokens=1024,
    config=bedrock_config,
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
