"""Recording live answers and replaying them in demo mode, with a fake LLM."""

import importlib.util
import json
from pathlib import Path

import pytest

from agent_fixtures import reply
from conftest import FAKE_KEY, ODD_KEY, FakeClient, fake_completion
from secondlook import budget, demo_runs, llm
from secondlook.agents.base import AgentContext, system_prompt
from secondlook.config import Settings

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def load_script(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SYSTEM, MESSAGES = "Explain.", [{"role": "user", "content": "Volatility is 14.2%."}]
LIVE = Settings(portkey_api_key=ODD_KEY, demo_mode=False, model_fast="test-fast-model", model_smart="test-smart-model")


@pytest.fixture
def recording(tmp_path):
    llm.record_responses_to(tmp_path / "rec")
    yield tmp_path / "rec"
    llm.record_responses_to(None)


def test_demo_key_ignores_model():
    assert llm.demo_key(SYSTEM, MESSAGES) == llm.cache_key("", SYSTEM, MESSAGES)
    assert llm.cache_key("gpt-6-luna", SYSTEM, MESSAGES) != llm.cache_key("gpt-6-sol", SYSTEM, MESSAGES)


def test_live_answer_recorded_without_settings_or_key(recording, ledger):
    client = FakeClient(fake_completion(f"Answer mentioning {ODD_KEY}", 120, 30))
    llm.call_agent("explainer", "test-smart-model", SYSTEM, MESSAGES, 500, settings=LIVE, client=client, db_path=ledger)
    path = recording / "explainer" / f"{llm.demo_key(SYSTEM, MESSAGES)}.json"
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert set(saved) == {"agent", "text", "input_tokens", "output_tokens"}
    assert (saved["input_tokens"], saved["output_tokens"]) == (120, 30)
    raw = path.read_text(encoding="utf-8")
    assert ODD_KEY not in raw and "test-smart-model" not in raw and "[REDACTED]" in raw


def test_recording_is_off_by_default(tmp_path, ledger):
    assert llm.RECORD_DIR is None
    llm.call_agent("explainer", "test-smart-model", SYSTEM, MESSAGES, 500, settings=LIVE, client=FakeClient(), db_path=ledger)
    assert not list(tmp_path.rglob("explainer/*.json"))


def test_recorded_answer_replays_in_demo_with_no_model_or_key(recording, ledger, monkeypatch):
    llm.call_agent("analyst", "test-smart-model", SYSTEM, MESSAGES, 500, settings=LIVE,
                   client=FakeClient(fake_completion("recorded text")), db_path=ledger)
    llm.record_responses_to(None)
    llm.CACHE.clear()
    monkeypatch.setattr(llm, "DEMO_DIR", recording)
    demo = Settings(demo_mode=True)  # no key, no models
    resp = llm.call_agent("analyst", demo.model_smart, SYSTEM, MESSAGES, 1400, settings=demo, db_path=ledger)
    assert resp.demo and resp.text == "recorded text" and resp.cost_usd == 0


def test_demo_mode_budgets_as_free_demo_model(ledger):
    # 1,400 tokens at the unknown-model fallback price would exceed $0.05 per call; demo calls cost $0
    (llm.DEMO_DIR / "explainer").mkdir(parents=True)
    (llm.DEMO_DIR / "explainer" / f"{llm.demo_key(SYSTEM, MESSAGES)}.json").write_text(
        json.dumps({"text": "ok"}), encoding="utf-8")
    llm.call_agent("explainer", "", SYSTEM, MESSAGES, 1400, settings=Settings(demo_mode=True), db_path=ledger)
    assert budget.summary(ledger)["total_usd"] == 0
    assert budget.PRICES[budget.DEMO_MODEL] == budget.ModelPrice(0.0, 0.0)


# --- full pipeline: record with a fake LLM, then replay with no key ------------

NO_NUMBER_DRAFT = {
    "cards": [{"title": "Fees", "body": "Fund fees are part of the picture.", "fact_keys": ["fees.annual_fees_usd"]}],
    "advisor_questions": ["How do my fees compare with similar portfolios?", "What would you look at first?"],
}
ANALYST = {"findings": [{"title": "Fees", "why_it_matters": "Fees compound.", "fact_keys": ["fees.annual_fees_usd"],
                         "dollar_fact_key": "fees.annual_fees_usd"}]}
PAUSE = {"summary": "The check compares the portfolio before and after the change.", "points": ["Cash changes."]}


class RoutingClient(FakeClient):
    """Replies by agent, recognised from the system prompt it was sent."""

    def __init__(self):
        super().__init__()
        self.by_prompt = {system_prompt("analyst"): ANALYST, system_prompt("explainer"): NO_NUMBER_DRAFT,
                          system_prompt("verifier"): {"passed": True, "problems": []}, system_prompt("pause"): PAUSE}

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        return reply(self.by_prompt[kwargs["messages"][0]["content"]])


def test_persona_facts_are_deterministic():
    assert demo_runs.persona_facts("midcareer") == demo_runs.persona_facts("midcareer")


def test_record_then_check_demo_end_to_end(ledger, capsys):
    record_demo, check_demo = load_script("record_demo"), load_script("check_demo")
    client = RoutingClient()
    assert record_demo.main([], client=client, settings=LIVE, db_path=ledger) == 0
    out = capsys.readouterr().out
    assert "template" not in out.split("Recording")[1].split("runs,")[0]
    total = float(out.split("Total cost: $")[1].split()[0])
    assert total > 0 and total == pytest.approx(budget.summary(ledger)["total_usd"])

    files = list(llm.DEMO_DIR.rglob("*.json"))
    by_agent = {a: len(list((llm.DEMO_DIR / a).glob("*.json"))) for a in ("analyst", "explainer", "verifier", "pause")}
    # analyst once per persona (same facts for every fluency), explainer per persona x fluency,
    # verifier once per persona (same draft and facts), pause once per scenario
    assert by_agent == {"analyst": 3, "explainer": 9, "verifier": 3, "pause": 3}
    assert len(files) == 18
    for f in files:
        text = f.read_text(encoding="utf-8")
        assert ODD_KEY not in text and FAKE_KEY not in text and "test-smart-model" not in text

    calls_before = len(client.calls)
    assert check_demo.main([]) == 0
    assert "PASS: demo mode works with no key, for $0." in capsys.readouterr().out
    assert len(client.calls) == calls_before  # replay never touched the client


def test_record_demo_refuses_without_key(capsys):
    assert load_script("record_demo").main([], settings=Settings(demo_mode=True)) == 1
    assert "No PORTKEY_API_KEY" in capsys.readouterr().out


def test_check_demo_fails_without_recordings(capsys):
    assert load_script("check_demo").main([]) == 1
    assert "FAIL" in capsys.readouterr().out
