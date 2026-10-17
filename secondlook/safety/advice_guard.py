"""Regex layer that flags investment advice, predictions and promises in AI-written text.

Second Look explains numbers; it never tells anyone to buy, sell or hold a
specific security, never sets price targets, and never promises returns.
This layer is deliberately strict: a false alarm only costs a rewrite or the
safe template, while a miss could put advice in front of a user.

Questions (sentences ending in "?") can be allowed to contain directive words,
because "Should I keep GFACX given its fees?" is exactly the kind of question
the user is encouraged to take to a fiduciary advisor.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

I = re.IGNORECASE

VERBS = r"(?:buy|sell|hold|keep|dump|unload|short|exit|cash out|get out|move|switch|rebalance|trim|add to|load up on)"


@dataclass(frozen=True)
class Rule:
    name: str
    pattern: re.Pattern
    question_ok: bool = False   # allowed inside a question when allow_questions=True
    negatable: bool = False     # skipped when negated: "not guaranteed", "isn't risk-free"


RULES: list[Rule] = [
    Rule("directive", re.compile(rf"\b(?:you|we|i)\s+(?:really\s+)?(?:should|must|need to|ought to|have to)\s+(?:not\s+)?{VERBS}\b", I), True),
    Rule("directive", re.compile(rf"\b(?:should|must|ought to)\s+(?:not\s+)?{VERBS}\b", I), True),
    Rule("directive", re.compile(r"\b(?:we|i)\s+(?:strongly\s+)?(?:recommend|suggest|advise|urge)\b", I), True),
    Rule("directive", re.compile(r"\brecommend(?:ed|s|ation)?\s+(?:that\s+)?(?:you|buying|selling|holding|keeping)\b", I), True),
    Rule("directive", re.compile(r"\b(?:don'?t|do not|never)\s+(?:sell|buy|panic[- ]sell|touch|bail)\b", I), True),
    Rule("directive", re.compile(r"\b(?:time to|now is the time to|it'?s time to|better to)\s+(?:buy|sell|get out|cash out|exit)\b", I), True),
    # Imperatives such as "Sell AAPL", "Buy more VOO", "Hold your shares" (case-sensitive verb, ticker or object).
    Rule("directive", re.compile(r"(?:^|[.!:;]\s+|\n)\s*(?:Buy|Sell|Hold|Dump|Short)\s+(?:more\s+|some\s+|all\s+)?(?:of\s+)?(?:[A-Z]{1,5}\b|your\b|the\b|it\b|them\b|now\b)"), True),
    Rule("rating", re.compile(r"\bstrong (?:buy|sell)\b|\b(?:buy|sell|hold) rating\b|\b(?:overweight|underweight|outperform) rating\b", I)),
    Rule("price_target", re.compile(r"\bprice target\b|\btarget price\b|\b(?:will|could|should|is going to) (?:hit|reach|trade at) \$?\d", I)),
    Rule("promise", re.compile(r"\bguarantee(?:d|s)?\b|\brisk[- ]free\b|\bcan(?:'|no)?t lose\b|\bsure thing\b|\bno[- ]brainer\b|\bsafe bet\b", I),
         negatable=True),
    Rule("prediction", re.compile(
        r"\b(?:will|is going to|are going to|is sure to|are sure to)\s+(?:definitely\s+)?"
        r"(?:rise|go up|double|triple|soar|recover|rebound|bounce back|outperform|beat the market|crash|collapse|fall|drop|go down)\b", I)),
    Rule("return_promise", re.compile(r"\b(?:will|you'?ll|you will|you are going to)\s+(?:earn|make|get|return|gain|grow)\s+(?:about\s+|at least\s+)?(?:\$|\d)", I)),
    Rule("judgment", re.compile(
        r"\b(?:good|bad|smart|wise|great|terrible|poor|foolish) (?:idea|move|decision|choice|call)\b"
        r"|\b(?:right|wrong) (?:move|call|decision|choice|thing to do)\b|\bmistake\b", I), True),
]


# A negation just before a match ("not a guaranteed outcome", "isn't risk-free", "no guarantees").
NEGATED = re.compile(r"\b(?:not|no|never|nor|without|isn'?t|aren'?t|wasn'?t|cannot be)\s+(?:a\s+|an\s+|any\s+|being\s+)?$", I)


@dataclass(frozen=True)
class GuardHit:
    rule: str
    excerpt: str

    def reason(self) -> str:
        return f"{self.rule.replace('_', ' ')}: \"{self.excerpt}\""


def _sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+|\n+", text) if s.strip()]


def check(text: str, allow_questions: bool = False) -> list[GuardHit]:
    """Every advice-like phrase found, with a short excerpt."""
    hits: list[GuardHit] = []
    for sentence in _sentences(text):
        is_question = sentence.rstrip().endswith("?")
        for rule in RULES:
            if allow_questions and is_question and rule.question_ok:
                continue
            match = next((m for m in rule.pattern.finditer(sentence)
                          if not (rule.negatable and NEGATED.search(sentence[: m.start()]))), None)
            if match:
                start, end = max(0, match.start() - 25), min(len(sentence), match.end() + 25)
                hits.append(GuardHit(rule.name, sentence[start:end].strip()))
    return hits


def is_safe(text: str, allow_questions: bool = False) -> bool:
    return not check(text, allow_questions)
