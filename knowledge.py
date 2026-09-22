"""Search the SEC filings knowledge base (Bedrock Managed Knowledge Base).

This is the RAG part of the agent: filings are chunked and embedded by
Bedrock, and `search` returns the passages closest in meaning to a question.
"""
import os

import boto3
from botocore.config import Config

from sec_edgar import parse_key

MAX_PASSAGE_CHARS = 700
# Search wider than we return, then keep one company's passages when asked.
CANDIDATES = 25

_client = None


def _runtime():
    global _client
    if _client is None:
        _client = boto3.client(
            "bedrock-agent-runtime",
            region_name=os.environ.get("AWS_REGION"),
            config=Config(connect_timeout=5, read_timeout=20, retries={"max_attempts": 3, "mode": "adaptive"}),
        )
    return _client


def configured() -> bool:
    return bool(os.environ.get("KB_ID"))


def _location_uri(result: dict) -> str:
    loc = result.get("location") or {}
    for key in ("s3Location", "customDocumentLocation", "webLocation"):
        value = loc.get(key) or {}
        uri = value.get("uri") or value.get("url") or value.get("id")
        if uri:
            return uri
    return ""


def search(query: str, ticker: str | None = None, limit: int = 5) -> list[dict]:
    """Passages from indexed filings most relevant to `query`, best first."""
    q = f"{ticker} {query}" if ticker else query
    response = _runtime().retrieve(
        knowledgeBaseId=os.environ["KB_ID"],
        retrievalQuery={"text": q[:1000]},
        # Managed KBs take managedSearchConfiguration; vectorSearchConfiguration
        # is rejected. Their filters don't support contains/startsWith, so the
        # company filter happens below, from each passage's S3 key.
        retrievalConfiguration={"managedSearchConfiguration": {"numberOfResults": CANDIDATES}},
    )
    passages = []
    for r in response.get("retrievalResults", []):
        meta = parse_key(_location_uri(r))
        if meta is None or (ticker and meta["ticker"] != ticker):
            continue
        text = " ".join((r.get("content") or {}).get("text", "").split())
        passages.append({**meta, "text": text[:MAX_PASSAGE_CHARS], "score": r.get("score")})
        if len(passages) == limit:
            break
    return passages
