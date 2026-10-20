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
RECORD_DIR: Path | None = None  # set by record_responses_to() while recording demo responses
CACHE_ENABLED = True            # scripts/measure_costs.py turns this off to measure every call
TRACE_PREFIX: str | None = None # when set, live requests carry a Portkey trace id "<prefix>-<agent>"
FORCE_REFRESH = False          # skip the Portkey gateway cache (scripts/measure_costs.py)

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
    cached_input_tokens: int = 0


def cache_key(model: str, system: str, messages: list[dict], response_schema: type[BaseModel] | None = None) -> str:
    payload = {
        "model": model,
        "system": system,
        "messages": messages,
        # A different schema means a different expected output shape.
        "schema": response_schema.model_json_schema() if response_schema else None,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def demo_key(system: str, messages: list[dict], response_schema: type[BaseModel] | None = None) -> str:
    """Key for demo/recorded/<agent>/<demo_file_name(key)>. Model-independent, so a recording made
    with any model replays in demo mode even when no model is configured."""
    return cache_key("", system, messages, response_schema)


DEMO_FILE_CHARS = 20  # file names use the first 20 hex characters of the key: short paths for Windows, no clashes


def demo_file_name(key: str) -> str:
    """demo/recorded/<agent>/<this>.json: a short form of the demo key, so a clone in a deep folder stays
    under the Windows 260-character path limit."""
    return f"{key[:DEMO_FILE_CHARS]}.json"


def record_responses_to(path: Path | None) -> None:
    """Turn recording of live responses on (a directory) or off (None). Used by scripts/record_demo.py."""
    global RECORD_DIR
    RECORD_DIR = Path(path) if path is not None else None


def _save_recording(agent_name: str, key: str, response: "LLMResponse") -> None:
    """Save only the model's answer and token counts. Nothing from settings or .env."""
    from secondlook.config import redact

    path = RECORD_DIR / agent_name / demo_file_name(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {  # no model name either: it may come from .env
        "agent": agent_name,
        "text": redact(response.text),
        "input_tokens": response.input_tokens,
        "output_tokens": response.output_tokens,
    }
    path.write_text(json.dumps(record, indent=1, ensure_ascii=False), encoding="utf-8")


def estimate_tokens(system: str, messages: list[dict]) -> int:
    """Rough upper estimate: ~4 characters per token plus per-message overhead."""
    chars = len(system) + sum(len(str(m.get("content", ""))) for m in messages)
    return math.ceil(chars / 4) + 4 * (len(messages) + 1)


def get_client(settings: Settings, trace_id: str | None = None, force_refresh: bool = False) -> Any:
    from portkey_ai import Portkey

    client = Portkey(
        api_key=settings.portkey_api_key.get_secret_value(),
        provider=settings.portkey_provider,
        request_timeout=TIMEOUT_S * 1000,  # Portkey's gateway timeout is in milliseconds
        trace_id=trace_id,                 # lets a request be found in Portkey's logs
        cache_force_refresh=force_refresh or None,  # True: bypass the gateway cache for a real model call
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
    # the short name, then the full-length name older recordings used, then a single per-agent file
    for path in (DEMO_DIR / agent_name / demo_file_name(key), DEMO_DIR / agent_name / f"{key}.json",
                 DEMO_DIR / f"{agent_name}.json"):
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    return None


def gateway_cache_status(raw: Any) -> str | None:
    """Portkey's "cache-status" response header: MISS, HIT, REFRESH, or None if not reported."""
    try:
        headers = raw.get_headers() if hasattr(raw, "get_headers") else None
        return (dict(headers or {}).get("cache-status") or None)
    except Exception:
        return None


def _usage(raw: Any) -> tuple[int, int]:
    usage = getattr(raw, "usage", None)
    return int(getattr(usage, "prompt_tokens", 0) or 0), int(getattr(usage, "completion_tokens", 0) or 0)


def cached_input_tokens(raw: Any) -> int:
    """Cached prompt tokens the provider reports (usage.prompt_tokens_details.cached_tokens); 0 if not reported."""
    usage = getattr(raw, "usage", None)
    details = getattr(usage, "prompt_tokens_details", None)
    if isinstance(details, dict):
        value = details.get("cached_tokens")
    else:
        value = getattr(details, "cached_tokens", None)
    if value is None:
        value = getattr(usage, "cache_read_input_tokens", None)
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _demo_response(agent_name, model, system, messages, max_tokens, response_schema, key, session_id, s, db_path):
    # Demo replays are free: budget them as the $0 demo model (the per-call token limit still applies).
    budget.check_before_call(session_id, estimate_tokens(system, messages), max_tokens, budget.DEMO_MODEL,
                             settings=s, db_path=db_path)
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
    budget.record_after_call(session_id, agent_name, budget.DEMO_MODEL, in_tok, out_tok, 0.0, billable=False, db_path=db_path)
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

    cached = CACHE.get(key) if CACHE_ENABLED else None
    if cached is not None:
        budget.record_after_call(
            session_id, agent_name, model or "demo", cached.input_tokens, cached.output_tokens, 0.0,
            cache_hit=True, db_path=db_path,
        )
        return replace(cached, cost_usd=0.0, latency_s=0.0, cache_hit=True)

    if s.demo_mode:
        response = _demo_response(agent_name, model, system, messages, max_tokens, response_schema,
                                  demo_key(system, messages, response_schema), session_id, s, db_path)
        CACHE[key] = response
        return response

    if not model:
        log.error("No model configured for agent=%s (set MODEL_FAST / MODEL_SMART)", agent_name)
        raise LLMError(GENERIC_ERROR)

    client = client or get_client(s, trace_id=f"{TRACE_PREFIX}-{agent_name}" if TRACE_PREFIX else None,
                                  force_refresh=FORCE_REFRESH)
    request = {
        "model": model,
        "messages": [{"role": "system", "content": system}, *messages],
        # gpt-6 models reject "max_tokens"; this cap also covers any reasoning tokens.
        "max_completion_tokens": max_tokens,
        "timeout": TIMEOUT_S,
    }
    if response_schema is not None:
        request["response_format"] = _response_format(response_schema)
    if s.reasoning_effort:
        request["reasoning_effort"] = s.reasoning_effort
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
        cached_tok = cached_input_tokens(raw)
        # A Portkey gateway cache hit never reaches the model, so it is recorded as a free cache hit.
        gateway_hit = gateway_cache_status(raw) == "HIT"
        cost = budget.record_after_call(session_id, agent_name, model, in_tok, out_tok, latency,
                                        cache_hit=gateway_hit, cached_input_tokens=cached_tok, db_path=db_path)
        try:
            text = raw.choices[0].message.content or ""
            parsed = _parse(text, response_schema)
        except Exception as exc:
            log.warning("agent=%s model=%s attempt %d/%d returned an unusable response: %r", agent_name, model, attempt, MAX_ATTEMPTS, exc, exc_info=True)
            continue
        response = LLMResponse(agent_name, model, text, parsed, in_tok, out_tok, cost, latency, cache_hit=gateway_hit,
                               cached_input_tokens=cached_tok)
        CACHE[key] = response
        if RECORD_DIR is not None:
            _save_recording(agent_name, demo_key(system, messages, response_schema), response)
        return response

    raise LLMError(GENERIC_ERROR)
