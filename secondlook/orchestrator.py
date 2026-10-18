"""Runs the agents for one analysis.

    analyst -> explainer -> verifier
      pass  -> show the AI cards
      fail  -> one rewrite with the verifier's reasons -> verify again
      fail  -> safe template rendered directly from the Facts JSON

At most MAX_LLM_STEPS (4) agent calls per analysis. The verifier's Python
checks always run; its model review runs only while steps remain. Any budget
limit or AI failure also falls back to the template, so the user always gets
an answer built only from computed numbers. Every call goes through
llm.call_agent (via agents.base.call).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ValidationError

from secondlook.agents import analyst, explainer, intake, pause, sandbox, shifts, verifier
from secondlook.agents.analyst import RankedFinding
from secondlook.agents.base import AgentContext, Fluency
from secondlook.agents.explainer import Card, ExplainerInput, ExplainerOutput
from secondlook.agents.template import render_pause_template, render_template
from secondlook import budget
from secondlook.budget import BudgetExceeded
from secondlook.config import get_logger
from secondlook.llm import LLMError
from secondlook.quant.models import Portfolio

MAX_LLM_STEPS = 4

log = get_logger(__name__)


class StepLimitReached(Exception):
    pass


class Steps:
    def __init__(self, limit: int = MAX_LLM_STEPS) -> None:
        self.limit = limit
        self.names: list[str] = []

    @property
    def remaining(self) -> int:
        return self.limit - len(self.names)

    def take(self, name: str) -> None:
        if self.remaining <= 0:
            raise StepLimitReached(name)
        self.names.append(name)


class AnalysisResult(BaseModel):
    cards: list[Card]
    advisor_questions: list[str]
    findings: list[RankedFinding] = []
    source: Literal["ai", "ai_rewrite", "template"]
    fluency: Fluency
    llm_steps: int
    steps: list[str]
    verifier_reasons: list[str] = []
    note: str | None = None


class PauseExplanation(BaseModel):
    summary: str
    points: list[str]
    source: Literal["ai", "template"]
    reasons: list[str] = []
    note: str | None = None


def _note_for(exc: Exception) -> str:
    if isinstance(exc, BudgetExceeded):
        return "The AI spending limit was reached, so this is the standard summary."
    return "The AI explanation was not available, so this is the standard summary."


def run_analysis(facts: dict, fluency: Fluency = "comfortable", ctx: AgentContext | None = None) -> AnalysisResult:
    ctx = ctx or AgentContext()
    steps = Steps()
    reasons: list[str] = []
    findings: list[RankedFinding] = []

    def finish(draft: ExplainerOutput, source: str, note: str | None = None) -> AnalysisResult:
        return AnalysisResult(cards=draft.cards, advisor_questions=draft.advisor_questions, findings=findings,
                              source=source, fluency=fluency, llm_steps=len(steps.names), steps=steps.names,
                              verifier_reasons=reasons, note=note)

    def verify(draft: ExplainerOutput):
        checked = verifier.python_checks(facts, draft)
        if checked.passed and steps.remaining > 0:
            steps.take("verifier")
            checked = verifier.run(verifier.VerifierInput(facts=facts, draft=draft), ctx, use_llm=True)
        return checked

    try:
        steps.take("analyst")
        findings = analyst.run(analyst.AnalystInput(facts=facts), ctx)

        steps.take("explainer")
        draft = explainer.run(ExplainerInput(facts=facts, findings=findings, fluency=fluency), ctx)
        result = verify(draft)
        if result.passed:
            return finish(draft, "ai")
        reasons.extend(result.reasons)
        budget.record_rewrite(ctx.session_id, "rewrite", result.reasons, fluency, db_path=ctx.db_path)

        steps.take("explainer_rewrite")
        rewrite = explainer.run(
            ExplainerInput(facts=facts, findings=findings, fluency=fluency, revision_notes=result.reasons), ctx)
        result = verify(rewrite)
        if result.passed:
            return finish(rewrite, "ai_rewrite")
        reasons.extend(result.reasons)
        budget.record_rewrite(ctx.session_id, "template", result.reasons, fluency, db_path=ctx.db_path)
        log.info("Draft failed review twice; using template. Reasons: %s", reasons)
        return finish(render_template(facts, fluency), "template", "The AI draft did not pass review, so this is the standard summary.")
    except (BudgetExceeded, LLMError, StepLimitReached, ValidationError, ValueError) as exc:
        log.warning("Analysis fell back to template after %s: %r", steps.names, exc)
        return finish(render_template(facts, fluency), "template", _note_for(exc))


def run_pause(facts: dict, ctx: AgentContext | None = None) -> PauseExplanation:
    """One AI call, checked in Python; the template is used if it fails or the AI is unavailable."""
    ctx = ctx or AgentContext()
    pause_facts = facts["pause_check"]
    try:
        output = pause.run(pause.PauseInput(pause_facts=pause_facts), ctx)
        problems = pause.check(output, pause_facts)
        if not problems:
            return PauseExplanation(summary=output.summary, points=output.points, source="ai")
        template = render_pause_template(pause_facts)
        return PauseExplanation(summary=template.summary, points=template.points, source="template", reasons=problems,
                                note="The AI explanation did not pass review, so this is the standard summary.")
    except (BudgetExceeded, LLMError, ValidationError, ValueError) as exc:
        log.warning("Pause explanation fell back to template: %r", exc)
        template = render_pause_template(pause_facts)
        return PauseExplanation(summary=template.summary, points=template.points, source="template", note=_note_for(exc))


class ShiftsExplanation(BaseModel):
    summary: str
    items: list[shifts.ShiftItem]
    source: Literal["ai", "template", "none"]
    reasons: list[str] = []
    note: str | None = None


def shifts_template(shifts_facts: dict, details: list[str]) -> ShiftsExplanation:
    """The Python wording of each computed shift, used when the AI draft is unavailable or fails review."""
    items = [shifts.ShiftItem(title=s["title"], body=d) for s, d in zip(shifts_facts["shifts"], details)]
    return ShiftsExplanation(summary="Based on your numbers, these shifts could bring your portfolio closer to the "
                                     "reference points shown.", items=items, source="template")


def run_shifts(shifts_facts: dict, details: list[str], fluency: Fluency = "comfortable",
               ctx: AgentContext | None = None, banned_names: list[str] | None = None) -> ShiftsExplanation:
    """One AI call to word the computed shifts, checked in Python; falls back to the Python wording.
    No call is made when no shift was computed."""
    ctx = ctx or AgentContext()
    if not shifts_facts.get("shifts"):
        return ShiftsExplanation(summary="No shifts stand out from your numbers.", items=[], source="none")
    try:
        output = shifts.run(shifts.ShiftsInput(shifts_facts=shifts_facts, fluency=fluency), ctx)
        problems = shifts.check(output, shifts_facts, banned_names or [])
        if not problems:
            return ShiftsExplanation(summary=output.summary, items=output.items, source="ai")
        budget.record_rewrite(ctx.session_id, "template", problems, fluency, db_path=ctx.db_path)
        return shifts_template(shifts_facts, details).model_copy(update={
            "reasons": problems, "note": "The AI wording did not pass review, so this is the standard wording."})
    except (BudgetExceeded, LLMError, ValidationError, ValueError) as exc:
        log.warning("Shifts explanation fell back to template: %r", exc)
        return shifts_template(shifts_facts, details).model_copy(update={"note": _note_for(exc)})


class SandboxExplanation(BaseModel):
    summary: str
    points: list[str]
    source: Literal["ai", "template"]
    reasons: list[str] = []
    note: str | None = None


def sandbox_template(sandbox_facts: dict) -> SandboxExplanation:
    """A standard summary built straight from the replay numbers."""
    rows = sandbox_facts["strategies"]
    points = [f"{r['name']}: ${r['value_today_usd']:,} today, {r['yearly_return_pct']}% a year, worst drop "
              f"{r['worst_drop_pct']}%." for r in rows]
    summary = (f"A pretend ${sandbox_facts['pretend_amount_usd']:,} replayed from {sandbox_facts['period']}: mixes with "
               "more stocks grew more and fell further in crashes. Your portfolio's replay has hindsight built in. "
               "Past results do not predict future results.")
    return SandboxExplanation(summary=summary, points=points, source="template")


def run_sandbox(sandbox_facts: dict, fluency: Fluency = "comfortable", ctx: AgentContext | None = None,
                banned_names: list[str] | None = None) -> SandboxExplanation:
    """One AI call to explain the sandbox replay, checked in Python; falls back to the standard summary."""
    ctx = ctx or AgentContext()
    try:
        output = sandbox.run(sandbox.SandboxInput(sandbox_facts=sandbox_facts, fluency=fluency), ctx)
        problems = sandbox.check(output, sandbox_facts, banned_names or [])
        if not problems:
            return SandboxExplanation(summary=output.summary, points=output.points, source="ai")
        budget.record_rewrite(ctx.session_id, "template", problems, fluency, db_path=ctx.db_path)
        return sandbox_template(sandbox_facts).model_copy(update={
            "reasons": problems, "note": "The AI explanation did not pass review, so this is the standard summary."})
    except (BudgetExceeded, LLMError, ValidationError, ValueError) as exc:
        log.warning("Sandbox explanation fell back to template: %r", exc)
        return sandbox_template(sandbox_facts).model_copy(update={"note": _note_for(exc)})


def run_intake(text: str, ctx: AgentContext | None = None) -> Portfolio:
    """Pasted text -> Portfolio. Raises ValueError (user-safe) if nothing valid is found."""
    return intake.run(intake.IntakeInput(text=text), ctx or AgentContext())
