# Decisions

| ADR | Decision | Status |
|---|---|---|
| [0001](0001-agentcore-runtime-hosting.md) | Host the LangGraph agent on AgentCore Runtime, called directly from the Chrome extension | accepted |
| [0002](0002-yfinance-market-data.md) | yfinance for market data, isolated in `market_data.py` | accepted |
| [0003](0003-sec-filings-managed-knowledge-base.md) | SEC filings search with a Bedrock Managed Knowledge Base (RAG) | accepted |
| [0004](0004-tauri-desktop-app.md) | Tauri for the desktop app, sharing the extension's UI | accepted |
| [0005](0005-traceable-numbers.md) | Traceable numbers: SEC XBRL figures, a calculator tool, and a verify node that checks every figure | accepted |
| [0006](0006-scored-evals-in-ci.md) | Scored evals with FinanceBench, tracked in `evals/history/`, run in GitHub Actions through OIDC (no stored keys) | accepted |
