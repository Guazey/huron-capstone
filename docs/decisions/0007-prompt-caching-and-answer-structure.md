# ADR-0007: Prompt caching, and a fixed shape for answers

**Date:** 2026-10-24
**Status:** accepted
**Decided by:** FDE
**SA reviewed:** n/a (capstone)

## Context
- **Cost and speed.** One question takes several model calls: ask for tools,
  read the results, maybe ask for more, then answer. Each call resends the
  tool schemas, the system prompt, and the whole turn so far. A three-call
  question sent about 13,600 input tokens, most of them the same prefix
  again.
- **Answer quality.** Answers were correct and sourced but read like notes:
  no consistent lead, numbers without comparison, and explanations from the
  model's memory ("this reflects Microsoft's cloud and software mix") that no
  tool returned. The verify step only catches $ and % figures, so a
  memory-sourced reason or a benchmark like "the market averages 18-22x"
  slips past it.

## Decision
1. **Bedrock prompt caching** (`model.py`): `bind_tools(..., cache_control=...)`
   makes langchain-aws place cache points after the tools, the system prompt,
   and the latest message. Each later call in a turn reads the earlier prefix
   from cache (billed at 0.1x input; writes at 1.25x).
2. **Cost accounting follows** (`evals/results.py`, `app.py`): runs record
   cache read and write tokens and price them at their own rates, so the
   cost column stays comparable with runs from before caching. The request
   log line gains `cache_read_tokens`.
3. **An answer shape in the system prompt** (`prompts.py`, "How to write the
   answer"):
   - a bold one-sentence bottom line with the key figure;
   - 2-4 evidence bullets, each figure with a comparison the tools returned
     (prior period, estimates, peers, 52-week range);
   - explanations only from tool results, and no benchmarks from memory: to
     compare with a peer, look the peer up;
   - a "What to watch:" line for analysis questions when the tool results
     support one (next earnings date, a filing risk);
   - one or two sentences for simple lookups; under about 150 words.
   - One worked example answer. Haiku followed the rules list loosely and the
     example closely.
   - "Why" questions with no explanation in the data now say so and stop,
     instead of listing generic causes or sending the user off to research.

## Measured (same commit, before vs. after)
| | Before (2026-10-04) | After (2026-10-24) |
|---|---|---|
| Behavior suite | 16/16 | 16/16 |
| FinanceBench | 47/48, est. $0.88 | 47/48, est. $0.44 |
| FinanceBench p50 latency | 16.8s | 15.1s |
| Behavior p50 latency | 7.3s | 7.9s |

A single comparison question: call 3 read 5,305 of its 5,312 input tokens
from cache on a warm run.

## Alternatives considered
| Option | Why not |
|---|---|
| Cache points placed by hand in the messages | langchain-aws already places them correctly and respects Bedrock's limit of 4; hand placement means maintaining Converse block formats ourselves |
| 1-hour cache TTL | Writes cost 2x instead of 1.25x. The win is within one turn (seconds apart), which the 5-minute TTL covers |
| An LLM judge to grade answer style | Another model call per case, and style is partly taste. The behavior suite already checks the things that matter (grounding, links, sources) |
| Pad the system prompt past the cache minimum | The first call's prefix is about 4,000 tokens, just under Haiku 4.5's 4,096 minimum, so it isn't cached. Padding to cross it would add tokens to every call to save a little on some |

## Consequences
- Easier: about half the input cost per multi-step question; the eval cost
  column shows it.
- Harder: answers are slightly longer and analysis questions now gather a
  bit more evidence (earnings for a "why"), so the behavior suite's p50 rose
  about 0.6s. FinanceBench, which is mostly lookups, got faster.
- Known limits:
  - The first call of each turn isn't cached (prefix below the minimum).
  - The no-memory-explanations rule is a prompt rule; Haiku still sometimes
    writes vague context like "far above historical norms". The verify step
    can't catch statements without figures.
- Follow-ups: model routing (roadmap item 6) would let analysis questions use
  a stronger model that follows the shape more reliably.
