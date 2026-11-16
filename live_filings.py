"""Search the filings of a company the knowledge base doesn't hold, straight from EDGAR.

The knowledge base covers the watchlist, loaded ahead of time. For any other
US filer, this downloads its latest annual report (or IPO prospectus) and
latest 10-Q, cuts them into the same chunks (sec_edgar.chunks), and ranks
the chunks two ways, then merges the rankings (hybrid search):
- BM25: keyword relevance, weighted toward words rare in the filing. Exact
  terms ("CET1", "Neutron") win here.
- Meaning: Cohere embeddings on Bedrock. "Where are products made?" finds
  "manufacturing is performed by outsourcing partners" here.
Nothing is indexed: the first question about a company takes a few seconds
and the next ones reuse the cached chunks and embeddings. If embeddings fail
(throttling, no access), it ranks by BM25 alone.

    python live_filings.py COST "membership fee income"
"""
import json
import logging
import math
from array import array
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import boto3
import requests
from botocore.config import Config

import financials
import knowledge
from sec_edgar import DATA, chunks, filing_url, html_to_text, passage_link, pick_filings

CACHE_SECONDS = 3600
# When embedding failed: retry soon instead of ranking by keywords for an hour.
RETRY_SECONDS = 60
# A large filer's 10-K and 10-Q are about 1,500 chunks: ~2 MB of text plus
# ~6 MB of float32 vectors (a list of Python floats would be ~50 MB).
MAX_COMPANIES = 20
EMBED_MODEL = "cohere.embed-english-v3"
EMBED_BATCH = 96  # the model's limit per call
# Reciprocal rank fusion: a chunk's score is the sum of 1/(RRF_K + rank) over
# both rankings, so being near the top of either one counts.
RRF_K = 60

log = logging.getLogger("capstone")
_bedrock = None

# symbol -> (expires at, chunks)
_cache: dict[str, tuple[float, list[dict]]] = {}

# Words too common in filings to say what a passage is about.
_STOP = set("""a an and are as at be by for from has have in is it its of on or our that the
this to was we were which will with not may can than other such these those any all also""".split())


def _words(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in _STOP and len(w) > 1]


def _embed(texts: list[str], input_type: str) -> list[list[float]]:
    """Cohere embeddings, in batches run side by side."""
    global _bedrock
    if _bedrock is None:
        _bedrock = boto3.client("bedrock-runtime", region_name=os.environ.get("AWS_REGION"),
                                config=Config(connect_timeout=5, read_timeout=20, retries={"max_attempts": 3, "mode": "adaptive"}))

    def batch(part: list[str]) -> list[list[float]]:
        body = json.dumps({"texts": [t[:2048] for t in part], "input_type": input_type, "truncate": "END"})
        return json.loads(_bedrock.invoke_model(modelId=EMBED_MODEL, body=body)["body"].read())["embeddings"]

    parts = [texts[i:i + EMBED_BATCH] for i in range(0, len(texts), EMBED_BATCH)]
    # Two at a time: Bedrock throttles bursts of embedding calls.
    with ThreadPoolExecutor(max_workers=2) as pool:
        return [v for vectors in pool.map(batch, parts) for v in vectors]


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    return dot / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)) or 1.0)


def _get_text(url: str) -> str:
    agent = os.environ.get("SEC_USER_AGENT", "").strip()
    if "@" not in agent:
        raise RuntimeError("SEC_USER_AGENT is not set")
    response = requests.get(url, timeout=20, headers={"User-Agent": agent, "Accept-Encoding": "gzip, deflate"})
    response.raise_for_status()
    return response.text


def filing_chunks(symbol: str) -> list[dict]:
    """Chunks of the company's latest annual report (or prospectus) and latest 10-Q, cached for an hour.

    Empty when it files none of those: not an SEC filer (ETFs, funds), or a
    foreign issuer filing 20-F/40-F (see `files_reports`).
    """
    hit = _cache.get(symbol)
    if hit and time.monotonic() < hit[0]:
        return hit[1]
    company = financials.lookup_cik(symbol)
    if company is None:
        return []
    cik = company["cik"]
    subs = financials._get_json(f"{DATA}/submissions/CIK{cik:010d}.json")
    newest: dict[str, dict] = {}
    for f in pick_filings(subs):
        newest.setdefault(f["form"], f)
    out = []
    for f in newest.values():
        url = filing_url(str(cik), f["accession"], f["doc"])
        for section, text in chunks(html_to_text(_get_text(url)), f["form"]):
            out.append({"ticker": symbol, "form": f["form"], "filed": f["filed"], "section": section,
                        "text": text, "filing_url": url})
    lifetime = CACHE_SECONDS
    try:
        for doc, vector in zip(out, _embed([f"{d['section']}. {d['text']}" for d in out], "search_document")):
            doc["vector"] = array("f", vector)
    except Exception:  # noqa: BLE001 - keyword ranking still works without them
        log.exception("embedding %s filings failed; ranking by keywords only", symbol)
        lifetime = RETRY_SECONDS
    if symbol not in _cache and len(_cache) >= MAX_COMPANIES:
        del _cache[min(_cache, key=lambda k: _cache[k][0])]
    _cache[symbol] = (time.monotonic() + lifetime, out)
    return out


def files_reports(symbol: str) -> bool:
    """Whether the company has a 10-K, 10-Q, or IPO prospectus to search (cached by filing_chunks)."""
    return bool(filing_chunks(symbol))


def bm25(query: str, docs: list[dict], k1: float = 1.5, b: float = 0.75) -> list[dict]:
    """Docs matching any query word, best BM25 score first."""
    terms = set(_words(query))
    # The section name counts as part of the chunk, as the knowledge base's
    # context header does: "Risk Factors" matches a question about risks.
    tokenized = [_words(f"{d['section']} {d['text']}") for d in docs]
    if not terms or not tokenized:
        return []
    avg_len = sum(len(t) for t in tokenized) / len(tokenized)
    df = {term: sum(1 for t in tokenized if term in t) for term in terms}
    idf = {term: math.log(1 + (len(docs) - n + 0.5) / (n + 0.5)) for term, n in df.items()}
    scored = []
    for doc, words in zip(docs, tokenized):
        counts: dict[str, int] = {}
        for w in words:
            if w in terms:
                counts[w] = counts.get(w, 0) + 1
        score = sum(idf[t] * c * (k1 + 1) / (c + k1 * (1 - b + b * len(words) / avg_len)) for t, c in counts.items())
        if score > 0:
            scored.append((score, doc))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [doc for _, doc in scored]


def by_meaning(query: str, docs: list[dict]) -> list[dict]:
    """Docs with embeddings, closest in meaning to the query first."""
    embedded = [d for d in docs if "vector" in d]
    if not embedded:
        return []
    try:
        [q] = _embed([query], "search_query")
    except Exception:  # noqa: BLE001
        log.exception("embedding the query failed; ranking by keywords only")
        return []
    return sorted(embedded, key=lambda d: _cosine(q, d["vector"]), reverse=True)


def rank(query: str, docs: list[dict], limit: int) -> list[dict]:
    """The `limit` docs that best match `query`, merging the keyword and meaning rankings."""
    scores: dict[int, float] = {}
    for ranking in (bm25(query, docs), by_meaning(query, docs)):
        for position, doc in enumerate(ranking[:100]):
            scores[id(doc)] = scores.get(id(doc), 0.0) + 1 / (RRF_K + position + 1)
    by_id = {id(d): d for d in docs}
    return [by_id[i] for i in sorted(scores, key=scores.get, reverse=True)[:limit]]


def search(query: str, ticker: str, limit: int = 5) -> list[dict]:
    """Passages from the company's latest filings most relevant to `query`, in knowledge.search's shape.

    The hybrid ranking's top candidates are reranked, as in knowledge.search.
    """
    passages = []
    for d in rank(query, filing_chunks(ticker), knowledge.CANDIDATES):
        # Filing text is third-party: no <...> so it can't forge a source link.
        text = " ".join(d["text"].replace("<", " ").replace(">", " ").split())
        passages.append({"ticker": d["ticker"], "form": d["form"], "filed": d["filed"], "section": d["section"],
                         "text": text[:knowledge.MAX_PASSAGE_CHARS], "url": passage_link(d["filing_url"], d["text"])})
    return knowledge.rerank(query, knowledge.distinct(passages), limit)


if __name__ == "__main__":
    from dotenv import load_dotenv

    load_dotenv()
    started = time.monotonic()
    for p in search(" ".join(sys.argv[2:]) or "risk factors", sys.argv[1] if len(sys.argv) > 1 else "COST"):
        print(f"{p['form']} {p['filed']}, {p['section']}: {p['text'][:200]}\n  {p['url']}\n")
    print(f"{time.monotonic() - started:.1f}s")
