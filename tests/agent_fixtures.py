"""Hand-written Facts JSON and fake-LLM replies shared by the agent and orchestrator tests."""

import json

from conftest import fake_completion

FACTS = {
    "portfolio": {"total_usd": 181152, "cash_pct": 0.7},
    "look_through": {"top_companies": [
        {"ticker": "AAPL", "name": "Apple Inc.", "pct": 13.3, "usd": 24068, "via_pct": {"direct": 7.6, "VOO": 2.0}},
    ]},
    "fees": {"weighted_expense_ratio_pct": 0.05, "annual_fees_usd": 91, "projection": [
        {"years": 10, "fees_cost_usd": 1528, "extra_vs_baseline_usd": 1},
        {"years": 30, "fees_cost_usd": 14630, "extra_vs_baseline_usd": 7},
    ]},
    "crash_replays": [{"scenario": "2008-09 financial crisis", "window": "2007-10 to 2009-03",
                       "max_drawdown_pct": 54.2, "max_drawdown_usd": 98155, "months_to_recover": 25.5}],
    "retirement_plan": {"status": "saving", "target_nest_egg_usd": 1974359, "scenarios": [
        {"scenario": "middle", "net_return_pct": 3.95, "projected_at_retirement_usd": 1251118, "gap_usd": 723241}]},
    "pause_check": {
        "proposed_action": "Sell all of the whole portfolio to cash",
        "before": {"total_usd": 181152, "cash_pct": 0.7, "top_companies_pct": {"AAPL": 13.3},
                   "top_sectors_pct": {"Technology": 56.6}, "max_drawdown_pct": {"gfc": 54.2},
                   "max_drawdown_usd": {"gfc": 98155}},
        "after": {"total_usd": 181152, "cash_pct": 100.0, "top_companies_pct": {}, "top_sectors_pct": {"Cash": 100.0},
                  "max_drawdown_pct": {"gfc": 0.0}, "max_drawdown_usd": {"gfc": 0}},
        "market_history": [{"event": "2008-09 financial crisis (SPY)", "peak_to_bottom_drop_pct": 55.2,
                            "bottom_date": "2009-03-09", "gain_1y_after_bottom_pct": 71.9,
                            "gain_3y_after_bottom_pct": 114.3, "months_bottom_to_recovery": 41.3}],
    },
}

ANALYST_REPLY = {"findings": [
    {"title": "Hidden Apple", "why_it_matters": "Apple shows up in several funds.",
     "fact_keys": ["look_through.top_companies[0].pct"], "dollar_fact_key": "look_through.top_companies[0].usd"},
    {"title": "Retirement gap", "why_it_matters": "The middle scenario falls short.",
     "fact_keys": ["retirement_plan.scenarios[0].gap_usd"], "dollar_fact_key": "retirement_plan.scenarios[0].gap_usd"},
    {"title": "Fees over 30 years", "why_it_matters": "Small fees compound.",
     "fact_keys": ["fees.projection[1].fees_cost_usd"], "dollar_fact_key": "fees.projection[1].fees_cost_usd"},
    {"title": "Crash replay", "why_it_matters": "A 2008 repeat would hurt.",
     "fact_keys": ["crash_replays[0].max_drawdown_usd"], "dollar_fact_key": "crash_replays[0].max_drawdown_usd"},
    {"title": "Made up", "why_it_matters": "Cites nothing real.", "fact_keys": ["nope.not_a_key"], "dollar_fact_key": None},
]}

GOOD_DRAFT = {
    "cards": [{"title": "Hidden Apple",
               "body": "Counting funds, Apple is 13.3% of the portfolio ($24,068); 7.6% is held directly.",
               "fact_keys": ["look_through.top_companies[0].pct", "look_through.top_companies[0].usd"]},
              {"title": "Crash replay",
               "body": "A repeat of the 2008-09 financial crisis would have meant a 54.2% fall, about $98,000.",
               "fact_keys": ["crash_replays[0].max_drawdown_pct"]}],
    "advisor_questions": ["How does my Apple exposure fit my goals?", "Should I keep this many overlapping funds?"],
}
BAD_NUMBER_DRAFT = {**GOOD_DRAFT, "cards": [{"title": "Hidden Apple", "body": "Apple is 15% of the portfolio.",
                                             "fact_keys": ["look_through.top_companies[0].pct"]}]}
ADVICE_DRAFT = {**GOOD_DRAFT, "cards": [{"title": "Hidden Apple", "body": "Apple is 13.3%. You should sell some Apple.",
                                         "fact_keys": ["look_through.top_companies[0].pct"]}]}

PASS = {"passed": True, "problems": []}
FAIL = {"passed": False, "problems": ["Treats the crash replay as a forecast."]}

NEUTRAL_PAUSE = {"summary": "Selling everything would move the portfolio to 100.0% cash.",
                 "points": ["In the 2008-09 replay the worst fall goes from 54.2% to 0.0%.",
                            "After the 2009 bottom the market rose 71.9% in a year."]}
JUDGING_PAUSE = {"summary": "Selling everything now would be a mistake.", "points": ["It fell 54.2%."]}


def reply(obj) -> object:
    return fake_completion(json.dumps(obj), prompt_tokens=800, completion_tokens=300)
