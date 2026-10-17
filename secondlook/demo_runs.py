"""The fixed set of runs that demo mode can replay: 3 sample personas x 3 fluency
levels, plus 3 Pause Button scenarios.

Facts are built from the offline snapshot (never live prices), so every prompt,
and therefore every demo key, is the same when recording and when replaying.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Literal

from pydantic import BaseModel

from secondlook import orchestrator
from secondlook.agents.base import AgentContext
from secondlook.quant.facts import build_facts, load_market_inputs
from secondlook.quant.models import Action
from secondlook.quant.profile import load_persona

PERSONAS = ("beginner", "midcareer", "retiree")
FLUENCIES = ("new", "comfortable", "pro")
PAUSE_SCENARIOS: list[tuple[str, Action]] = [
    ("midcareer", Action(kind="sell_all")),
    ("retiree", Action(kind="sell_pct", pct=50)),
    ("beginner", Action(kind="buy", ticker="QQQ", amount_usd=5000)),
]


class RunResult(BaseModel):
    label: str
    kind: Literal["analysis", "pause"]
    source: str            # "ai", "ai_rewrite" or "template"
    steps: list[str] = []
    note: str | None = None


def persona_facts(name: str, action: Action | None = None) -> dict:
    user, household, answers = load_persona(name)
    extra = [action.ticker] if action is not None and action.ticker else []
    profiles, adj = load_market_inputs(household.portfolio(), prefer_live=False, extra=extra)
    return build_facts(None, profiles, adj, action, household=household, user=user, risk_answers=answers)


def run_all(
    ctx: AgentContext,
    personas: tuple[str, ...] = PERSONAS,
    fluencies: tuple[str, ...] = FLUENCIES,
    pauses: list[tuple[str, Action]] = PAUSE_SCENARIOS,
    on_result=None,
) -> list[RunResult]:
    """Each run is its own session (like one user visit), so the per-session budget applies
    per run and the daily budget caps the whole set."""
    results = []

    def session(i: int) -> AgentContext:
        return replace(ctx, session_id=f"{ctx.session_id}-{i:02d}")

    for name in personas:
        facts = persona_facts(name)
        for fluency in fluencies:
            r = orchestrator.run_analysis(facts, fluency, session(len(results)))
            results.append(RunResult(label=f"{name} / {fluency}", kind="analysis", source=r.source, steps=r.steps, note=r.note))
            if on_result:
                on_result(results[-1])
    for name, action in pauses:
        r = orchestrator.run_pause(persona_facts(name, action), session(len(results)))
        results.append(RunResult(label=f"{name} / pause: {action.describe()}", kind="pause", source=r.source,
                                 steps=["pause"], note=r.note))
        if on_result:
            on_result(results[-1])
    return results
