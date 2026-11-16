"""Search the SEC filings knowledge base (Bedrock Managed Knowledge Base).

This is the RAG part of the agent: filings are cut into section chunks
(sec_edgar.chunk_files) and embedded by Bedrock, and `search` returns the
passages closest in meaning to a question.
`rerank` then reorders them with a reranking model, which reads the question
and each passage together (live_filings uses it too).
"""
import logging
import os
import re

import boto3
from botocore.config import Config

from sec_edgar import parse_key, passage_link

# A whole chunk (sec_edgar.CHUNK_CHARS): cutting it shorter hid the answer half
# the time (evals/filings.py).
MAX_PASSAGE_CHARS = 1100
# Search wider than we return, at the knowledge base's maximum. Semantic search
# alone ranks some answers far down: JPMorgan's CET1 requirement sits in a chunk
# about stress testing, 48th for "how much capital does JPMorgan have to hold?"
# (evals/filings.py). Cohere Rerank bills per query of up to 100 passages, so
# reranking 100 costs what 25 did. About a third of what comes back is dropped
# (tables of contents, copies of a passage from another filing).
CANDIDATES = 100
# Share of the shorter passage's 3-word runs the other one has for the two to
# count as the same passage. 10-Qs repeat the 10-K's risk and legal paragraphs,
# and without this the top 5 for "is Google being sued?" was one paragraph five
# times. At 0.7 they are the same paragraph, or the same lawsuit told twice;
# below that, passages start to differ in substance.
DUPLICATE_OVERLAP = 0.7
# Reads the question and passage together, so it can tell "what if the CEO
# leaves?" (key-person risk) from passages that only share its words (CEO
# pay-award vesting terms). RERANK_MODEL="" turns it off.
RERANK_MODEL = "cohere.rerank-v3-5:0"

log = logging.getLogger(__name__)
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
    s3 = (result.get("location") or {}).get("s3Location") or {}
    return s3.get("uri") or ""


# "Item 1A. | Risk Factors | 12": a table-of-contents row. These chunks match
# topic queries well (they name every section) but say nothing.
_TOC_ROW = re.compile(r"Item\s*\d+\s*[A-C]?\s*\.?\s*\|", re.I)


def is_table_of_contents(text: str) -> bool:
    return len(_TOC_ROW.findall(text)) >= 3


def _shingles(text: str) -> set[tuple[str, ...]]:
    words = re.findall(r"[a-z0-9]+", text.lower())
    return {tuple(words[i:i + 3]) for i in range(len(words) - 2)}


def distinct(passages: list[dict]) -> list[dict]:
    """The passages with near-copies merged, in the same order.

    A passage that repeats an earlier one (DUPLICATE_OVERLAP) takes that one's
    place if its filing is newer, so the agent quotes the latest filing, unless
    it is much shorter: a 10-Q's one-line recap of a 10-K paragraph doesn't
    replace the paragraph. A much longer copy replaces a recap, whatever its date.
    """
    kept: list[tuple[set, dict]] = []
    for passage in passages:
        words = _shingles(passage["text"])
        for n, (other_words, other) in enumerate(kept):
            shared = len(words & other_words) / (min(len(words), len(other_words)) or 1)
            if shared >= DUPLICATE_OVERLAP:
                fuller = len(other_words) < DUPLICATE_OVERLAP * len(words)
                similar = len(words) >= DUPLICATE_OVERLAP * len(other_words)
                if fuller or (similar and passage["filed"] > other["filed"]):
                    kept[n] = (words, passage)
                break
        else:
            kept.append((words, passage))
    return [p for _, p in kept]


def _rerank_text(passage: dict) -> str:
    """The passage with the context line chunk_files gives it: without its section the reranker scored worse."""
    return (f"{passage['ticker']} {passage['form']} filed {passage['filed']}. "
            f"{passage.get('section') or ''}. {passage['text']}")


def rerank(query: str, passages: list[dict], limit: int) -> list[dict]:
    """The `limit` passages that best answer `query`, by the reranking model.

    Each passage gets "reranked": True. If the model can't be reached
    (throttled, no access, off), the passages keep their order, marked False.
    """
    model = os.environ.get("RERANK_MODEL", RERANK_MODEL)
    if not model or len(passages) <= 1:
        return [{**p, "reranked": False} for p in passages[:limit]]
    client = _runtime()
    try:
        response = client.rerank(
            queries=[{"type": "TEXT", "textQuery": {"text": query[:1000]}}],
            sources=[{"type": "INLINE", "inlineDocumentSource": {"type": "TEXT", "textDocument": {"text": _rerank_text(p)}}}
                     for p in passages],
            rerankingConfiguration={"type": "BEDROCK_RERANKING_MODEL", "bedrockRerankingConfiguration": {
                "numberOfResults": min(limit, len(passages)),
                "modelConfiguration": {"modelArn": f"arn:aws:bedrock:{client.meta.region_name}::foundation-model/{model}"}}},
        )
    except Exception:  # noqa: BLE001 - search still works, in the first ranking's order
        log.warning("reranking failed; keeping the search order", exc_info=True)
        return [{**p, "reranked": False} for p in passages[:limit]]
    return [{**passages[r["index"]], "reranked": True} for r in response["results"]]


def search(query: str, ticker: str | None = None, limit: int = 5) -> list[dict]:
    """Passages from indexed filings most relevant to `query`, best first.

    Each passage is one chunk (see sec_edgar.chunk_files) with the section it
    came from and a link that opens the filing at that passage. The knowledge
    base's top candidates are reranked (see `rerank`).
    """
    config: dict = {"numberOfResults": CANDIDATES}
    if ticker:
        # Each chunk's .metadata.json names its company, so the knowledge
        # base returns only that company's chunks.
        config["filter"] = {"equals": {"key": "ticker", "value": ticker}}
    response = _runtime().retrieve(
        knowledgeBaseId=os.environ["KB_ID"],
        retrievalQuery={"text": query[:1000]},
        # Managed KBs take managedSearchConfiguration; vectorSearchConfiguration is rejected.
        retrievalConfiguration={"managedSearchConfiguration": config},
    )
    passages = []
    for r in response.get("retrievalResults", []):
        key = _location_uri(r)
        meta = parse_key(key)
        # Whole-filing objects from before chunking (no section) aren't used.
        if meta is None or "/chunks/" not in key:
            continue
        # Filing text is third-party: no <...> so it can't forge a source link.
        raw = (r.get("content") or {}).get("text", "").replace("<", " ").replace(">", " ")
        # The first line is the context header chunk_files added; the tool
        # result states the company, form, and section itself. Checked, not
        # assumed: a result without it (or with its line breaks flattened)
        # keeps all its text.
        first, _, rest = raw.partition("\n")
        header = f"({meta['ticker']}) {meta['form']} filed {meta['filed']}." in first
        body = rest if header and rest.strip() else raw
        text = " ".join(body.split())
        if not text or is_table_of_contents(text):
            continue
        section = (r.get("metadata") or {}).get("section") or ""
        passages.append({**meta, "section": section, "text": text[:MAX_PASSAGE_CHARS],
                         "url": passage_link(meta["url"], body.strip()), "score": r.get("score")})
    return rerank(query, distinct(passages), limit)
