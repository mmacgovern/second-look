import pandas as pd
import pytest

from quant_toys import TOY_PROFILES, TOY_VALUES
from secondlook import budget, ui_data
from secondlook.quant.fees import fee_report
from secondlook.quant.models import Action
from secondlook.quant.replay import Scenario
from secondlook.quant.xray import xray
from secondlook.ui_data import (
    company_table, crash_frame, fee_curves, friendly_note, holdings_for_buy, overlap_table, parse_action,
    session_cost, treemap_frame,
)


def test_treemap_tiles_add_up_to_the_portfolio():
    frame = treemap_frame(xray(TOY_VALUES, TOY_PROFILES, top_n=100))
    by_id = frame.set_index("id")
    # AAA $5,400 = direct $3,000 + via FUNDA $2,000 + via FUNDB $400
    assert by_id.loc["AAA", "value"] == pytest.approx(5400)
    assert by_id.loc["AAA/direct", "label"] == "Held directly" and by_id.loc["AAA/FUNDA", "value"] == pytest.approx(2000)
    # untraced = invested $9,000 - named $7,400 (AAA 5,400 + BBB 1,200 + CCC 800)
    assert by_id.loc["untraced", "value"] == pytest.approx(1600)
    assert by_id.loc["cash", "value"] == pytest.approx(1000)
    assert by_id.loc["Portfolio", "value"] == pytest.approx(10000)
    # every parent equals the sum of its children (Plotly branchvalues="total")
    for parent, group in frame[frame["parent"] != ""].groupby("parent"):
        assert group["value"].sum() == pytest.approx(by_id.loc[parent, "value"])


def test_treemap_groups_companies_beyond_top():
    frame = treemap_frame(xray(TOY_VALUES, TOY_PROFILES, top_n=100), top=1)
    assert frame.set_index("id").loc["other", "value"] == pytest.approx(2000)  # BBB 1,200 + CCC 800


def test_overlap_and_company_tables():
    xr = xray(TOY_VALUES, TOY_PROFILES)
    assert overlap_table(xr).to_dict("records") == [{"Funds": "FUNDA and FUNDB", "Shared holdings (%)": 20.0}]
    first = company_table(xr).iloc[0]
    assert (first["Ticker"], first["Share (%)"], first["Dollars"], first["Direct (%)"]) == ("AAA", 54.0, 5400, 30.0)


def test_fee_curves_by_hand():
    curves = fee_curves(fee_report(TOY_VALUES, TOY_PROFILES), years=10)
    assert len(curves) == 11
    last = curves.iloc[-1]
    # 10,000 x 1.06^10, x 1.0595^10, x 1.0576^10 (weighted ER 0.24%)
    assert last["No fees"] == pytest.approx(17908.48, abs=0.01)
    assert last["Index-fund fees (0.05%)"] == pytest.approx(17824.18, abs=0.01)
    assert last["Your fees (0.24%)"] == pytest.approx(17507.11, abs=0.01)


def test_crash_frame_dollars_and_drawdown():
    dates = pd.to_datetime(["2020-01-01", "2020-02-01", "2020-03-01", "2020-04-01", "2021-06-01"])
    adj = pd.DataFrame({"X": [100.0, 90.0, 80.0, 100.0, 110.0]}, index=dates)
    scenario = Scenario(key="t", label="Toy", start="2020-01-01", end="2020-03-15")
    frame = crash_frame({"X": 2000.0}, adj, scenario, recovery_date="2020-04-01", extra_months=1)
    assert frame["value_usd"].tolist() == pytest.approx([2000, 1800, 1600, 2000])  # stops a month after recovery
    assert frame["drawdown_pct"].tolist() == pytest.approx([0, -10, -20, 0])


@pytest.mark.parametrize("text, expected", [
    ("Sell everything", Action(kind="sell_all")),
    ("I want to get out of the market", Action(kind="sell_all")),
    ("move it all to cash", Action(kind="sell_all")),
    ("move half to cash", Action(kind="sell_pct", pct=50)),
    ("sell 30%", Action(kind="sell_pct", pct=30)),
    ("sell 25% of VOO", Action(kind="sell_pct", pct=25, ticker="VOO")),
    ("sell all of my aapl", Action(kind="sell_all", ticker="AAPL")),
    ("buy $5,000 of QQQ", Action(kind="buy", ticker="QQQ", amount_usd=5000)),
    ("buy 2k more nvda", Action(kind="buy", ticker="NVDA", amount_usd=2000)),
    ("buy more VTI for $1,500", Action(kind="buy", ticker="VTI", amount_usd=1500)),
])
def test_parse_action(text, expected):
    assert parse_action(text, ["VOO", "AAPL", "NVDA"]) == expected


@pytest.mark.parametrize("text", ["", "what do you think?", "buy more", "sell ZZZZ", "sell 150%"])
def test_parse_action_returns_none_when_unclear(text):
    assert parse_action(text, ["VOO"]) is None


def test_session_cost_from_ledger(ledger):
    budget.record_after_call("web-1", "explainer", "test-fast-model", 1000, 500, 0.5, db_path=ledger)
    budget.record_after_call("web-1", "verifier", "test-fast-model", 200, 100, 0.2, db_path=ledger)
    budget.record_after_call("web-2", "explainer", "test-fast-model", 9999, 9999, 0.5, db_path=ledger)
    cost = session_cost("web-1", ledger)
    # (1,000 + 200) x $1/1M + (500 + 100) x $4/1M = $0.0012 + $0.0024
    assert (cost.calls, cost.tokens, cost.usd) == (2, 1800, pytest.approx(0.0036))
    assert session_cost("nobody", ledger) == type(cost)(0, 0, 0.0)


def test_friendly_notes():
    assert friendly_note(None, False) is None
    assert "spending limit" in friendly_note("The AI spending limit was reached, so...", False)
    assert "demo mode" in friendly_note("The AI explanation was not available...", True)
    assert "not available" in friendly_note("The AI explanation was not available...", False)


def test_holdings_for_buy_lists_holdings_first_then_index_funds():
    assert holdings_for_buy({"AAPL": 10, "VOO": 50, "CASH": 5}) == ["VOO", "AAPL", "VTI", "QQQ"]


# --- Retirement Plan tab helpers (Prompt 7b) ------------------------------------

from secondlook.quant.plan import make_plan  # noqa: E402
from secondlook.quant.profile import RISK_PROFILES, UserProfile  # noqa: E402
from secondlook.ui_data import (  # noqa: E402
    PLAN_LABEL, asset_mix, balances_frame, gap_headline, load_risk_ranges, mix_vs_range, plan_band_frame,
)

RETURNS = {"cautious": 0.0, "middle": 0.05, "strong": 0.10}


def saver(contribution=10_000.0):
    return UserProfile(age=63, retirement_age=65, desired_income_today_usd=50_000,
                       social_security_or_pension_usd=30_000, annual_contribution_usd=contribution)


def test_plan_band_frame_by_hand():
    plan = make_plan(saver(), 100_000, real_returns=RETURNS)
    band = plan_band_frame(plan)
    assert band["age"].tolist() == [63, 64, 65]
    # middle: 100,000 -> 115,000 -> 130,750 (5%, +10,000 at each year end)
    assert band["middle"].tolist() == pytest.approx([100_000, 115_000, 130_750])
    assert band["cautious"].tolist() == pytest.approx([100_000, 110_000, 120_000])
    assert band["target"].tolist() == pytest.approx([20_000 / 0.039] * 3)


def test_gap_headline_saving_range():
    plan = make_plan(saver(), 100_000, real_returns=RETURNS)
    text, lo, hi = gap_headline(plan, 50_000)
    # required yearly saving minus today's 10,000, for each scenario; range from strong to cautious
    need = [s.required_annual_contribution_usd - 10_000 for s in plan.scenarios]
    assert (lo, hi) == pytest.approx((min(need), max(need)))
    assert text == f"Estimated gap: ${min(need):,.0f} to ${max(need):,.0f} of extra saving per year"


def test_gap_headline_on_track_and_retired():
    rich = make_plan(saver(), 2_000_000, real_returns=RETURNS)
    assert gap_headline(rich, 50_000) == ("On track in every scenario", 0.0, 0.0)
    retiree = UserProfile(age=70, retirement_age=65, desired_income_today_usd=65_000, social_security_or_pension_usd=34_000)
    plan = make_plan(retiree, 577_868)
    # supported income = 577,868 x 3.9% + 34,000 = 56,536.85 -> shortfall 8,463.15 in every scenario
    text, lo, hi = gap_headline(plan, 65_000)
    assert lo == hi == pytest.approx(65_000 - (577_868 * 0.039 + 34_000))
    assert text == "Estimated gap: $8,463 of income per year"


def test_balances_frame_applies_tax_to_pre_tax_only():
    frame = balances_frame({"401k_403b": 1000.0, "roth_ira": 2000.0}, 0.15)
    assert frame.to_dict("records") == [
        {"Account type": "Roth IRA", "Balance": 2000.0, "Counted toward retirement": 2000.0},
        {"Account type": "401(k)/403(b)", "Balance": 1000.0, "Counted toward retirement": 850.0},
    ]


def test_asset_mix_from_look_through():
    # toy sectors: tech .64, health .16, energy .10, cash .10
    assert asset_mix(xray(TOY_VALUES, TOY_PROFILES)) == pytest.approx({"Stocks": 90.0, "Bonds": 0.0, "Cash": 10.0, "Other": 0.0})


def test_mix_vs_range():
    ranges = {"stock_pct": {"Moderate": [50, 70]}}
    assert mix_vs_range(45, "Moderate", ranges) == ((50.0, 70.0), "below")
    assert mix_vs_range(60, "Moderate", ranges) == ((50.0, 70.0), "within")
    assert mix_vs_range(99.2, "Moderate", ranges) == ((50.0, 70.0), "above")
    assert mix_vs_range(60, "Aggressive", ranges) == (None, "no reference")


def test_reference_ranges_cover_every_risk_profile_and_are_labeled():
    ranges = load_risk_ranges()
    assert set(ranges["stock_pct"]) == set(RISK_PROFILES.values())
    assert ranges["is_illustrative"] is True and "Illustrative" in ranges["source"]
    for lo, hi in ranges["stock_pct"].values():
        assert 0 <= lo < hi <= 100
    assert PLAN_LABEL == "Planning estimate, not a guarantee."


def test_params_to_reset_keeps_widget_bound_params():
    keys = ["sample", "fluency", "pause", "tab", "page", "about"]
    assert ui_data.params_to_reset(keys) == ["sample", "pause", "page", "about"]


def test_widget_params_match_app_bindings():
    """Every widget bound to the URL in app.py is protected from the upload reset."""
    import re
    from pathlib import Path

    app = (Path(__file__).resolve().parents[1] / "app.py").read_text(encoding="utf-8")
    bound = set(re.findall(r'key="(\w+)"[^)]*bind="query-params"', app))
    assert bound == set(ui_data.WIDGET_PARAMS)
