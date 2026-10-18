"""Shifts agent (MODEL_SMART): words the Python-computed possible shifts for one fluency level.

The suggestions themselves come from secondlook/quant/shifts.py. The draft is checked in Python:
every number must be in the shifts facts, and advice_guard.check_shift rejects any named stock, fund
or ticker, price targets, predictions and promises. A failing draft is replaced by the Python wording.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from secondlook.agents.base import AgentContext, AgentSpec, Fluency, call, facts_block
from secondlook.safety import advice_guard
from secondlook.safety.numbers import unmatched_numbers

SPEC = AgentSpec(name="shifts", model="smart", max_tokens=900)


class ShiftsInput(BaseModel):
    shifts_facts: dict
    fluency: Fluency = "comfortable"


class ShiftItem(BaseModel):
    title: str = Field(max_length=90)
    body: str = Field(max_length=600)


class ShiftsOutput(BaseModel):
    summary: str = Field(max_length=400)
    items: list[ShiftItem] = Field(min_length=1, max_length=5)

    def text(self) -> str:
        return "\n".join([self.summary] + [f"{i.title}. {i.body}" for i in self.items])


def check(output: ShiftsOutput, shifts_facts: dict, banned_names: list[str]) -> list[str]:
    reasons = [f"The number {n} is not in the facts." for n in unmatched_numbers(output.text(), shifts_facts)]
    reasons += [f"Not allowed ({h.reason()})." for h in advice_guard.check_shift(output.text(), banned_names)]
    if len(output.items) != len(shifts_facts.get("shifts", [])):
        reasons.append("Write exactly one item per computed shift.")
    return reasons


def run(data: ShiftsInput, ctx: AgentContext) -> ShiftsOutput:
    return call(SPEC, f"<fluency>{data.fluency}</fluency>\n{facts_block(data.shifts_facts)}", ShiftsOutput, ctx)
