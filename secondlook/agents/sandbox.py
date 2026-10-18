"""Sandbox agent (MODEL_SMART): explains the "Try a shift" replay of a pretend $1,000.

The replay itself is computed in secondlook/quant/sandbox.py. The draft is checked in Python:
every number must be in the sandbox facts, and advice_guard.check_shift rejects named stocks,
funds or tickers, price targets, predictions and promises. A failing draft is replaced by a
standard summary built from the numbers.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from secondlook.agents.base import AgentContext, AgentSpec, Fluency, call, facts_block
from secondlook.safety import advice_guard
from secondlook.safety.numbers import unmatched_numbers

SPEC = AgentSpec(name="sandbox", model="smart", max_tokens=900)


class SandboxInput(BaseModel):
    sandbox_facts: dict
    fluency: Fluency = "comfortable"


class SandboxOutput(BaseModel):
    summary: str = Field(max_length=500)
    points: list[str] = Field(min_length=2, max_length=5)

    def text(self) -> str:
        return self.summary + "\n" + "\n".join(self.points)


def check(output: SandboxOutput, sandbox_facts: dict, banned_names: list[str]) -> list[str]:
    reasons = [f"The number {n} is not in the facts." for n in unmatched_numbers(output.text(), sandbox_facts)]
    reasons += [f"Not allowed ({h.reason()})." for h in advice_guard.check_shift(output.text(), banned_names)]
    if "hindsight" not in output.text().lower():
        reasons.append("Say that the replay of the user's own portfolio has hindsight built in.")
    return reasons


def run(data: SandboxInput, ctx: AgentContext) -> SandboxOutput:
    return call(SPEC, f"<fluency>{data.fluency}</fluency>\n{facts_block(data.sandbox_facts)}", SandboxOutput, ctx)
