"""Scores for an eval run: per-case measurements, a summary, history on disk, and a trend report.

Every run is saved as evals/history/<UTC time>-<commit>-<suite>.json, so a
change to the prompt, model, or tools shows up as a before/after number
instead of an impression. `python -m evals.results` prints the trend.
"""
import json
import pathlib
import statistics
import subprocess
from datetime import datetime, timezone

HISTORY = pathlib.Path(__file__).resolve().parent / "history"
# Claude Haiku 4.5 list price on Bedrock, USD per million tokens. An estimate
# for comparing runs, not a bill: cross-region profiles and discounts vary.
PRICE_PER_MTOK = {"input": 1.00, "output": 5.00}
# Prompt caching: a cache read costs 0.1x input, a cache write 1.25x.
CACHE_READ_RATE, CACHE_WRITE_RATE = 0.1, 1.25
MAX_ANSWER_CHARS = 600


def usage(messages) -> dict:
    """Tokens and tool calls across the AI messages of one turn.

    Drafts the verify step rejected are removed from state, so their tokens
    aren't counted: the totals slightly undercount turns with a rewrite.
    """
    tokens = {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "cache_write_tokens": 0}
    tools = []
    for m in messages:
        if m.type != "ai":
            continue
        meta = m.usage_metadata or {}
        tokens["input_tokens"] += meta.get("input_tokens", 0)
        tokens["output_tokens"] += meta.get("output_tokens", 0)
        details = meta.get("input_token_details") or {}
        tokens["cache_read_tokens"] += details.get("cache_read", 0)
        # langchain-aws reports writes under the cache's TTL when Bedrock gives one.
        tokens["cache_write_tokens"] += sum(details.get(k, 0) for k in (
            "cache_creation", "ephemeral_5m_input_tokens", "ephemeral_1h_input_tokens"))
        tools += [c["name"] for c in m.tool_calls]
    return {**tokens, "tools": tools}


def case_result(case: dict, passed: bool, reason: str, answer: str, latency_ms: int,
                messages=(), number_check: dict | None = None, upstream: str | None = None) -> dict:
    """One case's scores. `upstream` names an outage the failed case hit (eval.upstream_reason)."""
    check = number_check or {}
    return {
        "id": case.get("id") or case["question"][:60],
        "category": case["category"],
        "passed": passed,
        "reason": reason,
        "upstream": None if passed else upstream,
        "latency_ms": latency_ms,
        **usage(messages),
        "figures_checked": check.get("figures", 0),
        "figures_unverified": len(check.get("unverified", [])),
        "rewrites": check.get("rewrites", 0),
        "answer": answer[:MAX_ANSWER_CHARS],
    }


def cost_usd(input_tokens: int, output_tokens: int, cache_read: int = 0, cache_write: int = 0) -> float:
    """input_tokens includes the cached ones; those are repriced at their own rate."""
    uncached = input_tokens - cache_read - cache_write
    input_cost = uncached + cache_read * CACHE_READ_RATE + cache_write * CACHE_WRITE_RATE
    return (input_cost * PRICE_PER_MTOK["input"] + output_tokens * PRICE_PER_MTOK["output"]) / 1e6


def _is_upstream(case: dict) -> bool:
    # Runs saved before outages were tracked have no "upstream" field.
    return not case["passed"] and bool(case.get("upstream"))


def summarize(cases: list[dict]) -> dict:
    """Totals for a run. A case that failed during an outage isn't scored:
    it counts in `upstream`, not in `passed`/`scored` or the pass rate."""
    by_category: dict[str, dict] = {}
    for c in cases:
        cat = by_category.setdefault(c["category"], {"passed": 0, "total": 0})
        if _is_upstream(c):
            cat["upstream"] = cat.get("upstream", 0) + 1
            continue
        cat["total"] += 1
        cat["passed"] += c["passed"]
    latencies = sorted(c["latency_ms"] for c in cases) or [0]
    input_tokens = sum(c["input_tokens"] for c in cases)
    output_tokens = sum(c["output_tokens"] for c in cases)
    # Runs saved before prompt caching have no cache fields.
    cache_read = sum(c.get("cache_read_tokens", 0) for c in cases)
    cache_write = sum(c.get("cache_write_tokens", 0) for c in cases)
    passed = sum(c["passed"] for c in cases)
    upstream = sum(_is_upstream(c) for c in cases)
    scored = len(cases) - upstream
    return {
        "passed": passed,
        "total": len(cases),
        "scored": scored,
        "upstream": upstream,
        "pass_rate": round(passed / scored, 4) if scored else 0.0,
        "by_category": dict(sorted(by_category.items())),
        "latency_ms_p50": round(statistics.median(latencies)),
        "latency_ms_p95": latencies[min(len(latencies) - 1, round(0.95 * (len(latencies) - 1)))],
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read_tokens": cache_read,
        "cache_write_tokens": cache_write,
        "est_cost_usd": round(cost_usd(input_tokens, output_tokens, cache_read, cache_write), 4),
        "rewrites": sum(c["rewrites"] for c in cases),
        "figures_unverified": sum(c["figures_unverified"] for c in cases),
    }


def _commit() -> str:
    try:
        sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True,
                             text=True, check=True).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"],
                               capture_output=True, text=True, check=True).stdout.strip()
        return sha + ("-dirty" if dirty else "")
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def save(suite: str, model: str | None, cases: list[dict], where: pathlib.Path = HISTORY) -> pathlib.Path:
    now = datetime.now(timezone.utc)
    commit = _commit()
    run = {
        "run_at": now.isoformat(timespec="seconds"),
        "commit": commit,
        "suite": suite,
        "model": model,
        "summary": summarize(cases),
        "cases": cases,
    }
    where.mkdir(parents=True, exist_ok=True)
    path = where / f"{now:%Y%m%dT%H%M%SZ}-{commit}-{suite}.json"
    path.write_text(json.dumps(run, indent=1) + "\n")
    return path


def load_history(where: pathlib.Path = HISTORY) -> list[dict]:
    return [json.loads(p.read_text()) for p in sorted(where.glob("*.json"))]


def _pct(passed: int, total: int, upstream: int = 0) -> str:
    score = f"{passed}/{total} ({100 * passed / total:.0f}%)" if total else "-"
    return f"{score} +{upstream} upstream" if upstream else score


def _run_pct(s: dict) -> str:
    # Older summaries have no "scored": every case was scored then.
    return _pct(s["passed"], s.get("scored", s["total"]), s.get("upstream", 0))


def report(runs: list[dict], last: int = 15) -> str:
    """Markdown: the trend of recent runs, then what failed in the newest one of each suite."""
    if not runs:
        return "No eval runs saved yet. Run `python eval.py`."
    lines = [
        "| Run (UTC) | Commit | Suite | Passed | p50 latency | Est. cost | Rewrites | Unmatched figures |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for run in runs[-last:][::-1]:
        s = run["summary"]
        lines.append(
            f"| {run['run_at'][:16].replace('T', ' ')} | `{run['commit']}` | {run['suite']} | "
            f"{_run_pct(s)} | {s['latency_ms_p50'] / 1000:.1f}s | ${s['est_cost_usd']:.2f} | "
            f"{s['rewrites']} | {s['figures_unverified']} |"
        )
    latest: dict[str, dict] = {}
    for run in runs:
        latest[run["suite"]] = run
    for suite, run in latest.items():
        cats = ", ".join(f"{name} {_pct(c['passed'], c['total'], c.get('upstream', 0))}"
                         for name, c in run["summary"]["by_category"].items())
        lines += ["", f"**Latest {suite} run** (`{run['commit']}`): {cats}"]
        for c in run["cases"]:
            if _is_upstream(c):
                lines.append(f"- UPSTREAM `{c['id']}` ({c['category']}): {c['reason']} "
                             f"[outage: {c['upstream']}, not scored]")
            elif not c["passed"]:
                lines.append(f"- FAIL `{c['id']}` ({c['category']}): {c['reason']}")
    return "\n".join(lines)


if __name__ == "__main__":
    print(report(load_history()))
