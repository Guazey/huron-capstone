# ADR-0009: Ship c2b51a9 with two FinanceBench misses judged as model variance

**Date:** 2026-10-24
**Status:** accepted
**Decided by:** FDE (Matt Corey), as an exception to the pre-push gate
**SA reviewed:** n/a (capstone)

## Context
The pre-push gate blocks a push on any eval regression. After the prompt fix
in c2b51a9 (no Sources line when no tool returned a link), behavior scored
18/18 but FinanceBench scored 45/48, against 47/48 on 2302016 an hour
earlier. Two cases that passed there failed:

| Case | Answer | Expected | Why it looks like variance |
|---|---|---|---|
| `financebench_id_03882` Amcor FY2020 net AR | 1,609 | 1,616 | Took the FY2021 10-K's comparative column instead of the original filing: the same data-vintage miss as `07661` (Block) |
| `financebench_id_03718` Lockheed 2-year revenue CAGR | 0.5 | 0.4% | Computed 0.45% correctly, then rounded it up in the final line |

Neither answer involves a Sources line or an empty tool result, the only
behavior c2b51a9 changed. Both pulled their figures through `get_financials`
as usual. `07661` fails on every run and isn't new.

## Decision
Push the 7 commits with FinanceBench at 45/48 and no rerun, and treat the two
misses as run-to-run model variance unless they recur.

## Alternatives considered
| Option | Why not |
|---|---|
| Rerun FinanceBench (~$0.47, ~20 min) | Would show whether the misses recur, but neither touches the changed rule; Matt chose not to spend another run before pushing |
| Revert c2b51a9 | Brings back the invented-link failure on empty feeds, a real defect, to remove two misses it didn't plausibly cause |
| Hold the push | Leaves the E-Trade and stale-ticker fixes unshipped |

## Consequences
- Easier: the page-context fixes and the empty-feed prompt fix ship today.
- Harder: c2b51a9's FinanceBench number is 45/48, not a confirmed 47/48. A
  real regression of 1-2 cases could be hiding in that variance.
- Follow-ups: the next FinanceBench run (the weekly CI run at the latest)
  confirms or refutes this. If `03882` or `03718` fails again, look at the
  answer-shape rules in `prompts.py` (rounding in the final "Answer:" line)
  before anything else. Single-run evals can't tell a 1-2 case regression
  from noise on this suite; repeated runs or a per-case pass rate would.
