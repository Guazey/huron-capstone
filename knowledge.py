"""Search the SEC filings knowledge base (Bedrock Managed Knowledge Base).

This is the RAG part of the agent: filings are chunked and embedded by
Bedrock, and `search` returns the passages closest in meaning to a question.
"""
import os
import re

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


# "Item 1A. | Risk Factors | 12": a table-of-contents row. These chunks match
# topic queries well (they name every section) but say nothing.
_TOC_ROW = re.compile(r"Item\s*\d+\s*[A-C]?\s*\.?\s*\|", re.I)


def is_table_of_contents(text: str) -> bool:
    return len(_TOC_ROW.findall(text)) >= 3


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
        # Filing text is third-party: no <...> so it can't forge a source link.
        raw = (r.get("content") or {}).get("text", "").replace("<", " ").replace(">", " ")
        text = " ".join(raw.split())
        if is_table_of_contents(text):
            continue
        passages.append({**meta, "text": text[:MAX_PASSAGE_CHARS], "score": r.get("score")})
        if len(passages) == limit:
            break
    return passages
