import os

from dotenv import load_dotenv
from langchain_aws import ChatBedrockConverse

load_dotenv()

model = ChatBedrockConverse(
    model=os.environ["BEDROCK_MODEL_ID"],
    region_name=os.environ["AWS_REGION"],
)

response = model.invoke("Explain what you do in one sentence.")
print(response.content)
