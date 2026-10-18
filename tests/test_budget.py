import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from secondlook import budget
from secondlook.budget import BudgetExceeded, check_before_call, record_after_call


def test_cost_uses_price_table():
    # test-fast-model: $1 / 1M input, $4 / 1M output
    assert budget.cost_usd("test-fast-model", 1_000_000, 0) == pytest.approx(1.00)
    assert budget.cost_usd("test-fast-model", 1000, 500) == pytest.approx(0.001 + 0.002)


def test_unknown_model_uses_conservative_fallback():
    assert budget.price_for("never-heard-of-it") == budget.FALLBACK_PRICE
    assert budget.cost_usd("never-heard-of-it", 1000, 1000) > budget.cost_usd("test-smart-model", 1000, 1000)


def test_allows_call_within_all_limits(live_settings, ledger):
    worst = check_before_call("s1", 1000, 500, "test-fast-model", settings=live_settings, db_path=ledger)
    assert worst == pytest.approx(0.003)


def test_blocks_over_per_call_token_limit(live_settings, ledger):
    with pytest.raises(BudgetExceeded) as err:
        check_before_call("s1", 10, 1501, "test-fast-model", settings=live_settings, db_path=ledger)
    assert err.value.limit == "per-call token"


def test_blocks_over_per_call_cost_limit(live_settings, ledger):
    # 1000 output tokens on the fallback price = $0.075 > $0.05
    with pytest.raises(BudgetExceeded) as err:
        check_before_call("s1", 10, 1000, "unknown-model", settings=live_settings, db_path=ledger)
    assert err.value.limit == "per-call cost"


def test_blocks_over_session_budget(live_settings, ledger):
    record_after_call("s1", "agent", "test-smart-model", 0, 4_900, 1.0, db_path=ledger)  # $0.098
    with pytest.raises(BudgetExceeded) as err:
        check_before_call("s1", 100, 500, "test-fast-model", settings=live_settings, db_path=ledger)
    assert err.value.limit == "session"
    # A different session is unaffected.
    check_before_call("s2", 100, 500, "test-fast-model", settings=live_settings, db_path=ledger)


def test_blocks_over_daily_budget(live_settings, ledger):
    for i in range(20):  # 20 sessions x $0.0999 = $1.998 today; next call's worst case is $0.0021
        record_after_call(f"other-{i}", "agent", "test-smart-model", 0, 4_995, 1.0, db_path=ledger)
    with pytest.raises(BudgetExceeded) as err:
        check_before_call("fresh", 100, 500, "test-fast-model", settings=live_settings, db_path=ledger)
    assert err.value.limit == "daily"


def test_yesterday_does_not_count_toward_daily(live_settings, ledger):
    record_after_call("old", "agent", "test-smart-model", 0, 99_000, 1.0, db_path=ledger)  # $1.98
    yesterday = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    with sqlite3.connect(ledger) as conn:
        conn.execute("UPDATE calls SET timestamp = ?", (yesterday,))
    conn.close()
    assert budget.spent_today(ledger) == 0
    check_before_call("fresh", 100, 500, "test-fast-model", settings=live_settings, db_path=ledger)


def test_record_uses_actual_usage_and_cache_hits_are_free(ledger):
    assert record_after_call("s1", "a", "test-fast-model", 1000, 500, 0.4, db_path=ledger) == pytest.approx(0.003)
    assert record_after_call("s1", "a", "test-fast-model", 1000, 500, 0.0, cache_hit=True, db_path=ledger) == 0
    assert record_after_call("s1", "a", "test-fast-model", 1000, 500, 0.0, billable=False, db_path=ledger) == 0
    with sqlite3.connect(ledger) as conn:
        rows = conn.execute("SELECT session_id, agent, model, input_tokens, output_tokens, cache_hit FROM calls").fetchall()
    conn.close()
    assert rows[0] == ("s1", "a", "test-fast-model", 1000, 500, 0)
    assert rows[1][-1] == 1


def test_summary_groups_by_agent_and_session(ledger):
    record_after_call("s1", "explainer", "test-fast-model", 1000, 500, 0.5, db_path=ledger)
    record_after_call("s1", "critic", "test-smart-model", 1000, 500, 1.5, db_path=ledger)
    record_after_call("s2", "explainer", "test-fast-model", 1000, 500, 0.5, cache_hit=True, db_path=ledger)
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


# --- real model prices -------------------------------------------------------

def test_real_model_prices_are_listed():
    assert budget.PRICES["gpt-6-luna"] == budget.ModelPrice(0.10, 0.50, 0.01)
    assert budget.PRICES["gpt-6-sol"] == budget.ModelPrice(2.00, 10.00, 0.20)
    assert budget.PRICES["gpt-6-astra"] == budget.ModelPrice(10.00, 50.00, 1.00)
    # the fallback stays above every real model
    assert all(p.output_per_1m < budget.FALLBACK_PRICE.output_per_1m for p in budget.PRICES.values())


def test_full_analysis_on_luna_fits_default_budgets(ledger):
    from secondlook.config import Settings

    s = Settings()  # defaults: $0.05 per call, $0.10 session, 1,500 tokens per call
    # worst case per agent call: ~3,000 input tokens of facts and prompt + the agent's max_tokens
    worst = [check_before_call("s", 3000, out, "gpt-6-luna", settings=s, db_path=ledger) for out in (700, 1400, 500, 1400)]
    # 3,000 x $0.10/1M + 1,400 x $0.50/1M = $0.0003 + $0.0007 = $0.001 for the explainer
    assert max(worst) == pytest.approx(0.001)
    assert sum(worst) < s.session_budget_usd / 10


def test_astra_explainer_is_blocked_by_default_per_call_limit(ledger):
    from secondlook.config import Settings

    # 3,000 x $10/1M + 1,400 x $50/1M = $0.03 + $0.07 = $0.10 > $0.05
    with pytest.raises(BudgetExceeded) as err:
        check_before_call("s", 3000, 1400, "gpt-6-astra", settings=Settings(), db_path=ledger)
    assert err.value.limit == "per-call cost" and err.value.worst_case_usd == pytest.approx(0.10)


def test_summary_by_session_prefix(ledger):
    record_after_call("rec-1-00", "a", "test-fast-model", 1000, 0, 0.1, db_path=ledger)   # $0.001
    record_after_call("rec-1-01", "b", "test-fast-model", 2000, 0, 0.1, db_path=ledger)   # $0.002
    record_after_call("rec-10-00", "a", "test-fast-model", 4000, 0, 0.1, db_path=ledger)  # $0.004, other prefix
    record_after_call("rec_1-x", "a", "test-fast-model", 8000, 0, 0.1, db_path=ledger)    # "_" is not a wildcard
    out = budget.summary(ledger, session_prefix="rec-1-")
    assert out["total_usd"] == pytest.approx(0.003)
    assert {r["name"] for r in out["by_session"]} == {"rec-1-00", "rec-1-01"}


# --- cached input tokens (catch-up phase 2) ----------------------------------

def test_cached_input_tokens_billed_at_cached_rate():
    # gpt-6-luna: 1,000 input of which 600 cached, 100 output
    # 400 x $0.10/1M + 600 x $0.01/1M + 100 x $0.50/1M = $0.00004 + $0.000006 + $0.00005
    assert budget.cost_usd("gpt-6-luna", 1000, 100, cached_input_tokens=600) == pytest.approx(0.000096)
    assert budget.cost_usd("gpt-6-luna", 1000, 100) == pytest.approx(0.00015)
    # cached tokens can never exceed input tokens; models without a cached rate bill cached input at the full rate
    assert budget.cost_usd("gpt-6-luna", 100, 0, cached_input_tokens=500) == pytest.approx(100 * 0.01 / 1e6)
    assert budget.cost_usd("test-fast-model", 1000, 0, cached_input_tokens=1000) == pytest.approx(0.001)


def test_record_and_summarize_cached_input_tokens(ledger):
    record_after_call("s1", "explainer", "gpt-6-luna", 2000, 500, 0.5, cached_input_tokens=1200, db_path=ledger)
    record_after_call("s1", "explainer", "gpt-6-luna", 2000, 500, 0.5, db_path=ledger)
    row = budget.summary(ledger)["by_agent"][0]
    assert (row["input_tokens"], row["cached_input_tokens"]) == (4000, 1200)
    assert budget.calls(ledger)[0]["cached_input_tokens"] == 1200


def test_old_ledger_is_migrated_without_losing_rows(ledger):
    ledger.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(ledger) as conn:  # the table as it was before the new column
        conn.execute("""CREATE TABLE calls (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT NOT NULL,
                        session_id TEXT NOT NULL, agent TEXT NOT NULL, model TEXT NOT NULL, input_tokens INTEGER NOT NULL,
                        output_tokens INTEGER NOT NULL, cost_usd REAL NOT NULL, latency_s REAL NOT NULL,
                        cache_hit INTEGER NOT NULL)""")
        conn.execute("INSERT INTO calls (timestamp, session_id, agent, model, input_tokens, output_tokens, cost_usd, "
                     "latency_s, cache_hit) VALUES ('2026-10-17T00:00:00+00:00', 'old', 'analyst', 'gpt-6-luna', 10, 5, 0.25, 1.0, 0)")
    conn.close()
    rows = budget.calls(ledger)
    assert len(rows) == 1 and rows[0]["session_id"] == "old" and rows[0]["cached_input_tokens"] == 0
    record_after_call("new", "analyst", "gpt-6-luna", 100, 10, 0.1, cached_input_tokens=50, db_path=ledger)
    assert [r["cached_input_tokens"] for r in budget.calls(ledger)] == [0, 50]
    assert budget.summary(ledger)["total_usd"] == pytest.approx(0.25 + budget.cost_usd("gpt-6-luna", 100, 10, 50), abs=1e-6)
