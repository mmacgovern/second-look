import json
from pathlib import Path

import pandas as pd
import pytest

from quant_toys import TOY_PROFILES
from secondlook.quant.facts import TOKEN_TARGET, build_facts, estimate_tokens, facts_json, load_market_inputs, pct, usd
from secondlook.quant.models import Action
from secondlook.quant.parse import parse_csv

SAMPLES = Path(__file__).resolve().parents[1] / "data" / "sample_portfolios"
TOY_CSV = """Symbol,Quantity,Price,Account Number
FUNDA,40,100,ACCT-111
FUNDB,20,100,ACCT-111
AAA,30,100,ACCT-222
CASH,1000,1,ACCT-222
"""


@pytest.fixture
def toy_adj():
    # Flat prices through every scenario window -> no drawdowns
    dates = pd.date_range("2007-01-01", "2023-12-01", freq="MS")
    return pd.DataFrame({t: 100.0 for t in ("FUNDA", "FUNDB", "AAA", "SPY")}, index=dates)


@pytest.fixture
def toy_facts(toy_adj):
    return build_facts(parse_csv(TOY_CSV), TOY_PROFILES, toy_adj)


def test_rounding_helpers():
    assert pct(0.123456) == 12.3 and pct(0.0024, 3) == 0.24 and pct(None) is None
    assert usd(1234.5) == 1234 and usd(1235.5) == 1236 and usd(None) is None


def test_toy_facts_numbers(toy_facts):
    p = toy_facts["portfolio"]
    assert (p["total_usd"], p["holdings_count"], p["accounts_count"], p["cash_pct"]) == (10000, 3, 2, 10.0)
    assert p["largest_holdings"][0] == {"ticker": "FUNDA", "pct": 40.0, "usd": 4000}

    lt = toy_facts["look_through"]
    assert lt["coverage_pct_of_invested"] == 82.2           # 0.74 / 0.90
    assert lt["top_companies"][0] == {
        "ticker": "AAA", "name": "Aaa Corp", "pct": 54.0, "usd": 5400,
        "via_pct": {"direct": 30.0, "FUNDA": 20.0, "FUNDB": 4.0},
    }
    assert lt["sectors_pct"] == {"Technology": 64.0, "Healthcare": 16.0, "Energy": 10.0, "Cash": 10.0}
    assert lt["fund_overlap_pct"] == [{"funds": "FUNDA+FUNDB", "overlap_pct": 20.0}]

    fees = toy_facts["fees"]
    assert fees["weighted_expense_ratio_pct"] == 0.24 and fees["annual_fees_usd"] == 24
    assert fees["projection"][0] == {"years": 10, "fees_cost_usd": 401, "extra_vs_baseline_usd": 317}
    assert fees["costliest_funds"][0] == {"ticker": "FUNDB", "expense_ratio_pct": 1.0, "annual_usd": 20}

    assert [r["max_drawdown_pct"] for r in toy_facts["crash_replays"]] == [0.0, 0.0, 0.0]


def test_every_number_is_rounded(toy_facts):
    def walk(node):
        if isinstance(node, dict):
            for v in node.values():
                yield from walk(v)
        elif isinstance(node, list):
            for v in node:
                yield from walk(v)
        elif isinstance(node, float):
            yield node

    for number in walk(toy_facts):
        assert round(number, 3) == number


def test_no_account_numbers_or_descriptions(toy_facts):
    text = facts_json(toy_facts)
    assert "ACCT-111" not in text and "ACCT-222" not in text


def test_pause_section_only_with_action(toy_adj):
    portfolio = parse_csv(TOY_CSV)
    assert "pause_check" not in build_facts(portfolio, TOY_PROFILES, toy_adj)
    facts = build_facts(portfolio, TOY_PROFILES, toy_adj, Action(kind="sell_pct", pct=50))
    pause = facts["pause_check"]
    assert pause["proposed_action"] == "Sell 50% of the whole portfolio to cash"
    assert pause["before"]["cash_pct"] == 10.0 and pause["after"]["cash_pct"] == 55.0


def test_trims_detail_to_fit_token_target(toy_adj):
    portfolio = parse_csv(TOY_CSV)
    full = build_facts(portfolio, TOY_PROFILES, toy_adj)
    tight = build_facts(portfolio, TOY_PROFILES, toy_adj, token_target=10)
    assert "via_pct" in full["look_through"]["top_companies"][0]
    assert "via_pct" not in tight["look_through"]["top_companies"][0]
    assert len(facts_json(tight)) < len(facts_json(full))


@pytest.mark.parametrize("name", ["beginner", "midcareer", "retiree"])
@pytest.mark.parametrize("action", [None, Action(kind="sell_all"), Action(kind="buy", ticker="QQQ", amount_usd=10000)])
def test_real_samples_stay_under_token_target(name, action):
    portfolio = parse_csv(SAMPLES / f"{name}.csv")
    profiles, adj = load_market_inputs(portfolio, prefer_live=False)
    facts = build_facts(portfolio, profiles, adj, action)
    text = facts_json(facts)
    assert estimate_tokens(text) < TOKEN_TARGET
    assert json.loads(text) == facts
    assert len(facts["crash_replays"]) == 3
    assert "DEMO-0000" not in text


# --- household, plan, risk profile and concentration (Prompt 4b) -------------

from secondlook.quant.accounts import household_from_csvs  # noqa: E402
from secondlook.quant.profile import GlidePath, UserProfile, load_persona  # noqa: E402

TOY_MARKET = {"technology": 0.5, "healthcare": 0.3, "financial_services": 0.2}
TOY_USER = UserProfile(age=55, retirement_age=65, desired_income_today_usd=50000,
                       social_security_or_pension_usd=30000, annual_contribution_usd=5000)


@pytest.fixture
def household_facts(toy_adj):
    # Account 1 (FUNDA + FUNDB, $6,000) is a 401(k); Account 2 (AAA + cash, $4,000) is a Roth IRA
    household = household_from_csvs([(TOY_CSV, "401k_403b", {"Account 2": "roth_ira"})])
    return build_facts(None, TOY_PROFILES, toy_adj, household=household, user=TOY_USER,
                       risk_answers=[3, 3, 3, 3, 3], market_sectors=TOY_MARKET)


def test_household_accounts_and_counted_balance(household_facts):
    # 6,000 x 0.85 + 4,000 = 9,100
    assert household_facts["accounts"] == {
        "by_type_usd": {"401(k)/403(b)": 6000, "Roth IRA": 4000}, "counted_for_retirement_usd": 9100}


def test_plan_section_by_hand(household_facts):
    plan = household_facts["retirement_plan"]
    # (50,000 - 30,000) / 0.039 = 512,820.51
    assert (plan["target_nest_egg_usd"], plan["income_needed_from_portfolio_usd"]) == (512821, 20000)
    assert plan["counted_balance_usd"] == 9100 and plan["status"] == "saving"
    # net return = real return - weighted ER 0.24%
    assert [s["net_return_pct"] for s in plan["scenarios"]] == [1.76, 3.76, 5.76]
    middle = plan["scenarios"][1]
    g = 1.0376 ** 10
    projected = 9100 * g + 5000 * (g - 1) / 0.0376
    assert middle["projected_at_retirement_usd"] == round(projected)
    assert middle["required_annual_contribution_usd"] == round((512820.5128 - 9100 * g) * 0.0376 / (g - 1))


def test_investor_and_concentration_sections(household_facts):
    inv = household_facts["investor"]
    # 10 years -> capacity 3; answers all 3 -> tolerance 3
    assert (inv["years_to_retirement"], inv["capacity_1to5"], inv["tolerance_1to5"], inv["risk_profile"]) == (
        10, 3, 3, "Moderate")
    assert inv["portfolio_stock_pct"] == 90.0 and inv["reference_glide_path"] == "not filled in yet"
    conc = household_facts["concentration"]
    assert conc["largest_company"] == {"ticker": "AAA", "pct": 54.0}
    assert conc["effective_holdings"] == 2.8 and conc["effective_companies_upper_bound"] == 2.6
    assert conc["companies_over_threshold"] == [
        {"ticker": "AAA", "pct": 54.0, "direct_pct": 30.0}, {"ticker": "BBB", "pct": 12.0, "direct_pct": 0.0}]
    assert conc["stock_sectors_vs_market_pct_of_stocks"][0] == {
        "sector": "Technology", "portfolio": 71.1, "market": 50.0, "difference": 21.1}


def test_every_assumption_is_labeled(household_facts):
    a = household_facts["assumptions"]
    assert a["withdrawal_rate_pct"] == 3.9
    assert a["real_returns_before_fees_pct"] == {"cautious": 2.0, "middle": 4.0, "strong": 6.0}
    assert a["fund_expense_ratio_subtracted_pct"] == 0.24
    assert a["pre_tax_accounts_reduced_by_pct"] == 15.0
    assert a["concentration_threshold_pct"] == 10.0
    assert a["fee_projection_gross_return_pct"] == 6.0 and a["fee_baseline_expense_ratio_pct"] == 0.05
    assert "today's dollars" in a["all_dollars"] and a["contributions"] and a["crash_replay_method"]


def test_user_edits_flow_into_facts(toy_adj):
    household = household_from_csvs([(TOY_CSV, "traditional_ira", None)])
    facts = build_facts(None, TOY_PROFILES, toy_adj, household=household, user=TOY_USER, withdrawal_rate=0.045,
                        tax_rate=0.25, threshold=0.5, market_sectors=TOY_MARKET)
    assert facts["assumptions"]["withdrawal_rate_pct"] == 4.5
    assert facts["accounts"]["counted_for_retirement_usd"] == 7500  # 10,000 x 0.75
    assert [f["ticker"] for f in facts["concentration"]["companies_over_threshold"]] == ["AAA"]
    assert "investor" in facts and "risk_profile" not in facts["investor"]  # no quiz answers given


def test_filled_glide_path_reported(toy_adj):
    glide = GlidePath(is_placeholder=False, source="Toy family", points=[
        {"years_to_retirement": 0, "stock_share": 0.5}, {"years_to_retirement": 20, "stock_share": 0.9}])
    facts = build_facts(parse_csv(TOY_CSV), TOY_PROFILES, toy_adj, user=TOY_USER, glide_path=glide,
                        market_sectors=TOY_MARKET)
    assert facts["investor"]["reference_stock_pct"] == 70.0  # 10 years: halfway from 50% to 90%
    assert facts["investor"]["reference_source"] == "Toy family"


@pytest.mark.parametrize("name", ["beginner", "midcareer", "retiree"])
@pytest.mark.parametrize("action", [None, Action(kind="sell_all")])
def test_personas_with_plan_stay_under_2000_tokens(name, action):
    user, household, answers = load_persona(name)
    profiles, adj = load_market_inputs(household.portfolio(), prefer_live=False)
    facts = build_facts(None, profiles, adj, action, household=household, user=user, risk_answers=answers)
    assert estimate_tokens(facts_json(facts)) < 2000
    assert {"investor", "retirement_plan", "accounts", "concentration", "assumptions"} <= set(facts)
    assert "DEMO-0000" not in facts_json(facts)


def test_employer_stock_in_facts(toy_adj):
    employee = TOY_USER.model_copy(update={"employer_ticker": "BBB"})
    facts = build_facts(parse_csv(TOY_CSV), TOY_PROFILES, toy_adj, user=employee, market_sectors=TOY_MARKET)
    assert facts["concentration"]["employer_stock"] == {"ticker": "BBB", "pct": 12.0, "direct_pct": 0.0, "over_threshold": True}
    assert "employer_stock" not in build_facts(parse_csv(TOY_CSV), TOY_PROFILES, toy_adj, user=TOY_USER,
                                               market_sectors=TOY_MARKET)["concentration"]


def test_pause_facts_carry_time_horizon_and_risk_profile(toy_adj):
    facts = build_facts(parse_csv(TOY_CSV), TOY_PROFILES, toy_adj, Action(kind="sell_all"), user=TOY_USER,
                        risk_answers=[3, 3, 3, 3, 3], market_sectors=TOY_MARKET)
    ctx = facts["pause_check"]["investor_context"]
    assert ctx == {"years_to_retirement": 10, "status": "saving", "risk_profile": "Moderate",
                   "slowest_recovery_shown_months": 1.0, "fastest_recovery_shown_months": 1.0}  # flat monthly toy prices
    no_user = build_facts(parse_csv(TOY_CSV), TOY_PROFILES, toy_adj, Action(kind="sell_all"), market_sectors=TOY_MARKET)
    assert "investor_context" not in no_user["pause_check"]


def test_real_pause_context_uses_recovery_months():
    from secondlook.demo_runs import persona_facts

    ctx = persona_facts("retiree", Action(kind="sell_all"))["pause_check"]["investor_context"]
    assert ctx["status"] == "retired" and ctx["risk_profile"] == "Conservative"
    assert ctx["fastest_recovery_shown_months"] < ctx["slowest_recovery_shown_months"]
