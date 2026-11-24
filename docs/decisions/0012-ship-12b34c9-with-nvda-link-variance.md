# ADR-0012: Ship 12b34c9 with one behavior miss judged as model variance

**Date:** 2026-11-23
**Status:** accepted
**Decided by:** FDE (Matt Corey), as an exception to the pre-push gate
**SA reviewed:** n/a (capstone)

## Context
12b34c9 changes only the eval harness (ADR-0011: outages are scored as
upstream, not as failures). The agent code is identical to e907d2e. On the
gate run, behavior scored 16/17 with 1 upstream (Yahoo's news endpoint was
still down), FinanceBench 47/48 (the known Block miss, `07661`), and filings
23/24 (the same as e907d2e). One behavior case that had passed in each of
the previous 5 runs failed:

| Case | What happened | Why it looks like variance |
|---|---|---|
| Compare NVIDIA's last two quarters of revenue | The figures were right ($96.22B vs $81.61B, +17.9%, every figure verified), but the Sources link was an EDGAR company-browse URL the model built itself (`cgi-bin/browse-edgar?...CIK=0001045810&type=10-Q`), not the filing link `get_financials` returned | 12b34c9 doesn't touch the prompt, tools, model, or graph, so it can't change what the agent writes. The case passed 5 runs in a row on the same agent code |

A first filings run also missed a TSLA case. It was run without `--pace 21`,
so Rerank throttled on 12 of 24 cases. That run was discarded (it was never
committed) and rerun with pacing, which matched e907d2e.

## Decision
Push 12b34c9 with this miss, treated as run-to-run model variance unless it
happens again.

## Alternatives considered
| Option | Why not |
|---|---|
| Rerun behavior (~$0.15, ~20 min) | Would show whether the invented link recurs, but no code it exercises changed. Matt chose not to spend another run |
| Hold the push | Leaves the weekly CI run failing on outages, which this change fixes |

## Consequences
- Easier: the next scheduled eval run scores outages as upstream instead of
  failing CI on them.
- Harder: the agent did invent a link once, and the gate's single run can't
  say how often. The URL used NVIDIA's correct company number, so it's a
  real-looking link to a real page, not to the filing the figures came from.
- Follow-ups: if the NVIDIA case fails the same way again, tighten the
  prompt rule that sources must be links a tool returned, starting with
  `get_financials` answers. The ship check also counts a case that was
  upstream in this run but passed in the last one as a regression; that
  check lives outside this repo (Workstation `ship_check.py`) and needs to
  read the new `upstream` field.
