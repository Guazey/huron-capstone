# ADR-0011: Score cases failed by an upstream outage apart from failures

**Date:** 2026-11-23
**Status:** accepted
**Decided by:** FDE (Matt Corey)
**SA reviewed:** n/a (capstone)

## Context
The weekly eval failed two Sundays in a row for reasons outside this code:

| Run | What broke | Effect |
|---|---|---|
| 2026-11-16 | Bedrock `ThrottlingException` on Converse, even after the one retry | A batch of FinanceBench cases recorded as failures |
| 2026-11-23 (scheduled and rerun) | Yahoo Finance's news endpoint returned "YAHOO! FINANCE IS CURRENTLY DOWN!" | behavior 14/18, under the 0.8 bar; the news and page cases failed for lack of headlines and a source link |

The agent did the right thing during the Yahoo outage: it said the data was
unavailable instead of inventing it. But the checks can't pass without tool
data, so CI went red on an outage, and a real regression would look the same.

## Decision
A case that **fails** while a known outage happened is recorded with
`upstream: "<outage>"`, printed as `[UPSTREAM]`, and left out of the pass
rate. The summary counts it under `upstream`, and the trend report shows it
as `14/15 (93%) +3 upstream`.

- **Detection** (`eval.upstream_reason`) follows explicit causes
  (`raise ... from e`), not implicit context, so a bug raised while handling
  an outage stays a bug. It matches a deliberately short list: yfinance's
  "currently down" error and rate limit; Bedrock `ThrottlingException`,
  `ServiceUnavailableException`, `InternalServerException`,
  `ModelNotReadyException`; AWS connection failures and timeouts; connection
  errors and timeouts from `requests` (SEC) and `curl_cffi` (Yahoo); and
  HTTP 429/5xx (SEC). Everything
  else still fails the case. That includes `AccessDeniedException`, which
  caught the CI role's missing `bedrock:Rerank` on 2026-11-23, other
  `YFDataException`s (parse errors, missing data), 404s, certificate and
  proxy errors (our machine's setup), and our own exceptions.
- **Capture:** `tools_node` already turns a tool's exception into an error
  result and logs it with `log.exception` on the `capstone` logger. The eval
  attaches a handler there for each case and reads the exception back, so no
  product code changed. It reads only `tools_node`'s records: other code logs
  fallbacks it recovered from on the same logger (`live_filings` ranks by
  keywords when embedding fails).
- **A crash is judged by its own exception only.** An exception that escapes
  the graph (Bedrock throttling in FinanceBench) is classified in `run_case`'s
  crash handler. An outage a tool hit earlier in the same case doesn't count:
  `tools_node` already turned that into a result, so a later crash is our bug.
- **A case that passes despite an outage is a pass.** Only failures are excluded.
- **Inconclusive runs fail the bar:** if outages leave under half of a suite
  scored (`MIN_SCORED_SHARE`), `--min-pass-rate` fails it as "inconclusive"
  instead of passing on what's left.

## Alternatives considered
| Option | Why not |
|---|---|
| Retry the whole run on any failure | Doubles the cost (~$0.57 a run), and a real regression fails twice |
| Mark a case upstream when any tool returned an error result | Also hides our own bugs: `tools_node` returns the same "data source is unavailable" text for a `KeyError` in our code |
| Stub Yahoo in CI | Removes the live-data checks the behavior suite exists for (ADR-0006) |
| Only exclude a failure when its reason is about missing data | Reasons are free text from many checks. Matching them would break each time a check changes |

## Consequences
- A failed case that hit an outage isn't scored even if it would have failed
  anyway. The SpaceX "privately held" miss on 2026-11-23, for example, would
  have been hidden had a Yahoo call failed in that case. The `upstream`
  count is shown on every run so this stays visible.
- An outage that returns bad data without raising isn't detected. On
  2026-11-23 yfinance sometimes returned a NaN price instead of an exception.
  Those cases still fail.
- Throttling can be self-inflicted. A regression that bursts tool or model
  calls, or blows up the prompt until Bedrock throttles on tokens, would read
  as an outage. `MIN_SCORED_SHARE` limits how much that can hide, and a run
  of upstream counts on a healthy day is the signal to look.
- A whole category can be unscored while its suite passes (news, during a
  Yahoo news outage). The report shows it as `news - +1 upstream`.
- `summary.pass_rate` now means passed / scored. Saved runs from before this
  change stored passed / total and have no `upstream` or `scored` fields; the
  report reads both. Comparing raw `pass_rate` across that line mixes the two.
