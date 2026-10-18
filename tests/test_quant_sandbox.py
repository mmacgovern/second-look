"""Try a shift: the pretend-$1,000 replay math, the sandbox agent's checks and its fallback."""

import numpy as np
import pandas as pd
import pytest

from agent_fixtures import reply
from conftest import ODD_KEY, FakeClient
from secondlook import orchestrator
from secondlook.agents.base import AgentContext
from secondlook.config import Settings
from secondlook.quant import sandbox as sb
from secondlook.quant.models import CASH


@pytest.fixture
def adj():
    """Toy history: stocks double then halve, bonds rise steadily, international flat."""
    days = pd.bdate_range("2007-01-02", "2010-12-31")
    n = len(days)
    spy = np.concatenate([np.linspace(100, 200, n // 2), np.linspace(200, 100, n - n // 2)])
    return pd.DataFrame({"SPY": spy, "EFA": np.full(n, 50.0), "AGG": np.linspace(100, 120, n),
                         "XYZ": np.linspace(10, 30, n)}, index=days)


def test_mix_weights_sum_to_100_and_use_only_asset_types():
    for name, mix in sb.MIXES.items():
        assert sum(mix.values()) == pytest.approx(1.0), name
        assert set(mix) <= set(sb.ASSET_TYPES)
    stocks = {name: mix.get("SPY", 0) + mix.get("EFA", 0) for name, mix in sb.MIXES.items()}
    assert stocks["Conservative"] < stocks["Moderate"] < stocks["Risky"]


def test_replay_math_by_hand(adj):
    r = sb.run_sandbox(adj, {"XYZ": 600.0, CASH: 400.0})
    risky = next(s for s in r.strategies if s.name == "Risky")
    # Risky = 70% stocks (back to where they started), 25% flat international, 5% bonds (+20%), daily rebalanced
    assert risky.end_usd < 1000 + 0.05 * 200 + 1  # the stock round trip costs money when rebalancing daily
    assert risky.worst_drop_pct > 25
    yours = next(s for s in r.strategies if s.name == sb.YOURS)
    assert yours.mix_pct == {} and yours.end_usd > 1000  # XYZ tripled; cash flat
    assert r.amount_usd == 1000 and r.start == "2007-01-02" and r.years == pytest.approx(4.0, abs=0.05)


def test_conservative_falls_least(adj):
    r = sb.run_sandbox(adj)
    drops = {s.name: s.worst_drop_pct for s in r.strategies}
    assert drops["Conservative"] < drops["Moderate"] < drops["Risky"]
    assert sb.YOURS not in drops  # no portfolio given


def test_paths_are_monthly_and_start_at_the_amount(adj):
    r = sb.run_sandbox(adj, amount=1000)
    assert len(r.dates) == 49 and all(len(v) == 49 for v in r.paths.values())  # start day + 48 month ends
    assert r.dates[0] == "2007-01-02" and all(v[0] == pytest.approx(1000) for v in r.paths.values())


def test_facts_say_hindsight_and_past_results():
    facts = sb.sandbox_facts(sb.SandboxResult(amount_usd=1000, start="2007-01-03", end="2026-10-16", years=19.8,
                                              strategies=[], paths={}, dates=[]))
    assert "hindsight" in facts["your_portfolio_note"] and "Past results" in facts["past_results_note"]
    assert facts["period"] == "2007-01 to 2026-10"


# --- orchestrator.run_sandbox ---------------------------------------------------------------

FACTS = {"pretend_amount_usd": 1000, "period": "2007-01 to 2026-10", "your_portfolio_note": "hindsight",
         "strategies": [{"name": "Risky", "value_today_usd": 5637, "yearly_return_pct": 9.1, "worst_drop_pct": 54.2},
                        {"name": "Conservative", "value_today_usd": 2397, "yearly_return_pct": 4.5, "worst_drop_pct": 17.6}]}
GOOD = {"summary": "More stocks grew more and fell further. Your portfolio's replay has hindsight built in.",
        "points": ["Risky grew to $5,637 but fell 54.2% at worst.", "Conservative grew to $2,397 and fell 17.6% at worst."]}


@pytest.fixture
def ctx(ledger):
    def make(*replies):
        s = Settings(portkey_api_key=ODD_KEY, demo_mode=False, model_fast="test-fast-model", model_smart="test-smart-model")
        return AgentContext(session_id="s", settings=s, client=FakeClient(*replies), db_path=ledger)
    return make


def test_run_sandbox_uses_checked_ai_text(ctx):
    out = orchestrator.run_sandbox(FACTS, "comfortable", ctx(reply(GOOD)), ["SPY"])
    assert out.source == "ai" and out.points == GOOD["points"]


@pytest.mark.parametrize("bad", [
    {**GOOD, "points": ["SPY did best.", "Bonds were steadier."]},                                  # names a ticker
    {**GOOD, "points": ["Risky grew to $6,100.", "Bonds were steadier."]},                         # invented number
    {"summary": "More stocks grew more.", "points": ["Risky fell 54.2%.", "Conservative fell 17.6%."]},  # no hindsight
    {**GOOD, "points": ["Risky will rise again.", "Conservative fell 17.6%."]},                    # prediction
])
def test_run_sandbox_falls_back_on_bad_drafts(ctx, bad):
    out = orchestrator.run_sandbox(FACTS, "pro", ctx(reply(bad)), ["SPY"])
    assert out.source == "template" and out.reasons
    assert "$5,637" in " ".join(out.points) and "hindsight" in out.summary
