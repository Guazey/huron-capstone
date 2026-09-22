# Architecture: market sidebar on AgentCore

A Chrome side panel that sits next to a trading site. You ask it about a
ticker and it answers from live market data. The agent is the same LangGraph
graph as the CLI, hosted on Amazon Bedrock AgentCore Runtime.

## Diagram

```
┌──────────────────────────┐
│ Chrome side panel (MV3)  │  React + Vite
│  chat UI, streams tokens │  reads the ticker off the current page
│  login via Cognito       │  holds a login token, never AWS keys
└────────────┬─────────────┘
             │ POST /runtimes/{arn}/invocations
             │   Authorization: Bearer <Cognito access token>
             │   X-Amzn-Bedrock-AgentCore-Runtime-Session-Id: <one per chat, >=33 chars>
             ▼   response: SSE stream
┌──────────────────────────────────────────────────────────┐
│ AgentCore Runtime (serverless, ARM64 container, HTTP)     │
│   inbound auth: JWT authorizer -> Cognito user pool       │
│   execution role: bedrock:InvokeModel* on one model only  │
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
| Tools: `get_stock_price`, `get_price_history` | `tools.py` | done (slice 1) |
| Agent graph, nodes, prompt, model | `graph.py`, `nodes.py`, `prompts.py`, `model.py` | done; prompt updated for sidebar use |
| AgentCore entrypoint that streams the graph's output | `app.py` | done (slice 2), runs locally |
| ARM64 container + runtime + Cognito | `Dockerfile`, `infra/deploy.sh`, `invoke.sh`, `teardown.sh` | slice 3: written, not yet run |
| Side panel extension | `extension/` | slice 4 |
| Multi-turn memory + page ticker detection | `app.py`, `extension/` | slice 5 |

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

## Security and data

- **No AWS credentials on the client.** The only secret the extension holds
  is a short-lived Cognito token.
- **The execution role** can invoke one Bedrock model and write its own log
  group. Nothing else.
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
