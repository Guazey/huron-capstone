"""FinanceBench's numeric questions, asked of the full agent and graded against the expected value.

FinanceBench (Patronus AI, https://arxiv.org/abs/2311.11944) is a public set
of questions about real 10-K filings. This uses its 50 "metrics-generated"
questions: each has one numeric answer that comes from statement line items,
sometimes after arithmetic ("FY2019 fixed asset turnover", "3-year average
capex as a % of revenue"). That exercises the whole pipeline: finding the
ticker, get_financials for the right fiscal years, calculate, and the verify
step. Numeric answers are graded by code, with no AI judge.

The questions are downloaded at run time from a pinned commit, not copied
into this repo: the FinanceBench repository has no license file.
"""
import json
import pathlib
import re

import requests

COMMIT = "cc39aeb4afdf33909ee1412188bf89035950c2eb"  # 2024-12-03
URL = (f"https://raw.githubusercontent.com/patronus-ai/financebench/{COMMIT}"
       "/data/financebench_open_source.jsonl")
CACHE = pathlib.Path(__file__).resolve().parent / ".cache" / f"financebench-{COMMIT[:8]}.jsonl"

# Questions about companies the agent can't look up: get_financials goes
# through the SEC's current ticker list, and these are no longer in it.
SKIP = {"Activision Blizzard": "acquired by Microsoft in 2023; no longer listed"}

INSTRUCTION = ("\n\nWork from the company's SEC-reported figures. End with one final line in the "
               "form 'Answer: <number>' in the units the question asks for.")
ANSWER_LINE = re.compile(r"Answer:\**\s*\**\s*([-−–]?)\s*\$?\s*([\d,]*\.?\d+)", re.IGNORECASE)
SCALES = (1, 100, 0.01, 1e3, 1e-3, 1e6, 1e-6, 1e9, 1e-9)


def load() -> list[dict]:
    if not CACHE.exists():
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        response = requests.get(URL, timeout=30)
        response.raise_for_status()
        CACHE.write_bytes(response.content)
    return [json.loads(line) for line in CACHE.read_text().splitlines() if line.strip()]


def parse_number(text: str) -> tuple[float, int] | None:
    """'$1577.00' -> (1577.0, 0); '-0.02' -> (-0.02, 2); '0.40' -> (0.4, 1); '65.4%' -> (65.4, 1).

    The second value is how many decimals are significant. Trailing zeros are
    formatting, not precision: FinanceBench writes $389M of dividends as
    "$0.40" billion, which is 0.389 rounded to one decimal.
    """
    m = re.search(r"([-−–]?)\s*\$?\s*([\d,]*\.?\d+)", text)
    if not m:
        return None
    digits = m[2].replace(",", "")
    decimals = len(digits.split(".")[1].rstrip("0")) if "." in digits else 0
    return (-1 if m[1] else 1) * float(digits), decimals


def cases() -> list[dict]:
    """The numeric questions as eval cases, minus those about delisted companies."""
    found = []
    for row in load():
        if row["question_type"] != "metrics-generated" or row["company"] in SKIP:
            continue
        expected = parse_number(row["answer"])
        if expected is None:
            continue
        found.append({
            "id": row["financebench_id"],
            "category": "financebench",
            "company": row["company"],
            "question": row["question"].strip() + INSTRUCTION,
            "expected": row["answer"],
        })
    return found


def grade(answer: str, expected: str) -> tuple[bool, str]:
    """Pass if the final 'Answer:' value equals the expected one at its precision.

    Units are forgiven (1,577 million vs 1.577 billion, 0.165 vs 16.5%): the
    question's units are often ambiguous and the value is what's tested.
    Precision is not: 24.27 against an expected 24.26 fails.
    """
    found = ANSWER_LINE.findall(answer)
    if not found:
        return False, "no 'Answer: <number>' line"
    sign, digits = found[-1]
    got = (-1 if sign else 1) * float(digits.replace(",", ""))
    parsed = parse_number(expected)
    assert parsed is not None
    want, decimals = parsed
    tolerance = 0.5 * 10 ** -decimals + 1e-9
    for scale in SCALES:
        if abs(got * scale - want) <= tolerance:
            return True, ""
    return False, f"answered {got:g}, expected {expected}"
