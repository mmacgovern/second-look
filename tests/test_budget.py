import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from secondlook import budget
from secondlook.budget import BudgetExceeded, check_before_call, record_after_call


def test_cost_uses_price_table():
    # TODO-fast-model: $1 / 1M input, $4 / 1M output
    assert budget.cost_usd("TODO-fast-model", 1_000_000, 0) == pytest.approx(1.00)
    assert budget.cost_usd("TODO-fast-model", 1000, 500) == pytest.approx(0.001 + 0.002)


def test_unknown_model_uses_conservative_fallback():
    assert budget.price_for("never-heard-of-it") == budget.FALLBACK_PRICE
    assert budget.cost_usd("never-heard-of-it", 1000, 1000) > budget.cost_usd("TODO-smart-model", 1000, 1000)


def test_allows_call_within_all_limits(live_settings, ledger):
    worst = check_before_call("s1", 1000, 500, "TODO-fast-model", settings=live_settings, db_path=ledger)
    assert worst == pytest.approx(0.003)


def test_blocks_over_per_call_token_limit(live_settings, ledger):
    with pytest.raises(BudgetExceeded) as err:
        check_before_call("s1", 10, 1501, "TODO-fast-model", settings=live_settings, db_path=ledger)
    assert err.value.limit == "per-call token"


def test_blocks_over_per_call_cost_limit(live_settings, ledger):
    # 1000 output tokens on the fallback price = $0.075 > $0.05
    with pytest.raises(BudgetExceeded) as err:
        check_before_call("s1", 10, 1000, "unknown-model", settings=live_settings, db_path=ledger)
    assert err.value.limit == "per-call cost"


def test_blocks_over_session_budget(live_settings, ledger):
    record_after_call("s1", "agent", "TODO-smart-model", 0, 4_900, 1.0, db_path=ledger)  # $0.098
    with pytest.raises(BudgetExceeded) as err:
        check_before_call("s1", 100, 500, "TODO-fast-model", settings=live_settings, db_path=ledger)
    assert err.value.limit == "session"
    # A different session is unaffected.
    check_before_call("s2", 100, 500, "TODO-fast-model", settings=live_settings, db_path=ledger)


def test_blocks_over_daily_budget(live_settings, ledger):
    for i in range(20):  # 20 sessions x $0.0999 = $1.998 today; next call's worst case is $0.0021
        record_after_call(f"other-{i}", "agent", "TODO-smart-model", 0, 4_995, 1.0, db_path=ledger)
    with pytest.raises(BudgetExceeded) as err:
        check_before_call("fresh", 100, 500, "TODO-fast-model", settings=live_settings, db_path=ledger)
    assert err.value.limit == "daily"


def test_yesterday_does_not_count_toward_daily(live_settings, ledger):
    record_after_call("old", "agent", "TODO-smart-model", 0, 99_000, 1.0, db_path=ledger)  # $1.98
    yesterday = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    with sqlite3.connect(ledger) as conn:
        conn.execute("UPDATE calls SET timestamp = ?", (yesterday,))
    conn.close()
    assert budget.spent_today(ledger) == 0
    check_before_call("fresh", 100, 500, "TODO-fast-model", settings=live_settings, db_path=ledger)


def test_record_uses_actual_usage_and_cache_hits_are_free(ledger):
    assert record_after_call("s1", "a", "TODO-fast-model", 1000, 500, 0.4, db_path=ledger) == pytest.approx(0.003)
    assert record_after_call("s1", "a", "TODO-fast-model", 1000, 500, 0.0, cache_hit=True, db_path=ledger) == 0
    assert record_after_call("s1", "a", "TODO-fast-model", 1000, 500, 0.0, billable=False, db_path=ledger) == 0
    with sqlite3.connect(ledger) as conn:
        rows = conn.execute("SELECT session_id, agent, model, input_tokens, output_tokens, cache_hit FROM calls").fetchall()
    conn.close()
    assert rows[0] == ("s1", "a", "TODO-fast-model", 1000, 500, 0)
    assert rows[1][-1] == 1


def test_summary_groups_by_agent_and_session(ledger):
    record_after_call("s1", "explainer", "TODO-fast-model", 1000, 500, 0.5, db_path=ledger)
    record_after_call("s1", "critic", "TODO-smart-model", 1000, 500, 1.5, db_path=ledger)
    record_after_call("s2", "explainer", "TODO-fast-model", 1000, 500, 0.5, cache_hit=True, db_path=ledger)
    out = budget.summary(ledger)
    agents = {row["name"]: row for row in out["by_agent"]}
    sessions = {row["name"]: row for row in out["by_session"]}
    assert agents["explainer"]["calls"] == 2 and agents["explainer"]["cache_hits"] == 1
    assert agents["critic"]["cost_usd"] == pytest.approx(0.015)
    assert sessions["s1"]["cost_usd"] == pytest.approx(0.018)
    assert sessions["s2"]["cost_usd"] == 0
    assert out["total_usd"] == pytest.approx(0.018) and out["today_usd"] == pytest.approx(0.018)
    assert budget.summary(ledger, session_id="s2")["total_usd"] == 0


def test_budget_exceeded_message_is_readable():
    msg = str(BudgetExceeded("session", 0.0123, 0.001))
    assert "session" in msg and "$0.0123" in msg
