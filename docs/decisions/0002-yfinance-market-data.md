# ADR-0002: Use yfinance for market data, isolated in market_data.py

**Date:** 2026-09-22
**Status:** accepted
**Decided by:** FDE
**SA reviewed:** n/a (capstone)

## Context
The tool returned hardcoded prices for three tickers. A sidebar used next to
real trades can't show fake prices. We need free, keyless data for stocks,
ETFs and indexes to build the demo.

## Decision
Use yfinance, and only inside `market_data.py`. Validate tickers before any
call, cache results for 60 seconds, time out after 10 seconds, and treat an
empty history as "not found".

## Alternatives considered
| Option | Why not |
|---|---|
| Polygon / Alpaca / Finnhub | Needs an API key and a secret in Secrets Manager. It's the right move for real users, but not needed to prove the build |
| Keep the hardcoded prices | Misleading in a trading context |

## Consequences
- Easier: no key and no signup. Covers stocks, ETFs, indexes (`^GSPC`), FX (`EURUSD=X`) and foreign listings (`RY.TO`).
- Harder: it's unofficial scraping. It can break or be rate-limited, Yahoo can block cloud IPs, some quotes are delayed, and Yahoo's terms are personal use only. The eval can't check fixed prices, so it checks that every dollar amount in an answer came from a tool result.
- Follow-ups: if Yahoo blocks the AgentCore egress or this goes beyond a demo, replace `market_data.py` with a keyed provider. The key goes in Secrets Manager, and nothing else changes.
