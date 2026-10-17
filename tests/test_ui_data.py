import pandas as pd
import pytest

from quant_toys import TOY_PROFILES, TOY_VALUES
from secondlook import budget
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
