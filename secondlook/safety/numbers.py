"""Check that every number in AI-written text comes from the Facts JSON.

Each number in the text is matched against every number in the facts
(including numbers inside fact strings such as "55% SPY") within the
rounding the writer used: "13.3%" must be within 0.05, "13%" and "40%"
within 0.5, "$98,000" within $500, "$98k" within $500, "$1.2 million" within
$50,000. Trailing zeros count as rounding only from 1,000 up.
Signs are ignored ("a gap of $132,708" may describe -132708).

Not checked: small counts written as bare whole numbers 0-12 ("3 funds",
"10 years" is still checked when it has a unit-free value above 12), and bare
years 1900-2100. Names that contain numbers (401(k), S&P 500, ...) are removed
before checking.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

PROTECTED = re.compile(
    r"401\s*\(?k\)?|403\s*\(?b\)?|457\s*\(?b\)?|529 plans?|S&P\s*500|Nasdaq[- ]?100|Russell\s*[123]000|"
    r"Fortune\s*500|Dow\s*30|\b\d{1,2}(?:st|nd|rd|th)\b|\b[A-Z]{2,5}\d\b|Covid[- ]19",
    re.IGNORECASE,
)

NUMBER = re.compile(
    r"(?<![\w.,])(?P<dollar>\$)?(?P<int>\d{1,3}(?:,\d{3})+|\d+)(?P<dec>\.\d+)?"
    r"(?:\s*(?P<unit>%|percent\b|k\b|K\b|M\b|million\b|thousand\b|billion\b|bn\b))?",
)

SCALE = {"k": 1e3, "thousand": 1e3, "m": 1e6, "million": 1e6, "billion": 1e9, "bn": 1e9}


@dataclass(frozen=True)
class FoundNumber:
    text: str
    value: float
    tolerance: float
    kind: str  # "usd", "pct" or "plain"


def extract_numbers(text: str) -> list[FoundNumber]:
    cleaned = PROTECTED.sub(" ", text)
    found = []
    for m in NUMBER.finditer(cleaned):
        int_part = m.group("int").replace(",", "")
        decimals = m.group("dec") or ""
        unit = (m.group("unit") or "").lower()
        scale = SCALE.get(unit, 1.0)
        value = float(int_part + decimals) * scale
        if decimals:
            step = 10 ** -(len(decimals) - 1)
        elif float(int_part) * scale >= 1000:  # "$98,000" may be a rounded $98,155
            trailing = len(int_part) - len(int_part.rstrip("0")) if int_part.strip("0") else 0
            step = 10 ** trailing
        else:                                   # "40%" means 40, not "about 40 give or take 5"
            step = 1
        kind = "usd" if m.group("dollar") else "pct" if unit in ("%", "percent") else "plain"
        if kind == "plain" and not decimals and scale == 1.0:
            if value <= 12 or (1900 <= value <= 2100 and len(int_part) == 4):
                continue
        found.append(FoundNumber(m.group(0).strip(), value, 0.5 * step * scale, kind))
    return found


def fact_numbers(facts) -> list[float]:
    """Every number in the facts, including numbers written inside strings."""
    out: list[float] = []

    def walk(node):
        if isinstance(node, bool) or node is None:
            return
        if isinstance(node, (int, float)):
            out.append(abs(float(node)))
        elif isinstance(node, str):
            out.extend(abs(n.value) for n in extract_numbers(node))
        elif isinstance(node, dict):
            for v in node.values():
                walk(v)
        elif isinstance(node, (list, tuple)):
            for v in node:
                walk(v)

    walk(facts)
    return out


def unmatched_numbers(text: str, facts) -> list[str]:
    """Numbers in the text that do not match any fact within the writer's rounding."""
    known = fact_numbers(facts)
    return [
        n.text for n in extract_numbers(text)
        if not any(abs(n.value - f) <= n.tolerance + 1e-9 for f in known)
    ]
