"""Verifier: Python checks first, then MODEL_FAST for what regexes cannot judge.

Python checks (always run, no AI):
  - every number in the draft matches a Facts JSON number within rounding
  - advice_guard: buy/sell/hold directives, ratings, price targets,
    predictions and return promises (questions may name holdings)
  - every card cites fact keys that exist
The model review only runs when Python passes and use_llm is True (it costs
one LLM step).
"""

from __future__ import annotations

import json

from pydantic import BaseModel, Field

from secondlook.agents.base import AgentContext, AgentSpec, call, facts_block, key_exists, tagged
from secondlook.agents.explainer import ExplainerOutput
from secondlook.safety import advice_guard
from secondlook.safety.numbers import extract_numbers, fact_numbers, unmatched_numbers

SPEC = AgentSpec(name="verifier", model="fast", max_tokens=500)


class VerifierInput(BaseModel):
    facts: dict
    draft: ExplainerOutput


class VerifierVerdict(BaseModel):
    passed: bool
    problems: list[str] = Field(default_factory=list, max_length=8)


class VerificationResult(BaseModel):
    passed: bool
    reasons: list[str]
    unmatched_numbers: list[str] = []
    guard_hits: list[str] = []
    llm_checked: bool = False


PLAN_LABEL = "planning estimate"
# Stating an assumption: the word itself, or naming the assumed rate.
ASSUMPTION_WORDS = ("assum", "withdrawal rate", "real return", "after inflation", "after fees")


def plan_numbers(facts: dict) -> list[float]:
    """Dollar figures from the retirement plan (targets, projections, gaps), excluding small counts."""
    plan = facts.get("retirement_plan") or {}
    return [n for n in fact_numbers(plan) if n >= 1000]


def plan_problems(facts: dict, draft: ExplainerOutput) -> list[str]:
    """Every card that uses plan figures must show a range, name an assumption and say "planning estimate"."""
    known = plan_numbers(facts)
    problems = []
    for card in draft.cards:
        text = f"{card.title}. {card.body}"
        used = {f for n in extract_numbers(text) for f in known if abs(n.value - f) <= n.tolerance + 1e-9}
        cites_plan = any(k.startswith("retirement_plan") for k in card.fact_keys)
        if not used and not cites_plan:
            continue
        lower = text.lower()
        if PLAN_LABEL not in lower:
            problems.append(f'Card "{card.title}" uses plan figures without the label "planning estimate, not a guarantee".')
        if len(used) < 2:
            problems.append(f'Card "{card.title}" gives a single plan figure; show the range across scenarios.')
        if not any(word in lower for word in ASSUMPTION_WORDS):
            problems.append(f'Card "{card.title}" gives plan figures without stating the assumptions behind them.')
    return problems


def python_checks(facts: dict, draft: ExplainerOutput) -> VerificationResult:
    reasons: list[str] = []
    cards, questions = draft.card_text(), draft.questions_text()

    numbers = unmatched_numbers(cards + "\n" + questions, facts)
    for n in numbers:
        reasons.append(f"The number {n} is not in the facts. Use only numbers from the facts.")

    hits = advice_guard.check(cards) + advice_guard.check(questions, allow_questions=True)
    for hit in hits:
        reasons.append(f"Remove advice-like wording ({hit.reason()}).")

    for card in draft.cards:
        for key in card.fact_keys:
            if not key_exists(facts, key):
                reasons.append(f"Card \"{card.title}\" cites a fact key that does not exist: {key}.")

    reasons += plan_problems(facts, draft)

    return VerificationResult(passed=not reasons, reasons=reasons, unmatched_numbers=numbers,
                              guard_hits=[h.reason() for h in hits])


def run(data: VerifierInput, ctx: AgentContext, use_llm: bool = True) -> VerificationResult:
    result = python_checks(data.facts, data.draft)
    if not result.passed or not use_llm:
        return result
    draft = json.dumps(data.draft.model_dump(), separators=(",", ":"), ensure_ascii=False)
    verdict: VerifierVerdict = call(SPEC, facts_block(data.facts) + "\n" + tagged("draft", draft), VerifierVerdict, ctx)
    passed = verdict.passed and not verdict.problems
    reasons = [] if passed else (verdict.problems or ["The reviewer rejected the draft without a reason."])
    return VerificationResult(passed=passed, reasons=reasons, llm_checked=True)
