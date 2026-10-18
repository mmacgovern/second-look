"""scripts/measure_costs.py with a fake LLM that reports fixed token usage per agent."""

import importlib.util
import json
import math
from pathlib import Path
from types import SimpleNamespace

import pytest

from conftest import ODD_KEY
from secondlook import llm
from secondlook.agents.base import system_prompt
from secondlook.config import Settings

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("measure_costs", ROOT / "scripts" / "measure_costs.py")
mc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mc)

LIVE = Settings(portkey_api_key=ODD_KEY, demo_mode=False, model_fast="test-fast-model", model_smart="test-smart-model")
# (input tokens, output tokens) the fake model reports for each agent
USAGE = {"analyst": (1000, 100), "explainer": (2000, 300), "verifier": (1500, 50), "pause": (800, 200)}
REPLIES = {
    "analyst": {"findings": [{"title": "Fees", "why_it_matters": "Fees compound.", "fact_keys": ["fees.annual_fees_usd"],
                              "dollar_fact_key": "fees.annual_fees_usd"}]},
    "explainer": {"cards": [{"title": "Fees", "body": "Fund fees are part of the picture.", "fact_keys": ["fees.annual_fees_usd"]}],
                  "advisor_questions": ["How do my fees compare?", "What would you look at first?"]},
    "verifier": {"passed": True, "problems": []},
    "pause": {"summary": "The check compares the portfolio before and after.", "points": ["Cash changes."]},
}


class UsageClient:
    def __init__(self):
        self.calls = []
        self.by_prompt = {system_prompt(a): a for a in REPLIES}
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        agent = self.by_prompt[kwargs["messages"][0]["content"]]
        self.calls.append(agent)
        inp, out = USAGE[agent]
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(REPLIES[agent])))],
                               usage=SimpleNamespace(prompt_tokens=inp, completion_tokens=out))


# smart = $5 / $20 per 1M, fast = $1 / $4 per 1M (test-only prices from conftest)
ANALYST = (1000 * 5 + 100 * 20) / 1e6      # $0.007
EXPLAINER = (2000 * 5 + 300 * 20) / 1e6    # $0.016
VERIFIER = (1500 * 1 + 50 * 4) / 1e6       # $0.0017
ANALYSIS = ANALYST + EXPLAINER + VERIFIER  # $0.0247
PAUSE = (800 * 5 + 200 * 20) / 1e6         # $0.008


def run(tmp_path, ledger, *extra):
    client = UsageClient()
    code = mc.main(["--personas", "midcareer", "--fluencies", "new", "pro", "--repeats", "2", "--pauses", "1", *extra],
                   client=client, settings=LIVE, db_path=ledger, out_dir=tmp_path)
    report = json.loads((tmp_path / "cost_results.json").read_text(encoding="utf-8"))
    return code, report, client


def test_p95_nearest_rank():
    assert mc.p95(list(range(1, 21))) == 19        # ceil(0.95 x 20) = 19th value
    assert mc.p95([5.0]) == 5.0 and mc.p95([]) == 0.0
    assert mc.p95([1, 2, 3, 100]) == 100


def test_measurement_by_hand(tmp_path, ledger):
    code, report, client = run(tmp_path, ledger)
    assert code == 0 and report["stopped"] is None
    # caching off: 4 analyses x 3 calls + 1 pause, even though repeats are identical requests
    assert len(client.calls) == 13 and report["calls"] == 13
    assert (report["analyses"], report["pauses"]) == (4, 1)
    assert report["total_usd"] == pytest.approx(4 * ANALYSIS + PAUSE)
    agents = report["per_agent"]
    assert agents["explainer"]["mean_input_tokens"] == 2000 and agents["explainer"]["mean_output_tokens"] == 300
    assert agents["analyst"]["mean_usd"] == pytest.approx(ANALYST)
    assert report["per_analysis"]["mean_usd"] == pytest.approx(ANALYSIS)
    assert report["per_analysis"]["mean_input_tokens"] == 1000 + 2000 + 1500
    assert report["per_pause"]["mean_usd"] == pytest.approx(PAUSE)


def test_projection_by_hand(tmp_path, ledger):
    _, report, _ = run(tmp_path, ledger)
    by_users = {p["users"]: p for p in report["projection"]}
    # 1,000 users x 2 analyses x $0.0247 = $49.40
    assert by_users[1_000]["without_caching_usd"] == pytest.approx(49.40)
    assert by_users[50_000]["analyses"] == 100_000
    assert by_users[50_000]["without_caching_usd"] == pytest.approx(100_000 * ANALYSIS, abs=0.01)
    # with caching: each system prompt's tokens billed at 10% of the input price
    tokens = {a: math.ceil(len(system_prompt(a)) / 4) for a in ("analyst", "explainer", "verifier")}
    saving = (tokens["analyst"] * 5 + tokens["explainer"] * 5 + tokens["verifier"] * 1) * 0.9 / 1e6
    assert report["per_analysis"]["mean_usd_with_caching"] == pytest.approx(ANALYSIS - saving, abs=1e-7)
    assert by_users[1_000]["with_caching_usd"] == pytest.approx(2_000 * (ANALYSIS - saving), abs=0.01)


def test_outputs_markdown_table_and_portkey_check(tmp_path, ledger):
    _, report, _ = run(tmp_path, ledger)
    md = (tmp_path / "cost_results.md").read_text(encoding="utf-8")
    for text in ("| Unit | n | Mean input tokens", "**Full analysis**", "| 10,000 | 20,000 |", "With caching"):
        assert text in md
    check = report["portkey_check"]
    assert check["trace_id"].startswith(report["run_id"]) and check["trace_id"].endswith("-analyst")
    assert check["our_cost_usd"] == pytest.approx(ANALYST)


def test_cap_stops_before_overspending(tmp_path, ledger):
    # worst case of one analysis with test prices: analyst 0.034 + explainer 0.048 x 2 + verifier 0.006 = $0.136
    assert mc.worst_case(LIVE, ["analyst", "explainer", "verifier", "explainer"]) == pytest.approx(0.136)
    code, report, client = run(tmp_path, ledger, "--cap", "0.10")
    assert client.calls == [] and report["analyses"] == 0 and "stopped before" in report["stopped"]
    _, report, _ = run(tmp_path, ledger, "--cap", "0.20")
    # 0 + .136, .0247 + .136, .0494 + .136 fit under $0.20; .0741 + .136 does not
    assert report["analyses"] == 3 and report["total_usd"] <= 0.20


def test_cache_and_trace_settings_are_restored(tmp_path, ledger):
    run(tmp_path, ledger)
    assert llm.CACHE_ENABLED is True and llm.TRACE_PREFIX is None and llm.FORCE_REFRESH is False


def test_needs_a_key(tmp_path, capsys):
    assert mc.main([], settings=Settings(demo_mode=True), out_dir=tmp_path) == 2
    assert "PORTKEY_API_KEY" in capsys.readouterr().out
