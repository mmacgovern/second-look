import pytest

from agent_fixtures import ADVICE_DRAFT, ANALYST_REPLY, BAD_NUMBER_DRAFT, FACTS, FAIL, GOOD_DRAFT, JUDGING_PAUSE, NEUTRAL_PAUSE, PASS, reply
from conftest import ODD_KEY, FakeClient, fake_completion
from secondlook import budget, orchestrator
from secondlook.agents.base import AgentContext
from secondlook.agents.template import render_pause_template, render_template
from secondlook.agents.verifier import python_checks
from secondlook.config import Settings


@pytest.fixture
def settings():
    return Settings(portkey_api_key=ODD_KEY, demo_mode=False, model_fast="test-fast-model", model_smart="test-smart-model")


@pytest.fixture
def run(settings, ledger):
    def go(*replies, fluency="comfortable", settings_override=None):
        ctx = AgentContext(session_id="s", settings=settings_override or settings, client=FakeClient(*replies), db_path=ledger)
        return orchestrator.run_analysis(FACTS, fluency, ctx), ctx
    return go


def models(ctx):
    return [c["model"] for c in ctx.client.calls]


def test_happy_path_three_steps(run, ledger):
    result, ctx = run(reply(ANALYST_REPLY), reply(GOOD_DRAFT), reply(PASS))
    assert result.source == "ai" and result.steps == ["analyst", "explainer", "verifier"]
    assert result.cards[0].title == "Hidden Apple" and [f.title for f in result.findings][0] == "Retirement gap"
    assert models(ctx) == ["test-smart-model", "test-smart-model", "test-fast-model"]
    assert {r["name"] for r in budget.summary(ledger)["by_agent"]} == {"analyst", "explainer", "verifier"}


def test_python_fail_then_rewrite_is_model_checked(run):
    result, ctx = run(reply(ANALYST_REPLY), reply(BAD_NUMBER_DRAFT), reply(GOOD_DRAFT), reply(PASS))
    assert result.source == "ai_rewrite"
    assert result.steps == ["analyst", "explainer", "explainer_rewrite", "verifier"]
    assert any("15%" in r for r in result.verifier_reasons)
    assert "15%" in ctx.client.calls[2]["messages"][1]["content"]  # reasons sent with the rewrite


def test_model_review_fail_then_rewrite_checked_in_python_only(run):
    result, ctx = run(reply(ANALYST_REPLY), reply(GOOD_DRAFT), reply(FAIL), reply(GOOD_DRAFT))
    assert result.source == "ai_rewrite" and result.llm_steps == 4
    assert result.steps == ["analyst", "explainer", "verifier", "explainer_rewrite"]
    assert len(ctx.client.calls) == 4
    assert FAIL["problems"][0] in ctx.client.calls[3]["messages"][1]["content"]


def test_second_failure_renders_template(run):
    result, ctx = run(reply(ANALYST_REPLY), reply(ADVICE_DRAFT), reply(BAD_NUMBER_DRAFT))
    assert result.source == "template" and result.llm_steps == 3
    assert result.cards == render_template(FACTS, "comfortable").cards
    assert "did not pass review" in result.note
    assert any("should sell" in r for r in result.verifier_reasons) and any("15%" in r for r in result.verifier_reasons)


def test_every_rewrite_reason_is_recorded_in_the_ledger(run, ledger):
    run(reply(ANALYST_REPLY), reply(BAD_NUMBER_DRAFT), reply(GOOD_DRAFT), reply(PASS))
    rows = budget.rewrites(ledger)
    assert [r["stage"] for r in rows] == ["rewrite"] and rows[0]["session_id"] == "s"
    assert rows[0]["fluency"] == "comfortable" and any("15%" in r for r in rows[0]["reasons"])


def test_template_fallback_records_both_rejections(run, ledger):
    run(reply(ANALYST_REPLY), reply(ADVICE_DRAFT), reply(BAD_NUMBER_DRAFT))
    rows = budget.rewrites(ledger)  # newest first
    assert [r["stage"] for r in rows] == ["template", "rewrite"]
    assert any("should sell" in r for r in rows[1]["reasons"]) and any("15%" in r for r in rows[0]["reasons"])


def test_passing_draft_records_no_rewrite(run, ledger):
    run(reply(ANALYST_REPLY), reply(GOOD_DRAFT), reply(PASS))
    assert budget.rewrites(ledger) == []


def test_never_more_than_four_llm_calls(run):
    result, ctx = run(reply(ANALYST_REPLY), reply(GOOD_DRAFT), reply(FAIL), reply(ADVICE_DRAFT), reply(PASS))
    assert len(ctx.client.calls) <= orchestrator.MAX_LLM_STEPS == 4
    assert result.source == "template"


def test_budget_block_falls_back_without_calling(run, settings):
    tight = settings.model_copy(update={"session_budget_usd": 0.0001})
    result, ctx = run(reply(ANALYST_REPLY), settings_override=tight)
    assert result.source == "template" and "spending limit" in result.note and ctx.client.calls == []


def test_ai_down_falls_back(run):
    result, ctx = run(ConnectionError("down"))
    assert result.source == "template" and "not available" in result.note
    assert len(ctx.client.calls) == 2  # analyst attempt + its single retry


def test_invalid_json_from_analyst_falls_back(run):
    result, _ = run(fake_completion("not json at all"))
    assert result.source == "template"


@pytest.mark.parametrize("fluency", ["new", "comfortable", "pro"])
def test_fluency_reaches_explainer_and_template(run, fluency):
    result, ctx = run(reply(ANALYST_REPLY), reply(GOOD_DRAFT), reply(PASS), fluency=fluency)
    assert f"<fluency>{fluency}</fluency>" in ctx.client.calls[1]["messages"][1]["content"]
    assert result.fluency == fluency
    template = render_template(FACTS, fluency)
    assert python_checks(FACTS, template).passed
    assert 2 <= len(template.advisor_questions) <= 3


def test_template_wording_differs_by_fluency():
    titles = {f: render_template(FACTS, f).cards[0].title for f in ("new", "comfortable", "pro")}
    assert len(set(titles.values())) == 3
    assert "Look-through" in titles["pro"]


def test_retired_template_uses_counted_balance():
    facts = {**FACTS, "retirement_plan": {"status": "retired", "target_nest_egg_usd": 794872,
                                          "counted_balance_usd": 577868, "scenarios": []}}
    card = render_template(facts, "new").cards[-1]
    assert "$577,868" in card.body and "$794,872" in card.body
    assert python_checks(facts, render_template(facts, "new")).passed


# --- pause ---------------------------------------------------------------------

def pause_ctx(settings, ledger, *replies):
    return AgentContext(session_id="p", settings=settings, client=FakeClient(*replies), db_path=ledger)


def test_run_pause_neutral(settings, ledger):
    out = orchestrator.run_pause(FACTS, pause_ctx(settings, ledger, reply(NEUTRAL_PAUSE)))
    assert out.source == "ai" and out.summary == NEUTRAL_PAUSE["summary"]


def test_run_pause_judgment_uses_template(settings, ledger):
    ctx = pause_ctx(settings, ledger, reply(JUDGING_PAUSE))
    out = orchestrator.run_pause(FACTS, ctx)
    assert out.source == "template" and any("mistake" in r for r in out.reasons)
    assert out.summary == render_pause_template(FACTS["pause_check"]).summary
    assert len(ctx.client.calls) == 1


def test_run_pause_ai_down(settings, ledger):
    out = orchestrator.run_pause(FACTS, pause_ctx(settings, ledger, ConnectionError("down")))
    assert out.source == "template" and out.note


def test_pause_template_is_neutral_and_uses_fact_numbers():
    from secondlook.agents.pause import check

    template = render_pause_template(FACTS["pause_check"])
    assert check(template, FACTS["pause_check"]) == []
    assert "not whether to do it" in template.summary


def test_run_intake(settings, ledger):
    rows = {"rows": [{"symbol": "VOO", "quantity": 2, "price": 500, "market_value": None}]}
    portfolio = orchestrator.run_intake("VOO 2 shares at $500", pause_ctx(settings, ledger, reply(rows)))
    assert portfolio.values_by_symbol() == {"VOO": 1000.0}


def test_demo_mode_without_recordings_uses_template(ledger):
    result = orchestrator.run_analysis(FACTS, "new", AgentContext(settings=Settings(demo_mode=True), db_path=ledger))
    assert result.source == "template"
