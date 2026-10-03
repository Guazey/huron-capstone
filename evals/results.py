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
MAX_ANSWER_CHARS = 600


def usage(messages) -> dict:
    """Tokens and tool calls across the AI messages of one turn.

    Drafts the verify step rejected are removed from state, so their tokens
    aren't counted: the totals slightly undercount turns with a rewrite.
    """
    tokens = {"input_tokens": 0, "output_tokens": 0}
    tools = []
    for m in messages:
        if m.type != "ai":
            continue
        for key in tokens:
            tokens[key] += (m.usage_metadata or {}).get(key, 0)
        tools += [c["name"] for c in m.tool_calls]
    return {**tokens, "tools": tools}


def case_result(case: dict, passed: bool, reason: str, answer: str, latency_ms: int,
                messages=(), number_check: dict | None = None) -> dict:
    check = number_check or {}
    return {
        "id": case.get("id") or case["question"][:60],
        "category": case["category"],
        "passed": passed,
        "reason": reason,
        "latency_ms": latency_ms,
        **usage(messages),
        "figures_checked": check.get("figures", 0),
        "figures_unverified": len(check.get("unverified", [])),
        "rewrites": check.get("rewrites", 0),
        "answer": answer[:MAX_ANSWER_CHARS],
    }


def cost_usd(input_tokens: int, output_tokens: int) -> float:
    return (input_tokens * PRICE_PER_MTOK["input"] + output_tokens * PRICE_PER_MTOK["output"]) / 1e6


def summarize(cases: list[dict]) -> dict:
    by_category: dict[str, dict] = {}
    for c in cases:
        cat = by_category.setdefault(c["category"], {"passed": 0, "total": 0})
        cat["total"] += 1
        cat["passed"] += c["passed"]
    latencies = sorted(c["latency_ms"] for c in cases) or [0]
    input_tokens = sum(c["input_tokens"] for c in cases)
    output_tokens = sum(c["output_tokens"] for c in cases)
    passed = sum(c["passed"] for c in cases)
    return {
        "passed": passed,
        "total": len(cases),
        "pass_rate": round(passed / len(cases), 4) if cases else 0.0,
        "by_category": dict(sorted(by_category.items())),
        "latency_ms_p50": round(statistics.median(latencies)),
        "latency_ms_p95": latencies[min(len(latencies) - 1, round(0.95 * (len(latencies) - 1)))],
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "est_cost_usd": round(cost_usd(input_tokens, output_tokens), 4),
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


def _pct(passed: int, total: int) -> str:
    return f"{passed}/{total} ({100 * passed / total:.0f}%)" if total else "-"


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
            f"{_pct(s['passed'], s['total'])} | {s['latency_ms_p50'] / 1000:.1f}s | ${s['est_cost_usd']:.2f} | "
            f"{s['rewrites']} | {s['figures_unverified']} |"
        )
    latest: dict[str, dict] = {}
    for run in runs:
        latest[run["suite"]] = run
    for suite, run in latest.items():
        cats = ", ".join(f"{name} {_pct(c['passed'], c['total'])}"
                         for name, c in run["summary"]["by_category"].items())
        lines += ["", f"**Latest {suite} run** (`{run['commit']}`): {cats}"]
        for c in run["cases"]:
            if not c["passed"]:
                lines.append(f"- FAIL `{c['id']}` ({c['category']}): {c['reason']}")
    return "\n".join(lines)


if __name__ == "__main__":
    print(report(load_history()))
