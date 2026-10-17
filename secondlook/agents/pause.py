"""Pause agent (MODEL_SMART): neutral explanation of a pause check.

The draft is checked in Python (numbers from the facts only, advice_guard with
no exceptions for questions) and replaced by a template if it fails.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from secondlook.agents.base import AgentContext, AgentSpec, call, facts_block
from secondlook.safety import advice_guard
from secondlook.safety.numbers import unmatched_numbers

SPEC = AgentSpec(name="pause", model="smart", max_tokens=700)


class PauseInput(BaseModel):
    pause_facts: dict   # the "pause_check" section of the Facts JSON


class PauseOutput(BaseModel):
    summary: str = Field(max_length=600)
    points: list[str] = Field(min_length=1, max_length=6)

    def text(self) -> str:
        return self.summary + "\n" + "\n".join(self.points)


def check(output: PauseOutput, pause_facts: dict) -> list[str]:
    reasons = [f"The number {n} is not in the facts." for n in unmatched_numbers(output.text(), pause_facts)]
    reasons += [f"Not neutral ({h.reason()})." for h in advice_guard.check(output.text())]
    return reasons


def run(data: PauseInput, ctx: AgentContext) -> PauseOutput:
    return call(SPEC, facts_block(data.pause_facts), PauseOutput, ctx)
