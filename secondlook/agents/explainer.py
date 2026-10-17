"""Explainer agent (MODEL_SMART): findings + Facts JSON -> cards for one fluency level."""

from __future__ import annotations

import json

from pydantic import BaseModel, Field

from secondlook.agents.analyst import RankedFinding
from secondlook.agents.base import AgentContext, AgentSpec, Fluency, call, facts_block, tagged

SPEC = AgentSpec(name="explainer", model="smart", max_tokens=1400)


class Card(BaseModel):
    title: str = Field(max_length=120)
    body: str = Field(max_length=1200)
    fact_keys: list[str] = []


class ExplainerInput(BaseModel):
    facts: dict
    findings: list[RankedFinding]
    fluency: Fluency = "comfortable"
    revision_notes: list[str] = []


class ExplainerOutput(BaseModel):
    cards: list[Card] = Field(min_length=1, max_length=4)
    advisor_questions: list[str] = Field(min_length=2, max_length=3)

    def card_text(self) -> str:
        return "\n".join(f"{c.title}. {c.body}" for c in self.cards)

    def questions_text(self) -> str:
        return "\n".join(self.advisor_questions)


def build_message(data: ExplainerInput) -> str:
    findings = json.dumps([f.model_dump() for f in data.findings], separators=(",", ":"))
    parts = [f"<fluency>{data.fluency}</fluency>", facts_block(data.facts), tagged("findings", findings)]
    if data.revision_notes:
        parts.append(tagged("revision_notes", "\n".join(f"- {n}" for n in data.revision_notes)))
    return "\n".join(parts)


def run(data: ExplainerInput, ctx: AgentContext) -> ExplainerOutput:
    return call(SPEC, build_message(data), ExplainerOutput, ctx)
