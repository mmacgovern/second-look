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
