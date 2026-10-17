"""Shared plumbing for agents: specs, prompt loading, and the single path to llm.call_agent."""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel

from secondlook import llm
from secondlook.config import Settings, get_settings

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"

Fluency = Literal["new", "comfortable", "pro"]


class AgentSpec(BaseModel):
    name: str
    model: Literal["fast", "smart"]   # MODEL_FAST or MODEL_SMART from settings
    max_tokens: int


@dataclass
class AgentContext:
    """Per-analysis call context passed through to llm.call_agent (tests inject a fake client)."""

    session_id: str = "default"
    settings: Settings | None = None
    client: Any = None
    db_path: Path | None = None

    def resolved_settings(self) -> Settings:
        return self.settings or get_settings()


@lru_cache(maxsize=None)
def system_prompt(name: str) -> str:
    return (PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8")


def model_for(spec: AgentSpec, settings: Settings) -> str:
    return settings.model_fast if spec.model == "fast" else settings.model_smart


def tagged(tag: str, content: str) -> str:
    """Wrap data in <tag>...</tag>, neutralising any closing tag inside the data."""
    safe = content.replace(f"</{tag}", f"&lt;/{tag}").replace(f"<{tag}", f"&lt;{tag}")
    return f"<{tag}>\n{safe}\n</{tag}>"


def facts_block(facts: dict) -> str:
    return tagged("facts", json.dumps(facts, separators=(",", ":"), ensure_ascii=False))


def call(spec: AgentSpec, user_content: str, output: type[BaseModel], ctx: AgentContext) -> BaseModel:
    settings = ctx.resolved_settings()
    response = llm.call_agent(
        spec.name,
        model_for(spec, settings),
        system_prompt(spec.name),
        [{"role": "user", "content": user_content}],
        spec.max_tokens,
        output,
        session_id=ctx.session_id,
        settings=settings,
        client=ctx.client,
        db_path=ctx.db_path,
    )
    return response.parsed


def flatten(facts: Any, prefix: str = "") -> dict[str, Any]:
    """Fact key paths -> leaf values, e.g. "look_through.top_companies[0].pct" -> 13.3."""
    out: dict[str, Any] = {}
    if isinstance(facts, dict):
        for k, v in facts.items():
            out.update(flatten(v, f"{prefix}.{k}" if prefix else k))
    elif isinstance(facts, list):
        for i, v in enumerate(facts):
            out.update(flatten(v, f"{prefix}[{i}]"))
    else:
        out[prefix] = facts
    return out


def key_exists(facts: dict, key: str) -> bool:
    """True if `key` is a leaf path or a parent of one."""
    paths = flatten(facts)
    return key in paths or any(p.startswith(key + ".") or p.startswith(key + "[") for p in paths)
