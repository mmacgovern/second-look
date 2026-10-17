"""The only module that calls Portkey.

call_agent() checks the budget before every API attempt, records actual usage
after every response, caches responses in memory, retries at most once, and in
demo mode replays recorded responses from demo/recorded/ instead of calling the
API. Failures surface as a generic LLMError; details go to the redacting logger.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from secondlook import budget
from secondlook.config import PROJECT_ROOT, Settings, get_logger, get_settings

DEMO_DIR = PROJECT_ROOT / "demo" / "recorded"
TIMEOUT_S = 30
MAX_ATTEMPTS = 2  # first try + one retry
GENERIC_ERROR = "The AI service could not complete this request. Please try again later."

CACHE: dict[str, "LLMResponse"] = {}

log = get_logger(__name__)


class LLMError(Exception):
    """A user-safe failure. The message never contains provider details."""


@dataclass(frozen=True)
class LLMResponse:
    agent: str
    model: str
    text: str
    parsed: BaseModel | None
    input_tokens: int
    output_tokens: int
    cost_usd: float
    latency_s: float
    cache_hit: bool = False
    demo: bool = False


def cache_key(model: str, system: str, messages: list[dict], response_schema: type[BaseModel] | None = None) -> str:
    payload = {
        "model": model,
        "system": system,
        "messages": messages,
        # A different schema means a different expected output shape.
        "schema": response_schema.model_json_schema() if response_schema else None,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def estimate_tokens(system: str, messages: list[dict]) -> int:
    """Rough upper estimate: ~4 characters per token plus per-message overhead."""
    chars = len(system) + sum(len(str(m.get("content", ""))) for m in messages)
    return math.ceil(chars / 4) + 4 * (len(messages) + 1)


def get_client(settings: Settings) -> Any:
    from portkey_ai import Portkey

    client = Portkey(
        api_key=settings.portkey_api_key.get_secret_value(),
        provider=settings.portkey_provider,
        request_timeout=TIMEOUT_S,
    )
    # The SDK retries internally; turn that off so call_agent's single retry is the only one.
    inner = getattr(client, "openai_client", None)
    if inner is not None and hasattr(inner, "max_retries"):
        inner.max_retries = 0
    return client


def _response_format(schema: type[BaseModel]) -> dict:
    return {
        "type": "json_schema",
        "json_schema": {"name": schema.__name__, "schema": schema.model_json_schema()},
    }


def _parse(text: str, schema: type[BaseModel] | None) -> BaseModel | None:
    if schema is None:
        return None
    stripped = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    return schema.model_validate_json(stripped)


def _load_recording(agent_name: str, key: str) -> dict | None:
    for path in (DEMO_DIR / agent_name / f"{key}.json", DEMO_DIR / f"{agent_name}.json"):
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    return None


def _usage(raw: Any) -> tuple[int, int]:
    usage = getattr(raw, "usage", None)
    return int(getattr(usage, "prompt_tokens", 0) or 0), int(getattr(usage, "completion_tokens", 0) or 0)


def _demo_response(agent_name, model, system, messages, max_tokens, response_schema, key, session_id, s, db_path):
    budget.check_before_call(session_id, estimate_tokens(system, messages), max_tokens, model, settings=s, db_path=db_path)
    recording = _load_recording(agent_name, key)
    if recording is None:
        log.warning("Demo mode: no recording for agent=%s key=%s in %s", agent_name, key, DEMO_DIR)
        raise LLMError("Demo mode has no recorded response for this step.")
    text = recording["text"] if "text" in recording else json.dumps(recording.get("parsed"))
    try:
        parsed = _parse(text, response_schema)
    except Exception:
        log.warning("Demo recording for agent=%s does not match its schema", agent_name, exc_info=True)
        raise LLMError(GENERIC_ERROR) from None
    in_tok, out_tok = int(recording.get("input_tokens", 0)), int(recording.get("output_tokens", 0))
    budget.record_after_call(session_id, agent_name, model or "demo", in_tok, out_tok, 0.0, billable=False, db_path=db_path)
    return LLMResponse(agent_name, model, text, parsed, in_tok, out_tok, 0.0, 0.0, demo=True)


def call_agent(
    agent_name: str,
    model: str,
    system: str,
    messages: list[dict],
    max_tokens: int,
    response_schema: type[BaseModel] | None = None,
    *,
    session_id: str = "default",
    settings: Settings | None = None,
    client: Any = None,
    db_path: Path | None = None,
) -> LLMResponse:
    """Run one agent call through the budget, cache, retry and demo machinery.

    Raises budget.BudgetExceeded if a limit would be broken, LLMError on any other failure.
    """
    s = settings or get_settings()
    key = cache_key(model, system, messages, response_schema)

    cached = CACHE.get(key)
    if cached is not None:
        budget.record_after_call(
            session_id, agent_name, model or "demo", cached.input_tokens, cached.output_tokens, 0.0,
            cache_hit=True, db_path=db_path,
        )
        return replace(cached, cost_usd=0.0, latency_s=0.0, cache_hit=True)

    if s.demo_mode:
        response = _demo_response(agent_name, model, system, messages, max_tokens, response_schema, key, session_id, s, db_path)
        CACHE[key] = response
        return response

    if not model:
        log.error("No model configured for agent=%s (set MODEL_FAST / MODEL_SMART)", agent_name)
        raise LLMError(GENERIC_ERROR)

    client = client or get_client(s)
    request = {
        "model": model,
        "messages": [{"role": "system", "content": system}, *messages],
        "max_tokens": max_tokens,
        "timeout": TIMEOUT_S,
    }
    if response_schema is not None:
        request["response_format"] = _response_format(response_schema)
    est_input = estimate_tokens(system, messages)

    for attempt in range(1, MAX_ATTEMPTS + 1):
        budget.check_before_call(session_id, est_input, max_tokens, model, settings=s, db_path=db_path)
        start = time.perf_counter()
        try:
            raw = client.chat.completions.create(**request)
        except Exception as exc:
            log.warning("agent=%s model=%s attempt %d/%d failed: %r", agent_name, model, attempt, MAX_ATTEMPTS, exc, exc_info=True)
            continue
        latency = time.perf_counter() - start
        in_tok, out_tok = _usage(raw)
        cost = budget.record_after_call(session_id, agent_name, model, in_tok, out_tok, latency, db_path=db_path)
        try:
            text = raw.choices[0].message.content or ""
            parsed = _parse(text, response_schema)
        except Exception as exc:
            log.warning("agent=%s model=%s attempt %d/%d returned an unusable response: %r", agent_name, model, attempt, MAX_ATTEMPTS, exc, exc_info=True)
            continue
        response = LLMResponse(agent_name, model, text, parsed, in_tok, out_tok, cost, latency)
        CACHE[key] = response
        return response

    raise LLMError(GENERIC_ERROR)
