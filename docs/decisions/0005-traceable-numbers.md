# ADR-0005: Traceable numbers: SEC XBRL figures, a calculator tool, and a verify node

**Date:** 2026-10-02
**Status:** accepted
**Decided by:** FDE
**SA reviewed:** n/a (capstone)

## Context
The agent's numbers had three weak spots:
- **Fundamentals** came from yfinance's `info`: Yahoo's derived figures with
  no period, form, or filing attached, so "revenue $416B" couldn't be traced.
- **Derived numbers** (growth, margins, differences) were worked out by the
  model in its head. Models get multi-digit arithmetic wrong while sounding
  sure. Published finance-QA studies put arithmetic among the most common
  errors, and code execution fixes most of those.
- **"Every number comes from a tool"** was a prompt rule. `eval.py` checked it
  after the fact, but nothing enforced it for a real user.

Real runs added two more: the model labeled NVIDIA's quarter ending
2026-04-26 "Q1 FY2026" from memory (it's Q1 FY2027), and it stopped
mid-answer to call a tool, which left the user half an answer.

## Decision
1. **`get_financials`, backed by `financials.py`**: the SEC's XBRL
   `companyfacts` API. It is free and official, and covers any US 10-K filer.
   - Each value carries its period dates, form, filing date, and a link to the
     filing.
   - Values are deduplicated so the latest filing (a restatement) wins.
   - Fiscal labels ("Q1 FY2027") come from the *first* filing that reported
     the period; later filings' tags describe themselves, not the period.
2. **`calculate`, backed by `calculator.py`**: arithmetic parsed into a syntax
   tree and evaluated from an allowlist (numbers, operators, abs/round/min/
   max/sqrt). `eval()` is never called. The answer shows the formula's
   inputs ("6.4% ($416.16B vs. $391.04B)").
3. **A `verify` node, backed by `verify.py`**, between the agent's final answer
   and END:
   - It pulls every $, %, percentage-point, and B/M/T figure out of the
     answer, and matches it against numbers in tool results and the user's
     messages, allowing honest rounding and rescaling.
   - A miss sends the answer back once with the list of figures. The panel
     clears the draft (`discard` event).
   - When the check finishes, the rejected draft and the note are removed
     from memory, and the verdict streams as a `checked` event. The panel
     shows "✓ N figures matched to sources" or the figures it couldn't match.
4. **The text the model writes next to a tool call** is dropped from state.
   The model then can't carry on mid-sentence after the result, so the answer
   after the last tool call is always complete.

## Alternatives considered
| Option | Why not (for now) |
|---|---|
| AgentCore Code Interpreter for the math | A sandboxed Python session per question adds latency, cost, IAM, and a network dependency, all for arithmetic. It's the right tool for models and DCFs, not for growth rates and margins. `calculate` is synchronous, deterministic, and unit-tested |
| An LLM "judge" to check numbers | Probabilistic, and costs a second model call per answer. Checking that a number appears in a tool result is a string-matching problem; code does it exactly |
| Bedrock Guardrails contextual grounding check | Scores how grounded an answer is overall, not which figure is wrong, so it can't tell the agent what to fix. Worth adding later for the filings (RAG) answers |
| Paid fundamentals (FMP, Daloopa, Capital IQ) | Keys, cost, and licensing. XBRL is the primary source those vendors build from |
| Ask the model, in the prompt, to label fiscal periods | Tried first; it still guessed from memory. The label now comes from the data |

## Consequences
- Easier: the figures in a fundamentals answer can be traced to a specific filing.
  Derived numbers come from code. An invented figure is caught before the
  answer is final, and flagged if the rewrite still misses. The structured log
  line records `figures_checked`, `figures_unverified`, and `rewrites` (counts,
  never values) for monitoring.
- Harder:
  - A rewrite costs one more model call. In testing it fired in about 1 of
    4 multi-figure answers.
  - The runtime now calls sec.gov, so `SEC_USER_AGENT` must be set at deploy.
  - Apple's facts file is about 4 MB; it's cached for an hour per company.
- Known limits:
  - Bare numbers (a P/E of 31.2, share counts without "B") aren't checked:
    without a unit there's no telling a figure from a label.
  - A correct number the model computed in its head is still flagged as
    unmatched. That's on purpose: "checked" means it came from a tool, not
    that it looks right.
  - The check matches values, not meaning: a real number attached to the
    wrong company would pass.
  - The numbers passed *into* `calculate` aren't themselves checked against
    tool results.
  - Companies that report under IFRS (20-F filers) and funds have no us-gaap
    facts.
- Follow-ups:
  - Check `calculate`'s inputs against earlier tool results.
  - Match figures to the entity they describe.
  - Use AgentCore Code Interpreter if multi-step models (DCF, comps) are added.
  - Add the `ifrs-full` namespace for foreign filers.
