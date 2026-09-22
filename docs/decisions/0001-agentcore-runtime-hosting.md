# ADR-0001: Host the LangGraph agent on AgentCore Runtime, called directly from the Chrome extension

**Date:** 2026-09-22
**Status:** accepted
**Decided by:** FDE
**SA reviewed:** n/a (capstone)

## Context
The agent is moving from a CLI to a Chrome side panel that people use while
trading. It needs an HTTPS endpoint with login, streaming, and chat memory,
and AWS credentials must never ship inside the browser. The goal is also to
show full-stack AWS work.

## Decision
Keep the LangGraph graph as-is and deploy it to Bedrock AgentCore Runtime
(HTTP protocol, ARM64 container). Inbound auth is a JWT authorizer backed by
a Cognito user pool, and AgentCore Memory keeps each chat's history. The
extension calls the Runtime directly.

## Alternatives considered
| Option | Why not |
|---|---|
| FastAPI on Lambda / App Runner + API Gateway | We'd build and host auth, sessions and scaling that AgentCore already provides, and it gives no AgentCore experience |
| Port the agent to Strands on AgentCore | Rewrites a working, tested graph. Keeping LangGraph proves the README's "framework-agnostic" claim. Still possible later as a comparison branch |
| AgentCore Harness (config-only loop) | Would throw away the graph code, which is the point of the capstone |
| Extension calls Bedrock directly | Puts AWS credentials in the browser |

## Consequences
- Easier: auth, session isolation, scaling, logs and traces come with the Runtime. No API server to run.
- Harder: ARM64 container builds, and AgentCore-specific deployment steps. Local development needs the entrypoint to run outside AgentCore too.
- Follow-ups: Terraform for Cognito and the runtime once the CLI steps are proven; KMS-encrypted log group with retention.
