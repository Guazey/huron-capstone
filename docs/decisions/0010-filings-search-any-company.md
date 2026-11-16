# ADR-0010: Filings search for any company: section chunks, reranking, and live EDGAR

**Date:** 2026-11-16
**Status:** accepted
**Decided by:** FDE
**SA reviewed:** n/a (capstone)

## Context
ADR-0003's filings search covered a 10-company watchlist, and its answers
were often the wrong passage. Bedrock chunked whole filings blind to their
structure, so a chunk could span two Items and said nothing about where it
came from. The only ranking was the knowledge base's semantic search, which
put boilerplate and tables of contents above the answer. Any other company
got "not indexed". There was no measure of retrieval on its own: the
behavior evals only showed whether the final answer was right.

## Decision
1. **A retrieval eval with no model calls** (`evals/filings.py`). 24 questions
   about the watchlist, phrased the way a user asks, each with a pattern only
   the answering passage contains. It scores hit rate in the top 5 and MRR,
   and is saved to `evals/history/` like the other suites.
2. **Section-aware chunks** (`sec_edgar.chunks`). Filings are split by their
   Items, named from the SEC's form instructions ("Item 1A. Risk Factors",
   "Part II, Item 1. Legal Proceedings") rather than the filing's own
   headings; a prospectus by its ALL-CAPS headings. Chunks are about 1,000
   characters, never cross a section, and never split a sentence. Each chunk
   is its own S3 object with a context header and a `.metadata.json` sidecar
   (ticker, form, section), so the per-company filter runs in the knowledge
   base (`equals`) instead of on the client.
3. **Rerank the top 100** (`knowledge.rerank`, Cohere Rerank 3.5 on Bedrock).
   The reranker reads the question and each passage together, with the
   passage's company, form, and section. If it fails (throttling, no
   access), the knowledge base's order is kept and the passages say so.
4. **100 candidates, near-copies merged** (`knowledge.CANDIDATES`,
   `knowledge.distinct`). 100 is the knowledge base's maximum. With 25,
   JPMorgan's CET1 requirement, which sits in a chunk about stress testing,
   was 48th and never reached the reranker. 10-Qs repeat the 10-K's risk and
   legal paragraphs, and "is Google being sued?" got one paragraph five
   times. Passages sharing 70% of their 3-word runs count as one, and the
   newest filing's copy is kept, unless it's much shorter (a one-line recap
   doesn't replace the paragraph).
5. **Any US filer, live from EDGAR** (`live_filings.py`). For a ticker
   outside the watchlist, the tool downloads its latest annual report (or
   prospectus) and latest 10-Q, chunks them the same way, and ranks them by
   BM25 and Cohere embeddings, merged with reciprocal rank fusion, then
   reranked as above. Chunks and float32 embeddings are cached for an hour
   (a minute if embedding failed), for up to 20 companies. When there is
   nothing to search, the tool says why instead of "no matching passages":
   an ETF or fund isn't an SEC company filer, and a foreign company files
   20-F or 40-F, which this doesn't read. The prompt claims only "companies
   that file 10-K/10-Q reports". Without a knowledge base, watchlist
   companies are read live too.
6. **Passages name their section**, and each links to the filing scrolled to
   that passage (a `#:~:text=` fragment of its first words).

## Alternatives considered
| Option | Why not |
|---|---|
| Customer-managed KB with OpenSearch hybrid search | Fixes keyword recall (CET1) but brings a vector store to run and pay for around the clock (ADR-0003); 100 candidates plus reranking got the same case into the top 5 |
| Index every company ahead of time | Thousands of filers, most never asked about; reading one on demand takes 5–7 s the first time |
| Dedupe after reranking | Wastes reranker slots on copies and leaves fewer than 5 distinct passages |
| LLM-written context per chunk (contextual retrieval) | A model call per chunk at ingestion (thousands per company) for a gain the section header already gives most of |

## Consequences
- Filings eval: 12/24 (first run, 3c2475b) → 21/24 with section chunks →
  22–23/24 with reranking, 100 candidates, and merged copies. MRR 0.43 →
  0.83. p50 retrieval about 0.9–1.0 s.
- Eval change: the JPM case's pattern now accepts "CET1 capital regulatory
  ratio requirements" as well as "CET1 capital ratio requirement". Both are
  the filing's own words for what the question asks, and the old pattern
  rejected the 10-K's requirements chart over the word "regulatory".
- Known miss: "Is Meta spending more on data centers?" returns Meta's
  passage on higher infrastructure investment in servers and data centers,
  but not the one with the $135–145B capex guidance the case looks for. A
  fix means changing chunking and reloading the knowledge base.
- Weak: a search with no ticker ranks all companies' chunks together and
  often returns off-topic ones ("which companies depend on TSMC?").
- Foreign issuers (TSM, ASML, BABA) can't be searched: 20-F support would
  need its own section names (Item 3.D Risk Factors, Item 5 Operating
  Review).
- Deploying needs new permissions: `bedrock:Rerank` and InvokeModel on the
  two Cohere models, in the runtime role (`deploy.sh`), the CI role
  (`ci_role.sh`), and the permissions boundary, which an admin updates by
  hand. Until then, reranking falls back to the knowledge base's order and
  live search ranks by BM25 alone; nothing fails.
- Rerank quota is 3 calls a minute here, so `evals/filings.py --pace 21`.
- The knowledge base still holds whole-filing objects from before chunking.
  They are skipped, but they take about a third of the 100 result slots
  until they're deleted and the knowledge base resynced.
- Follow-ups from the pre-push review:
  - Rerank quota: a broad question runs several searches, and 3 reranks a
    minute runs out within one answer. Ask for a quota increase before a
    demo, make the reranker fail fast (no retries), and log whether each
    search was reranked.
  - Tests for `sec_edgar._replace_prefix` (bulk S3 deletes by prefix) and
    `live_filings.filing_chunks` eviction, with stubbed S3 and EDGAR.
  - Run `evals.filings` in the weekly CI workflow, and add cases for the
    live EDGAR path and a filing passage with injected instructions.
  - A lock around the live cache: two first questions about the same
    company at once both download it.
