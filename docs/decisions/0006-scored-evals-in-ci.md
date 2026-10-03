# ADR-0006: Scored evals with FinanceBench, tracked over time, run in GitHub Actions through OIDC

**Date:** 2026-10-03
**Status:** accepted
**Decided by:** FDE
**SA reviewed:** n/a (capstone)

## Context
`eval.py` printed PASS/FAIL for 13 to 16 hand-written cases, on demand, and
the result vanished with the terminal. That left three gaps:
- **No trend.** "Did this prompt change help?" was answered from memory.
- **No outside yardstick.** Every case was ours, so the agent could look good
  on questions shaped around how it already works.
- **No automation.** The live eval needs AWS, so CI only ran unit tests.

## Decision
1. **Score every run and save it.**
   - `evals/results.py` records, per case: pass/fail, category, latency,
     tokens, tools called, figures checked, and verifier rewrites.
   - Per run, it records pass rate overall and by category, p50/p95 latency,
     tokens, and an estimated cost.
   - Each run is written to `evals/history/<time>-<commit>-<suite>.json`.
     `python -m evals.results` prints the trend and the latest failures.
2. **Add FinanceBench's numeric questions as a second suite.**
   - FinanceBench (Patronus AI) has 50 "metrics-generated" questions about
     real 10-Ks with one numeric answer. 48 are usable; Activision Blizzard is
     delisted.
   - The agent ends with `Answer: <number>`, and code grades it at the
     expected answer's precision. Trailing zeros are formatting, so "$0.40"
     means 0.4.
   - Units are forgiven (1,577 million = 1.577 billion); precision isn't.
   - The questions are downloaded from a pinned commit at run time, not
     copied: the FinanceBench repository has no license.
   - To answer them, `get_financials` gained `fiscal_year` (history back to
     about 2010) and nine line items: COGS, D&A, dividends, receivables,
     inventory, current assets, PP&E, payables, current liabilities.
   - A restated figure now also shows the value first reported, with its
     filing.
3. **Run it in GitHub Actions without stored AWS keys.**
   - The `eval` workflow runs on demand and weekly, never on push, because
     each run costs about $0.90.
   - It gets a 1-hour session on `capstone-sidebar-eval-ci` by trading
     GitHub's OIDC token.
   - The role trusts only `repo:Guazey/huron-capstone:ref:refs/heads/main`,
     so pull requests and other branches can't get credentials.
   - It may call one model and search one knowledge base, and the project
     permissions boundary caps it.
   - The run fails below an 80% pass rate. Scores are posted to the run page
     and committed to `evals/history/`.

## Alternatives considered
| Option | Why not |
|---|---|
| Access keys for an IAM user in GitHub secrets | Long-lived credentials that leak with any compromised workflow and must be rotated by hand. OIDC sessions expire in an hour and are scoped to one branch |
| Run the live eval on every push | About $0.90 and 15 minutes a run, mostly re-measuring unchanged code. Unit tests (no AWS) still run on every push |
| An LLM judge for FinanceBench answers | Costs a second model call per question, and its grade can drift. A number at a stated precision can be checked exactly |
| All 150 FinanceBench questions | The other 100 are open-ended ("Does 3M have a healthy liquidity profile?") and need a judge. They are also mostly about passages, which only our 10-company filings index covers |
| AgentCore Evaluations on production traces | Complements this rather than replacing it: it scores live traffic, not a fixed benchmark, so it can't say whether a change made the same questions better. Candidate for item 7 (governance) |
| LangSmith / Ragas / DeepEval | Another vendor and account for what is a JSON file per run. Worth it with many contributors or large datasets |

## Consequences
- Easier: every change gets a before/after number, by category, with its
  cost. The FinanceBench score can be compared with published results:
  - FinanceBench's paper reports GPT-4-Turbo with retrieval got 81% of
    questions wrong or refused.
  - Those results are on all 150 questions, not this numeric subset, so the
    comparison is indicative, not like for like.
- Harder:
  - CI needs a one-time admin step: attach `deployer-ci-policy.json` to the
    deployer, then run `infra/ci_role.sh`.
  - The workflow can push to main, but only the history files. It runs only
    from main, on schedule or dispatch.
  - Yahoo may rate-limit GitHub's runner IPs; that shows up as market-data
    case failures, not silence.
- Known limits:
  - Token counts miss drafts the verify step removed.
  - Cost is estimated at Haiku 4.5 list price.
  - FinanceBench answers come from the 10-K as first filed, while
    `get_financials` returns the latest value. A restated figure therefore
    "fails" unless the agent uses the original that the tool now shows.
- Follow-ups:
  - Raise the 80% bar as scores improve.
  - Add AgentCore Evaluations on production traces (item 7).
  - Add an LLM-judged subset of the open-ended questions once the filings
    index covers more companies (item 4).
