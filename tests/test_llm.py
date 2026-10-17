import json
import logging
import traceback

import pytest
from pydantic import BaseModel

from conftest import FAKE_KEY, ODD_KEY, fake_completion
from secondlook import budget, llm
from secondlook.budget import BudgetExceeded, record_after_call
from secondlook.config import Settings
from secondlook.llm import GENERIC_ERROR, LLMError, call_agent

SYSTEM = "Explain the numbers you are given."
MESSAGES = [{"role": "user", "content": "Volatility is 14.2%."}]


def call(settings, client, ledger, **overrides):
    args = dict(
        agent_name="explainer", model="test-fast-model", system=SYSTEM, messages=MESSAGES,
        max_tokens=500, session_id="s1", settings=settings, client=client, db_path=ledger,
    )
    args.update(overrides)
    return call_agent(**args)


# --- budget limits block before the client is ever called -------------------

def test_blocks_call_over_per_call_token_limit(live_settings, fake_client_cls, ledger):
    client = fake_client_cls()
    with pytest.raises(BudgetExceeded) as err:
        call(live_settings, client, ledger, max_tokens=1501)
    assert err.value.limit == "per-call token" and client.calls == []


def test_blocks_call_over_per_call_cost_limit(live_settings, fake_client_cls, ledger):
    client = fake_client_cls()
    with pytest.raises(BudgetExceeded) as err:
        call(live_settings, client, ledger, model="unpriced-model", max_tokens=1000)
    assert err.value.limit == "per-call cost" and client.calls == []


def test_blocks_call_over_session_budget(live_settings, fake_client_cls, ledger):
    record_after_call("s1", "earlier", "test-smart-model", 0, 4_900, 1.0, db_path=ledger)  # $0.098 spent
    client = fake_client_cls()
    with pytest.raises(BudgetExceeded) as err:
        call(live_settings, client, ledger)
    assert err.value.limit == "session" and client.calls == []


def test_blocks_call_over_daily_budget(live_settings, fake_client_cls, ledger):
    for i in range(20):  # $1.998 spent today across other sessions
        record_after_call(f"other-{i}", "earlier", "test-smart-model", 0, 4_995, 1.0, db_path=ledger)
    client = fake_client_cls()
    with pytest.raises(BudgetExceeded) as err:
        call(live_settings, client, ledger, session_id="fresh")
    assert err.value.limit == "daily" and client.calls == []


# --- successful calls ----------------------------------------------------------

def test_call_sets_max_tokens_timeout_and_records_usage(live_settings, fake_client_cls, ledger):
    client = fake_client_cls(fake_completion("Volatility measures swings.", 120, 40))
    resp = call(live_settings, client, ledger)
    assert resp.text == "Volatility measures swings."
    sent = client.calls[0]
    assert sent["max_completion_tokens"] == 500 and sent["timeout"] == 30
    assert sent["messages"][0] == {"role": "system", "content": SYSTEM}
    assert (resp.input_tokens, resp.output_tokens) == (120, 40)
    assert resp.cost_usd == pytest.approx(budget.cost_usd("test-fast-model", 120, 40))
    assert budget.summary(ledger)["total_usd"] == pytest.approx(resp.cost_usd)


def test_cache_returns_without_second_call(live_settings, fake_client_cls, ledger):
    client = fake_client_cls()
    first = call(live_settings, client, ledger)
    second = call(live_settings, client, ledger)
    assert len(client.calls) == 1
    assert second.cache_hit and not first.cache_hit
    assert second.text == first.text and second.cost_usd == 0
    agents = budget.summary(ledger)["by_agent"]
    assert agents[0]["calls"] == 2 and agents[0]["cache_hits"] == 1


def test_cache_key_changes_with_inputs():
    base = llm.cache_key("m", "sys", MESSAGES)
    assert base == llm.cache_key("m", "sys", [dict(MESSAGES[0])])
    assert base != llm.cache_key("m2", "sys", MESSAGES)
    assert base != llm.cache_key("m", "sys2", MESSAGES)
    assert base != llm.cache_key("m", "sys", [{"role": "user", "content": "other"}])


class Explanation(BaseModel):
    summary: str
    risk_level: str


def test_response_schema_is_requested_and_parsed(live_settings, fake_client_cls, ledger):
    client = fake_client_cls(fake_completion('```json\n{"summary": "ok", "risk_level": "moderate"}\n```'))
    resp = call(live_settings, client, ledger, response_schema=Explanation)
    assert resp.parsed == Explanation(summary="ok", risk_level="moderate")
    assert client.calls[0]["response_format"]["json_schema"]["name"] == "Explanation"


# --- retries and failures ----------------------------------------------------

def test_retries_once_then_succeeds(live_settings, fake_client_cls, ledger):
    client = fake_client_cls(TimeoutError("slow"), fake_completion("second try"))
    assert call(live_settings, client, ledger).text == "second try"
    assert len(client.calls) == 2


def test_gives_up_after_one_retry_with_generic_error(live_settings, fake_client_cls, ledger):
    client = fake_client_cls(ConnectionError("down"))
    with pytest.raises(LLMError) as err:
        call(live_settings, client, ledger)
    assert len(client.calls) == 2
    assert str(err.value) == GENERIC_ERROR


def test_invalid_schema_output_is_retried(live_settings, fake_client_cls, ledger):
    good = fake_completion('{"summary": "ok", "risk_level": "low"}')
    client = fake_client_cls(fake_completion("not json"), good)
    assert call(live_settings, client, ledger, response_schema=Explanation).parsed.risk_level == "low"
    assert len(client.calls) == 2
    assert budget.summary(ledger)["by_agent"][0]["calls"] == 2  # both attempts were billed


def test_missing_model_in_live_mode_fails_generically(live_settings, fake_client_cls, ledger):
    with pytest.raises(LLMError):
        call(live_settings, fake_client_cls(), ledger, model="")


# --- the key never leaks -----------------------------------------------------

@pytest.mark.parametrize("key", [FAKE_KEY, ODD_KEY])
def test_key_never_in_logs_or_exception_text(key, fake_client_cls, ledger, caplog):
    settings = Settings(portkey_api_key=key, demo_mode=False, model_fast="test-fast-model")
    client = fake_client_cls(PermissionError(f"401 invalid api key {key} for request"))
    with caplog.at_level(logging.DEBUG, logger="secondlook"):
        with pytest.raises(LLMError) as err:
            call(settings, client, ledger)
    exc_text = str(err.value) + repr(err.value) + "".join(traceback.format_exception(err.value))
    assert key not in exc_text
    assert key not in caplog.text
    assert "[REDACTED]" in caplog.text  # the details were logged, just redacted
    assert err.value.__cause__ is None and err.value.__context__ is None


# --- demo mode ---------------------------------------------------------------

def test_demo_mode_replays_recording_without_client(fake_client_cls, ledger, tmp_path):
    recorded = llm.DEMO_DIR
    recorded.mkdir(parents=True)
    (recorded / "explainer.json").write_text(
        json.dumps({"text": "Recorded explanation.", "input_tokens": 80, "output_tokens": 20}), encoding="utf-8"
    )
    settings = Settings(demo_mode=True)
    client = fake_client_cls()
    resp = call(settings, client, ledger)
    assert resp.demo and resp.text == "Recorded explanation." and resp.cost_usd == 0
    assert client.calls == []
    assert budget.summary(ledger)["total_usd"] == 0


def test_demo_mode_prefers_exact_recording_by_hash(ledger):
    key = llm.demo_key(SYSTEM, MESSAGES)
    (llm.DEMO_DIR / "explainer").mkdir(parents=True)
    (llm.DEMO_DIR / "explainer" / f"{key}.json").write_text(json.dumps({"text": "exact"}), encoding="utf-8")
    (llm.DEMO_DIR / "explainer.json").write_text(json.dumps({"text": "fallback"}), encoding="utf-8")
    assert call(Settings(demo_mode=True), None, ledger).text == "exact"


def test_demo_mode_without_recording_raises(ledger):
    with pytest.raises(LLMError):
        call(Settings(demo_mode=True), None, ledger, agent_name="nobody")


def test_demo_mode_still_enforces_budget(ledger):
    with pytest.raises(BudgetExceeded):
        call(Settings(demo_mode=True), None, ledger, max_tokens=5000)


def test_get_client_disables_sdk_retries_and_sets_timeout():
    client = llm.get_client(Settings(portkey_api_key=ODD_KEY, demo_mode=False))
    assert client.openai_client.max_retries == 0
    assert client.request_timeout == 30_000  # Portkey takes milliseconds: 30 s, not 30 ms
