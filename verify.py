"""Check that every figure in an answer appears in a tool result before the user sees it final.

The prompt already says "every number comes from a tool result", but a rule
in a prompt is a request. This is enforcement: code pulls each money amount,
percentage, and scaled number ($416.2B, 26.9%, 14.6 billion shares) out of
the answer and looks for it among the numbers the tools actually returned
(and the user typed). Honest rounding passes: "$416.2B" is grounded by
"$416,161,000,000". A figure with no match is sent back to the agent once to
fix; if it still can't be matched, the panel shows it as unverified.

Bare numbers (years, "10-K", "Q2", "3 months", a P/E of 31.2) aren't checked:
without a $, %, or scale word there's no telling a figure from a label.
"""
import re

SCALES = {
    "k": 1e3, "thousand": 1e3, "m": 1e6, "mn": 1e6, "million": 1e6,
    "b": 1e9, "bn": 1e9, "billion": 1e9, "t": 1e12, "tn": 1e12, "trillion": 1e12,
}
# $1,234.5  1,234.5%  $416.2B  14.6 billion  -0.71%  14.8 percentage points
FIGURE = re.compile(
    r"(?P<dollar>\$)?\s?(?P<num>\d[\d,]*(?:\.\d+)?)[\s-]?"
    r"(?P<unit>%|percent(?:age[\s-]points?)?\b|(?:thousand|million|billion|trillion|mn|bn|tn)\b|[KMBT]\b)?",
    re.IGNORECASE,
)


def _figures(text: str):
    """(text as written, value, decimals, scale, checkable) for each number in `text`."""
    for m in FIGURE.finditer(text):
        num = m["num"].replace(",", "")
        if num.count(".") > 1:
            continue
        unit = (m["unit"] or "").lower()
        scale = SCALES.get(unit, 1.0)
        # A lone capital letter only counts as a scale right after a money
        # amount: "$416B" is billions, "3 M" in a sentence probably isn't.
        if len(unit) == 1 and unit in "kmbt" and not m["dollar"]:
            scale = 1.0
        decimals = len(num.split(".")[1]) if "." in num else 0
        checkable = bool(m["dollar"] or unit.startswith(("%", "percent")) or scale != 1.0)
        yield m.group().strip(), float(num), decimals, scale, checkable


def claims(answer: str) -> list[tuple[str, float, int, float]]:
    """The figures in an answer that must be grounded: (as written, value, decimals, scale)."""
    return [(t, v, d, s) for t, v, d, s, checkable in _figures(answer) if checkable]


def known_values(texts) -> list[float]:
    """Every number in the given texts, as absolute values, both raw and scaled."""
    values = []
    for text in texts:
        for _, value, _, scale, _ in _figures(text):
            values.append(value)
            if scale != 1.0:
                values.append(value * scale)
    return values


def is_grounded(value: float, decimals: int, scale: float, known: list[float]) -> bool:
    """True if some known number, in the answer's scale and precision, rounds to `value`."""
    tolerance = 0.5 * 10 ** -decimals + 1e-9
    return any(abs(k / scale - value) <= tolerance for k in known)


def unverified(answer: str, sources) -> list[str]:
    """Figures in `answer` that no number in `sources` supports, as written, in order."""
    known = known_values(sources)
    seen, missing = set(), []
    for text, value, decimals, scale in claims(answer):
        if text not in seen and not is_grounded(value, decimals, scale, known):
            missing.append(text)
        seen.add(text)
    return missing


if __name__ == "__main__":
    tool = "Revenue FY ended 2025-09-27: $416,161,000,000 ($416.16B); calculate: 6.4255"
    answer = "Apple's revenue was $416.2 billion, up 6.43% (and margins hit 31.9%) in its 10-K."
    print("claims:    ", [c[0] for c in claims(answer)])
    print("unverified:", unverified(answer, [tool]))
