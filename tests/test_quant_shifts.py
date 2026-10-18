"""Possible shifts: the Python rules, the named-security guard, and the shifts agent's checks and fallback."""

import pytest

from agent_fixtures import reply
from conftest import ODD_KEY, FakeClient
from secondlook import budget, orchestrator
from secondlook.agents.base import AgentContext
from secondlook.config import Settings
from secondlook.quant import shifts as sh
from secondlook.quant.accounts import Household, TaggedAccount
from secondlook.quant.benchmark import Benchmark
from secondlook.quant.fees import FeeProjection, FeeReport
from secondlook.quant.models import Holding
from secondlook.quant.xray import CompanyExposure, XRay
from secondlook.safety import advice_guard

BENCH = Benchmark(fund="IDX", fund_name="Toy Index Fund", as_of="2026-10-17", source="test",
                  top_holdings=[{"symbol": "AAA", "name": "Acme Robotics Inc", "weight": 0.1}],
                  sector_weights={"Information Technology": 0.40, "Financials": 0.12, "Health Care": 0.10,
                                  "Industrials": 0.08, "Energy": 0.30})
RANGES = {"stock_pct": {"Moderate": [50, 70]}}


def make_xr(sectors, top_weight=0.05):
    return XRay(total_value_usd=100_000, cash_weight=sectors.get("cash", 0.0), sectors=sectors, overlaps=[], coverage=1.0,
                top_companies=[CompanyExposure(symbol="AAA", name="Acme Robotics Inc", weight=top_weight,
                                               value_usd=top_weight * 100_000, via={"direct": top_weight})],
                funds_without_holdings=[], unknown_symbols=[])


def fees(er=0.0005, extra=0.0):
    return FeeReport(weighted_expense_ratio=er, annual_cost_usd=er * 100_000, baseline_expense_ratio=0.0005,
                     gross_return=0.06, covered_value_usd=100_000, funds=[], unknown_symbols=[],
                     projections=[FeeProjection(years=30, value_no_fees_usd=1, value_baseline_usd=1, value_portfolio_usd=1,
                                                  cost_vs_no_fees_usd=1, cost_vs_baseline_usd=extra)])


def test_sector_rule_points_from_overweight_to_underweight():
    xr = make_xr({"technology": 0.60, "energy": 0.20, "financial_services": 0.10, "healthcare": 0.05, "industrials": 0.05})
    out = sh.possible_shifts(xr, fees(), BENCH, profiles={})
    sector = next(s for s in out if s.kind == "sector")
    assert sector.numbers["sector"] == "Information Technology" and sector.numbers["you_pct"] == 60.0
    assert sector.numbers["sp500_pct"] == 40.0 and sector.numbers["toward_1"] == "Energy"
    assert "Information Technology" in sector.detail and "Energy (20.0% vs. 30.0%)" in sector.detail


def test_no_rule_fires_for_a_portfolio_that_matches_the_references():
    # 60% stocks (inside 50-70%), and the stocks split exactly like the toy S&P 500 fund
    xr = make_xr({"technology": 0.24, "energy": 0.18, "financial_services": 0.072, "healthcare": 0.06,
                  "industrials": 0.048, "bonds": 0.40})
    assert sh.possible_shifts(xr, fees(), BENCH, profiles={}, risk_label="Moderate", ranges=RANGES) == []


def test_asset_mix_rule_both_directions():
    high = sh.possible_shifts(make_xr({"technology": 0.95, "bonds": 0.05}), fees(), BENCH, profiles={},
                              risk_label="Moderate", ranges=RANGES)
    mix = next(s for s in high if s.kind == "asset_mix")
    assert "toward bonds or cash" in mix.title and mix.numbers["stock_pct"] == 95.0
    low = sh.possible_shifts(make_xr({"technology": 0.30, "bonds": 0.70}), fees(), BENCH, profiles={},
                             risk_label="Moderate", ranges=RANGES)
    assert "toward stocks" in next(s for s in low if s.kind == "asset_mix").title


def test_single_company_and_fee_rules_never_name_the_security():
    out = sh.possible_shifts(make_xr({"technology": 0.4, "energy": 0.3, "financial_services": 0.12, "healthcare": 0.1,
                                      "industrials": 0.08}, top_weight=0.14), fees(0.002, 5000), BENCH, profiles={})
    kinds = {s.kind: s for s in out}
    assert kinds["single_company"].numbers["largest_company_pct"] == 14.0
    assert kinds["fees"].numbers["extra_cost_usd"] == 5000
    for s in out:
        assert "AAA" not in s.detail and "Acme" not in s.detail


def test_account_rule_flags_bonds_outside_pre_tax_accounts():
    profiles = {"BND": {"quote_type": "ETF", "asset_classes": {"bondPosition": 1.0}, "sector_weights": {}}}
    hh = Household(accounts=[
        TaggedAccount(label="A1", account_type="taxable",
                      holdings=[Holding(symbol="BND", quantity=1, market_value=20_000, account="A1")]),
        TaggedAccount(label="A2", account_type="401k_403b",
                      holdings=[Holding(symbol="BND", quantity=1, market_value=5_000, account="A2")]),
    ])
    shift = sh._account_shift(hh, profiles)
    assert shift.numbers == {"bonds_outside_pre_tax_usd": 20000, "accounts": "Taxable brokerage"}
    assert sh._account_shift(Household(accounts=hh.accounts[:1]), profiles) is None  # no pre-tax account


def test_at_most_four_shifts():
    out = sh.possible_shifts(make_xr({"technology": 0.95, "bonds": 0.05}, top_weight=0.2), fees(0.003, 9000), BENCH,
                             profiles={}, risk_label="Moderate", ranges=RANGES)
    assert len(out) <= sh.MAX_SHIFTS


def test_guard_allows_sector_and_account_shifts_but_not_securities():
    ok = "Consider shifting some money from Information Technology toward Health Care, and holding bonds in your IRA."
    assert advice_guard.check_shift(ok, ["AAA", "Acme"]) == []
    assert [h.rule for h in advice_guard.check_shift("You could move money into Acme instead.", ["AAA", "Acme"])] == ["named_security"]
    assert any(h.rule == "named_security" for h in advice_guard.check_shift("Consider QQQ for growth.", []))
    assert any(h.rule == "promise" for h in advice_guard.check_shift("This shift is guaranteed to help.", []))
    assert any(h.rule == "prediction" for h in advice_guard.check_shift("Tech will crash next year.", []))


def test_banned_names_include_holdings_fund_holdings_and_benchmark():
    profiles = {"VOO": {"name": "Vanguard S&P 500 ETF", "top_holdings": [{"symbol": "AAPL", "name": "Apple Inc."}]}}
    names = sh.banned_names({"VOO": 1.0, "CASH": 1.0}, profiles, BENCH)
    assert {"VOO", "Vanguard", "AAPL", "Apple", "AAA", "Acme", "IDX"} <= set(names) and "CASH" not in names


# --- orchestrator.run_shifts -------------------------------------------------------------

FACTS = {"about": "x", "reference": "S&P 500 weights via Toy",
         "shifts": [{"kind": "sector", "title": "Shift some stock money out of Information Technology",
                     "sector": "Information Technology", "you_pct": 60.0, "sp500_pct": 40.0}]}
DETAILS = ["Consider shifting part of your stock money out of Information Technology: 60.0% vs. 40.0%."]
GOOD = {"summary": "One shift stands out.", "items": [{"title": "Less Information Technology",
        "body": "Information Technology is 60% of your stocks vs. 40% of the S&P 500. Consider shifting some of it."}]}


@pytest.fixture
def ctx(ledger):
    def make(*replies):
        s = Settings(portkey_api_key=ODD_KEY, demo_mode=False, model_fast="test-fast-model", model_smart="test-smart-model")
        return AgentContext(session_id="s", settings=s, client=FakeClient(*replies), db_path=ledger)
    return make


def test_run_shifts_uses_checked_ai_wording(ctx):
    out = orchestrator.run_shifts(FACTS, DETAILS, "new", ctx(reply(GOOD)), ["AAA"])
    assert out.source == "ai" and out.items[0].title == "Less Information Technology"


def test_run_shifts_falls_back_when_a_security_is_named(ctx, ledger):
    bad = {"summary": "One shift.", "items": [{"title": "Sell Acme", "body": "Move your Acme money to bonds; it is 60%."}]}
    out = orchestrator.run_shifts(FACTS, DETAILS, "pro", ctx(reply(bad)), ["Acme"])
    assert out.source == "template" and out.items[0].body == DETAILS[0]
    assert any("named security" in r for r in out.reasons)
    assert budget.rewrites(ledger)[0]["stage"] == "template"


def test_run_shifts_rejects_invented_numbers(ctx):
    bad = {"summary": "x", "items": [{"title": "Tech", "body": "Tech is 73% of your stocks."}]}
    assert orchestrator.run_shifts(FACTS, DETAILS, "pro", ctx(reply(bad)), []).source == "template"


def test_run_shifts_makes_no_call_without_shifts(ctx):
    c = ctx(reply(GOOD))
    out = orchestrator.run_shifts({"shifts": []}, [], "new", c, [])
    assert out.source == "none" and out.items == [] and c.client.calls == []
