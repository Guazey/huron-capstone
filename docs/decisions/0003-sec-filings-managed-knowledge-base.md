# ADR-0003: SEC filings search with a Bedrock Managed Knowledge Base (RAG)

**Date:** 2026-09-22
**Status:** accepted
**Decided by:** FDE
**SA reviewed:** n/a (capstone)

## Context
Users ask what companies say about themselves: risks, strategy, IPO proceeds.
That lives in SEC filings, which are long documents rather than live data, so
the answer comes from searching stored text by meaning (RAG).

## Decision
Load the latest 10-K and two 10-Qs (or the IPO prospectus) for a 10-company
watchlist from SEC EDGAR into S3, and index them with a Bedrock **Managed**
Knowledge Base. The agent queries it through a `search_sec_filings` tool with
`Retrieve`.

## Alternatives considered
| Option | Why not |
|---|---|
| Customer-managed KB + OpenSearch Serverless | A vector store to run and pay for around the clock; the managed KB handles storage, chunking, and embeddings |
| pgvector / local vector DB | Not AWS-native and not hosted; the capstone is about the AWS stack |
| Fetch filings live per question | A 10-K is up to 1.5 MB of text; too slow and too many tokens to read per question |
| AgenticRetrieveStream | The LangGraph agent already plans and iterates; single-shot `Retrieve` keeps one agent in charge |

## Consequences
- Easier: no vector store to operate. Citations are exact, because each S3 key rebuilds the sec.gov URL.
- Harder: filings go stale until `sec_edgar.py` is rerun. Only watchlist companies are searchable.
- Found in the live test: managed KBs need `managedSearchConfiguration` (not `vectorSearchConfiguration`), and their filters don't support `stringContains`/`startsWith`, so the per-company filter runs client-side on the S3 key. Table-of-contents chunks rank high for broad queries and are dropped.
- Follow-ups: schedule a refresh (EventBridge + Lambda) when new 10-Qs land; add `.metadata.json` sidecars so filtering can use `equals` on the server.
