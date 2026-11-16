"""Retrieval quality for the SEC filings search, with no model involved.

Each case is a question about one indexed company and a pattern that only
the passage answering it contains, taken from the filing's own words. A case
passes when a passage matching the pattern is among the passages
`knowledge.search` returns (the 5 the agent sees). It also records the rank,
so a change to chunking, filtering, or reranking shows up as hit rate and
MRR (mean reciprocal rank: 1.0 means the answer is always first).

Questions are phrased the way a user asks, not in the filing's wording, so a
plain keyword match can't pass them on its own.

    python -m evals.filings            # score and save to evals/history/
    python -m evals.filings --no-save  # just print
    python -m evals.filings --pace 21  # one case per 21s: the rerank quota is 3/min

A case whose reranking failed (usually throttling) still counts, in the knowledge
base's order; the summary says how many, so a throttled run isn't read as a
ranking change.

Needs KB_ID (infra/outputs.env) and AWS credentials. No Bedrock model calls.
"""
import argparse
import re
import time

CASES = [
    ("AAPL", "Where are Apple's products actually made?", r"outsourcing partners located primarily in China"),
    ("AAPL", "How does Apple make money from apps?", r"App Store"),
    ("AAPL", "Is Apple worried about tariffs?", r"tariff"),
    ("NVDA", "Who manufactures Nvidia's chips?", r"Taiwan Semiconductor|TSMC"),
    ("NVDA", "How dependent is Nvidia on a few big customers?", r"one direct customer represented \d+%"),
    ("NVDA", "Can Nvidia still sell to China?", r"export control"),
    ("TSLA", "What happens to Tesla if the CEO leaves?", r"highly dependent on the services of Elon Musk"),
    ("TSLA", "Does Tesla make money selling credits to other automakers?", r"regulatory credits"),
    ("TSLA", "Is Tesla working on humanoid robots?", r"Optimus"),
    ("MSFT", "What is Microsoft's relationship with OpenAI?", r"partnership with OpenAI"),
    ("MSFT", "Why does Microsoft think scale matters for its cloud?", r"economies of scale"),
    ("JPM", "How much capital does JPMorgan have to hold?", r"CET1 capital (regulatory )?ratio requirements?"),
    ("JPM", "How does JPMorgan handle cyber attacks?", r"cybersecurity"),
    ("META", "How much is Meta spending on AR and VR?", r"Reality Labs"),
    ("META", "Which European laws affect Meta?", r"Digital Markets Act|DMA"),
    ("META", "Is Meta spending more on data centers?", r"capital expenditures"),
    ("GOOGL", "Is Google being sued by the government?", r"Department of Justice|DOJ"),
    ("GOOGL", "What is Waymo doing?", r"Waymo"),
    ("GOOGL", "What businesses does Alphabet have besides Google?", r"Other Bets"),
    ("AMZN", "How does Amazon deliver orders?", r"fulfillment network"),
    ("AMZN", "Does Amazon face issues with labor unions abroad?", r"works councils and labor unions"),
    ("RKLB", "What's the status of Rocket Lab's big new rocket?", r"Neutron"),
    ("RKLB", "Does Rocket Lab have military satellite contracts?", r"Space Development Agency"),
    ("SPCX", "How much money did SpaceX raise in its IPO and what is it for?", r"net proceeds"),
]


def run(search=None, pace: float = 0) -> list[dict]:
    """Score every case with `search(query, ticker) -> passages` (knowledge.search by default).

    `pace` is the seconds between cases, to stay under the rerank quota.
    """
    from evals import results

    if search is None:
        import knowledge
        search = knowledge.search
    scored = []
    for n, (ticker, question, pattern) in enumerate(CASES):
        if pace and n:
            time.sleep(pace)
        case = {"id": f"{ticker}: {question}", "category": ticker, "question": question}
        started = time.monotonic()
        try:
            passages = search(question, ticker)
        except Exception as e:  # noqa: BLE001 - a crash is a failed case
            latency_ms = round((time.monotonic() - started) * 1000)
            scored.append({**results.case_result(case, False, f"error: {e}"[:300], "", latency_ms), "rank": None})
            continue
        latency_ms = round((time.monotonic() - started) * 1000)
        rank = next((i + 1 for i, p in enumerate(passages) if re.search(pattern, p["text"], re.I)), None)
        reason = "" if rank else f"no passage matched {pattern!r} in {len(passages)} returned"
        top = passages[0]["text"][:300] if passages else ""
        scored.append({**results.case_result(case, rank is not None, reason, top, latency_ms), "rank": rank,
                       "reranked": bool(passages) and all(p.get("reranked") for p in passages)})
    return scored


def mrr(cases: list[dict]) -> float:
    return round(sum(1 / c["rank"] for c in cases if c.get("rank")) / len(cases), 3) if cases else 0.0


def main() -> None:
    from evals import results

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--no-save", action="store_true")
    parser.add_argument("--pace", type=float, default=0, help="seconds between cases")
    args = parser.parse_args()
    cases = run(pace=args.pace)
    for c in cases:
        print(f"[{'PASS' if c['passed'] else 'FAIL'}] rank {c['rank'] or '-'}  {c['id']}")
    s = results.summarize(cases)
    print(f"\n{s['passed']}/{s['total']} found in the top 5; MRR {mrr(cases)}; p50 {s['latency_ms_p50']}ms")
    unranked = sum(1 for c in cases if not c.get("reranked"))
    if unranked:
        print(f"{unranked} case(s) not reranked (throttled, off, or no passages): their ranks are the knowledge base's")
    if not args.no_save:
        print("saved", results.save("filings", None, cases))


if __name__ == "__main__":
    main()
