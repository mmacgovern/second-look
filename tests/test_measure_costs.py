"""scripts/measure_costs.py with a fake LLM that reports fixed token usage per agent."""

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from conftest import ODD_KEY
from secondlook import budget, llm
from secondlook.agents.base import system_prompt
from secondlook.budget import BudgetExceeded
from secondlook.config import Settings

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("measure_costs", ROOT / "scripts" / "measure_costs.py")
mc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mc)

LIVE = Settings(portkey_api_key=ODD_KEY, demo_mode=False, model_fast="test-fast-model", model_smart="test-smart-model")
# (input tokens, cached input tokens, output tokens) the fake model reports for each agent
USAGE = {"analyst": (1000, 400, 100), "explainer": (2000, 0, 300), "verifier": (1500, 0, 50), "pause": (800, 0, 200)}
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
        self.calls.append((agent, kwargs["model"]))
        inp, cached, out = USAGE[agent]
        usage = SimpleNamespace(prompt_tokens=inp, completion_tokens=out,
                                prompt_tokens_details=SimpleNamespace(cached_tokens=cached))
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(REPLIES[agent])))],
                               usage=usage)


# smart = $5 / $20 per 1M, fast = $1 / $4 per 1M (test-only prices without a cached rate)
ANALYST = (1000 * 5 + 100 * 20) / 1e6       # $0.007
EXPLAINER = (2000 * 5 + 300 * 20) / 1e6     # $0.016
VERIFIER = (1500 * 1 + 50 * 4) / 1e6        # $0.0017
ANALYSIS = ANALYST + EXPLAINER + VERIFIER   # $0.0247
PAUSE = (800 * 5 + 200 * 20) / 1e6          # $0.008
SMART_VERIFIER = (1500 * 5 + 50 * 20) / 1e6  # $0.0085 when the verifier runs on the smart model


def run(tmp_path, ledger, *extra):
    client = UsageClient()
    code = mc.main(["--personas", "midcareer", "--fluencies", "new", "pro", "--repeats", "2", "--pauses", "1", *extra],
                   client=client, settings=LIVE, db_path=ledger, out_dir=tmp_path)
    report = json.loads((tmp_path / "cost_results.json").read_text(encoding="utf-8")) if code == 0 else None
    return code, report, client


def test_p95_nearest_rank():
    assert mc.p95(list(range(1, 21))) == 19
    assert mc.p95([5.0]) == 5.0 and mc.p95([]) == 0.0


def test_main_pass_by_hand(tmp_path, ledger):
    code, report, client = run(tmp_path, ledger, "--skip-cache-pass", "--skip-quality")
    assert code == 0 and report["stopped"] is None
    # caching off: 4 analyses x 3 calls + 1 pause, even though repeats are identical requests
    assert len(client.calls) == 13 and report["calls"] == 13
    assert report["total_usd"] == pytest.approx(4 * ANALYSIS + PAUSE)
    assert report["per_agent"]["analyst"]["mean_cached_input_tokens"] == 400
    assert report["per_agent"]["explainer"]["mean_cached_input_tokens"] == 0
    assert report["per_analysis"]["mean_usd"] == pytest.approx(ANALYSIS)
    assert report["per_analysis"]["mean_cached_input_tokens"] == 400


def test_response_cache_pass_and_monthly_by_hand(tmp_path, ledger):
    code, report, client = run(tmp_path, ledger, "--skip-quality")
    # cache pass over the same 4 analyses: the analyst and verifier requests are identical for both fluencies,
    # the explainer differs by fluency -> 1 analyst + 2 explainer + 1 verifier billed
    cached_total = ANALYST + 2 * EXPLAINER + VERIFIER
    assert report["response_cache"]["total_usd"] == pytest.approx(cached_total)
    assert report["response_cache"]["mean_usd_per_analysis"] == pytest.approx(cached_total / 4)
    without, with_cache = report["monthly"]["without_cache"], report["monthly"]["with_cache"]
    assert without["per_user_usd"] == pytest.approx(2 * ANALYSIS)                      # $0.0494
    assert [u["total_usd"] for u in without["users"]] == pytest.approx([4.94, 494.0])  # 100 and 10,000 users
    assert with_cache["per_user_usd"] == pytest.approx(2 * cached_total / 4, abs=1e-6)
    md = (tmp_path / "cost_results.md").read_text(encoding="utf-8")
    assert "Mean cached input tokens" in md and "| 100 users | 10,000 users |" in md


def test_quality_vs_cost_by_hand(tmp_path, ledger):
    code, _, client = run(tmp_path, ledger, "--skip-cache-pass", "--smart-model", "test-smart-model")
    q = json.loads((tmp_path / "quality_vs_cost.json").read_text(encoding="utf-8"))["routings"]
    normal, smart = q["Normal routing"], q["MODEL_SMART for every agent"]
    assert normal["analyses"] == smart["analyses"] == 2       # 1 persona x 2 fluency levels
    assert normal["first_draft_pass_rate"] == 1.0 and normal["rewrites"] == 0 and normal["template_fallbacks"] == 0
    assert normal["mean_usd_per_analysis"] == pytest.approx(ANALYSIS)
    assert smart["mean_usd_per_analysis"] == pytest.approx(ANALYST + EXPLAINER + SMART_VERIFIER)
    # in the all-smart runs the verifier really ran on the smart model
    assert ("verifier", "test-smart-model") in client.calls
    assert "First-draft verifier pass rate" in (tmp_path / "quality_vs_cost.md").read_text(encoding="utf-8")


def test_quality_skipped_when_smart_equals_fast(tmp_path, ledger):
    same = LIVE.model_copy(update={"model_smart": "test-fast-model"})
    mc.main(["--personas", "midcareer", "--fluencies", "new", "--repeats", "1", "--pauses", "0", "--skip-cache-pass"],
            client=UsageClient(), settings=same, db_path=ledger, out_dir=tmp_path)
    q = json.loads((tmp_path / "quality_vs_cost.json").read_text(encoding="utf-8"))
    assert list(q["routings"]) == ["Normal routing"] and "--smart-model" in q["note"]


def test_max_usd_enforced_through_budget(tmp_path, ledger):
    with pytest.raises(BudgetExceeded):
        budget.check_run_budget(0.15, 0.06, 0.20)
    budget.check_run_budget(0.10, 0.05, 0.20)
    # worst case of one analysis with test prices: 0.034 + 0.048 x 2 + 0.006 = $0.136
    assert mc.worst_case(LIVE, mc.ANALYSIS_STEPS) == pytest.approx(0.136)
    code, report, client = run(tmp_path, ledger, "--max-usd", "0.10", "--skip-cache-pass", "--skip-quality")
    assert client.calls == [] and report["analyses"] == 0 and "run limit" in report["stopped"]


def test_daily_budget_blocks_and_says_so(tmp_path, ledger, capsys):
    tight = LIVE.model_copy(update={"daily_budget_usd": 0.05})
    code = mc.main(["--personas", "midcareer", "--repeats", "1"], client=UsageClient(), settings=tight, db_path=ledger,
                   out_dir=tmp_path)
    out = capsys.readouterr().out
    assert code == 3 and "daily budget would block" in out and "does not change .env" in out
    assert not (tmp_path / "cost_results.json").exists()


def test_estimate_only_makes_no_calls(tmp_path, ledger, capsys):
    client = UsageClient()
    roomy = LIVE.model_copy(update={"daily_budget_usd": 100.0})  # test prices are deliberately high
    assert mc.main(["--estimate-only"], client=client, settings=roomy, db_path=ledger, out_dir=tmp_path) == 0
    assert client.calls == [] and "Worst-case cost $" in capsys.readouterr().out


def test_cache_and_trace_settings_are_restored(tmp_path, ledger):
    run(tmp_path, ledger, "--skip-quality")
    assert llm.CACHE_ENABLED is True and llm.TRACE_PREFIX is None and llm.FORCE_REFRESH is False


def test_needs_a_key(tmp_path, capsys):
    assert mc.main([], settings=Settings(demo_mode=True), out_dir=tmp_path) == 2
    assert "PORTKEY_API_KEY" in capsys.readouterr().out
