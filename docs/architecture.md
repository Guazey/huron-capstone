# Architecture: market sidebar on AgentCore

A Chrome side panel that sits next to a trading site. You ask it about a
ticker and it answers from live market data. The agent is the same LangGraph
graph as the CLI, hosted on Amazon Bedrock AgentCore Runtime.

## Diagram

```
┌──────────────────────────┐
│ Chrome side panel (MV3)  │  React + Vite
│  chat UI, streams tokens │  only opens links the tools returned
│  login via Cognito       │  holds a login token, never AWS keys
└────────────┬─────────────┘
             │ POST /runtimes/{arn}/invocations
             │   Authorization: Bearer <Cognito access token>
             │   X-Amzn-Bedrock-AgentCore-Runtime-Session-Id: <one per chat, >=33 chars>
             ▼   response: SSE stream
┌──────────────────────────────────────────────────────────┐
│ AgentCore Runtime (serverless, ARM64 container, HTTP)     │
│   inbound auth: JWT authorizer -> Cognito user pool       │
│   execution role: one model, this memory, this KB, logs   │
│  ┌────────────────────────────────────────────┐           │
│  │ app.py  (BedrockAgentCoreApp entrypoint)    │           │
│  │   -> graph.py (LangGraph, unchanged)        │           │
│  │        tools.py -> market_data.py ──────────┼──────────▶ Yahoo Finance (yfinance)
│  └──────┬──────────────────────────┬──────────┘           │
│         ▼                          ▼                       │
│  Bedrock: Claude Haiku 4.5   AgentCore Memory              │
│  (Converse, max_tokens set)  (chat history per session)    │
└──────────────────────────────────────────────────────────┘
       logs + traces -> CloudWatch / AgentCore Observability
```

## Components

| Piece | File(s) | Status |
|---|---|---|
| Market data layer: the only code that knows yfinance; validates tickers, 60s cache, 10s timeout | `market_data.py` | done (slice 1) |
| 8 tools: `search_ticker`, `get_stock_price`, `get_price_history`, `get_company_profile`, `get_earnings`, `get_news`, `get_market_overview`, `search_sec_filings` | `tools.py` | done |
| Agent graph, nodes, prompt, model | `graph.py`, `nodes.py`, `prompts.py`, `model.py` | done; prompt updated for sidebar use |
| AgentCore entrypoint that streams the graph's output | `app.py` | done (slice 2), runs locally |
| ARM64 container + runtime + Cognito | `Dockerfile`, `infra/deploy.sh`, `invoke.sh`, `teardown.sh` | done (slice 3): deployed, READY, rejects calls without a valid token |
| Side panel extension | `extension/`, `infra/login_setup.sh` | done (slice 4): built; Cognito hosted login + PKCE |
| SEC filings RAG: `sec_edgar.py` → S3 → Bedrock Managed Knowledge Base; `search_sec_filings` tool | `sec_edgar.py`, `knowledge.py`, `infra/deploy.sh` | done ([ADR-0003](decisions/0003-sec-filings-managed-knowledge-base.md)) |
| Desktop app: the same UI in an always-on-top menu bar window; system-browser sign-in with a loopback redirect | `desktop/`, `extension/src/platform.ts`, `infra/login_setup.sh` | built, Cognito callback registered ([ADR-0004](decisions/0004-tauri-desktop-app.md)); sign-in not yet tested end to end |
| Multi-turn memory (AgentCore Memory, keyed by session + Cognito user) | `app.py`, `graph.py`, `infra/deploy.sh` | done; page ticker detection still to do |

## Request flow

1. The user opens the side panel and signs in once. The extension runs
   Cognito's hosted login with PKCE through `chrome.identity.launchWebAuthFlow`
   and keeps the token in `chrome.storage.session`.
2. Each new chat gets a UUID-based session ID. Every message in that chat
   sends the same ID.
3. The Runtime checks the JWT (issuer and client ID) and then routes the call
   to a microVM for that session.
4. `app.py` loads the chat's history from AgentCore Memory and runs the graph.
   It then streams the model's text back as SSE events.
5. The panel renders tokens as they arrive and shows the "as of" date and a
   "not financial advice" footer.

## Decisions

- [ADR-0001](decisions/0001-agentcore-runtime-hosting.md): LangGraph hosted on AgentCore Runtime, called directly from the extension with a Cognito JWT
- [ADR-0002](decisions/0002-yfinance-market-data.md): yfinance for market data, behind `market_data.py`
- [ADR-0003](decisions/0003-sec-filings-managed-knowledge-base.md): SEC filings RAG with a Bedrock Managed Knowledge Base
- [ADR-0004](decisions/0004-tauri-desktop-app.md): Tauri desktop app sharing the extension's UI through `platform.ts`
- Diagram: [`architecture.excalidraw`](architecture.excalidraw) (open at excalidraw.com)

## Security and data

- **No AWS credentials on the client.** The only secret the extension holds
  is a short-lived Cognito token.
- **The execution role** can invoke one Bedrock model, read and write
  events in its own AgentCore Memory, `Retrieve` from its one Knowledge Base,
  pull its own image, and write logs, traces, and metrics. Nothing else. The
  Knowledge Base's role can only read the filings bucket.
- **Injected text can't act.** Headlines, company descriptions, and filing
  passages are third-party text: they're labeled as data, stripped of `<>` so
  they can't forge a source link, and the panel renders no images and opens
  only links the tools returned. A strict extension CSP backs this up.
- **Memory is per user.** It's keyed by session ID and the Cognito user ID
  from the verified token; a deployed request without a readable user is
  refused rather than pooled.
- **Tool arguments come from the model and are untrusted.** Tickers are
  checked against a regex before any network call, and `period` is an enum.
- **Logs:** Runtime logs request and response payloads to CloudWatch, and
  chat text can contain whatever a user types. `app.py` logs only the
  prompt's length. `infra/` sets 14-day retention. A customer-managed KMS key
  on the log group (about $1 a month) is a follow-up before anyone besides
  Matt uses it.
- **No trade actions, ever.** The tools are read-only. The prompt gives
  information, not buy, sell or hold advice, and `eval.py` checks for that.

## Cost (rough, per question)

About 2 to 4 Bedrock calls on Haiku 4.5 (tool call plus answer), roughly
1.5k input and 300 output tokens each. That's a fraction of a cent per
question, plus AgentCore Runtime's per-second compute while a session is
active. yfinance is free. Measured in slice 2: a two-tool question (price +
1-month history) used 1,924 input and 182 output tokens in 2.6s, about
$0.003 on Haiku 4.5 at list price.

## Rollback

- Runtime: every deploy pushes a new immutable image tag. To roll back, rerun `update-agent-runtime` with the previous tag (listed in ECR).
- Remove everything: `infra/teardown.sh`.
- Extension: it's unpacked (not in the Chrome Web Store), so reload the
  previous build.

## Open questions

- Who uses it besides Matt? If nobody, the Cognito pool stays
  admin-created-users-only, with no self sign-up.
- Network mode: PUBLIC is fine for dev because yfinance needs outbound
  internet. VPC mode would need a NAT gateway (about $30 a month).
- Yahoo sometimes blocks requests from cloud IPs. If it blocks AgentCore's
  egress, switch to a keyed provider (ADR-0002 follow-up).
