"""Analyst agent (MODEL_SMART): Facts JSON -> the 3 findings most worth knowing.

The model picks findings and cites fact keys; Python then drops findings that
cite keys not in the facts, looks up each finding's dollar amount from its
dollar_fact_key (so the ranking never relies on a number the model wrote),
and ranks by that amount.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from secondlook.agents.base import AgentContext, AgentSpec, call, facts_block, flatten, key_exists

SPEC = AgentSpec(name="analyst", model="smart", max_tokens=700)
MAX_FINDINGS = 3


class AnalystInput(BaseModel):
    facts: dict


class Finding(BaseModel):
    title: str = Field(max_length=120)
    why_it_matters: str = Field(max_length=400)
    fact_keys: list[str] = Field(min_length=1, max_length=6)
    dollar_fact_key: str | None = None


class AnalystOutput(BaseModel):
    findings: list[Finding] = Field(min_length=1, max_length=5)


class RankedFinding(Finding):
    dollar_impact_usd: float | None = None


def rank(findings: list[Finding], facts: dict, limit: int = MAX_FINDINGS) -> list[RankedFinding]:
    leaves = flatten(facts)
    ranked = []
    for f in findings:
        keys = [k for k in f.fact_keys if key_exists(facts, k)]
        if not keys:
            continue
        dollar_key = f.dollar_fact_key if f.dollar_fact_key in leaves else None
        value = leaves.get(dollar_key) if dollar_key else None
        impact = abs(float(value)) if isinstance(value, (int, float)) and not isinstance(value, bool) else None
        ranked.append(RankedFinding(**{**f.model_dump(), "fact_keys": keys, "dollar_fact_key": dollar_key},
                                    dollar_impact_usd=impact))
    ranked.sort(key=lambda f: -1 if f.dollar_impact_usd is None else f.dollar_impact_usd, reverse=True)
    return ranked[:limit]


def run(data: AnalystInput, ctx: AgentContext) -> list[RankedFinding]:
    output: AnalystOutput = call(SPEC, facts_block(data.facts), AnalystOutput, ctx)
    findings = rank(output.findings, data.facts)
    if not findings:
        raise ValueError("Analyst returned no findings tied to the facts.")
    return findings
