import pytest

from secondlook.safety import advice_guard
from secondlook.safety.numbers import extract_numbers, fact_numbers, unmatched_numbers

FLAGGED = [
    ("You should sell your Apple shares.", "directive"),
    ("We recommend moving into bonds.", "directive"),
    ("It is recommended that you rebalance.", "directive"),
    ("Investors must buy the dip.", "directive"),
    ("Don't sell now.", "directive"),
    ("Now is the time to get out.", "directive"),
    ("Sell AAPL and buy VTI.", "directive"),
    ("Buy more VOO.", "directive"),
    ("Hold your shares through the storm.", "directive"),
    ("Analysts have a strong buy on NVDA.", "rating"),
    ("Our price target is $250.", "price_target"),
    ("NVDA could hit $300 next year.", "price_target"),
    ("This fund is a guaranteed winner.", "promise"),
    ("Treasuries are risk-free.", "promise"),
    ("Index funds are a no-brainer.", "promise"),
    ("The market will recover by next year.", "prediction"),
    ("Tech stocks are going to crash.", "prediction"),
    ("You will earn 7% a year.", "return_promise"),
    ("Selling everything would be a mistake.", "judgment"),
    ("That is a smart move.", "judgment"),
]

NEUTRAL = [
    "Apple is 13.3% of the portfolio once funds are counted.",
    "In the 2008-09 replay, the portfolio fell 54.2% at the worst point.",
    "People who sold at the 2009 bottom missed the 71.9% rise in the following year.",
    "Funds charge a weighted 0.05% a year.",
    "Hold on to this idea: costs compound.",
    "The S&P 500 fell sharply during the sell-off.",
    "This is a description of past data, not a forecast.",
    "In the middle scenario, the projection is $1,251,118.",
]


@pytest.mark.parametrize("text, rule", FLAGGED)
def test_flags_advice(text, rule):
    hits = advice_guard.check(text)
    assert rule in {h.rule for h in hits}, text
    assert not advice_guard.is_safe(text)


@pytest.mark.parametrize("text", NEUTRAL)
def test_neutral_text_passes(text):
    assert advice_guard.check(text) == [], text


def test_questions_may_name_holdings_but_not_promise():
    q = "Should I keep GFACX given its 1.35% expense ratio?"
    assert advice_guard.is_safe(q, allow_questions=True)
    assert advice_guard.is_safe("Would you recommend selling some QQQ?", allow_questions=True)
    assert not advice_guard.is_safe("Is this fund guaranteed to beat the market?", allow_questions=True)
    assert not advice_guard.is_safe("You should sell QQQ. Do you agree?", allow_questions=True)


def test_hit_reason_quotes_text():
    (hit,) = advice_guard.check("Honestly, you should sell AAPL today.")[:1]
    assert hit.rule == "directive" and "should sell" in hit.reason()


# --- numbers -----------------------------------------------------------------

@pytest.mark.parametrize("text, value, tolerance, kind", [
    ("13.3%", 13.3, 0.05, "pct"),
    ("13%", 13.0, 0.5, "pct"),
    ("1.35 percent", 1.35, 0.005, "pct"),
    ("$24,068", 24068, 0.5, "usd"),
    ("$98,000", 98000, 500, "usd"),
    ("$98k", 98000, 500, "usd"),
    ("$1.2 million", 1_200_000, 50_000, "usd"),
    ("25.5", 25.5, 0.05, "plain"),
    ("41", 41, 0.5, "plain"),
])
def test_extract_numbers(text, value, tolerance, kind):
    (n,) = extract_numbers(text)
    assert (n.value, n.tolerance, n.kind) == (pytest.approx(value), pytest.approx(tolerance), kind)


def test_extract_skips_names_years_and_small_counts():
    text = "Your 401(k) and 403(b) hold an S&P 500 fund; in 2008 and 2020, 3 funds overlapped for 10 years."
    assert extract_numbers(text) == []


def test_fact_numbers_include_numbers_inside_strings():
    facts = {"a": 1.5, "b": [{"c": -723241}], "proxies": {"VTTSX": "55% SPY + 36% EFA"}, "flag": True}
    assert sorted(fact_numbers(facts)) == [1.5, 36.0, 55.0, 723241.0]


def test_unmatched_numbers():
    facts = {"pct": 13.3, "usd": 98155, "gap_usd": -723241}
    assert unmatched_numbers("Apple is 13.3% (about 13%), worst fall about $98,000 or $98k.", facts) == []
    assert unmatched_numbers("A gap of $723,241.", facts) == []          # sign ignored
    assert unmatched_numbers("Apple is 15% and fees are $1,500.", facts) == ["15%", "$1,500"]
    assert unmatched_numbers("About $97,000.", facts) == ["$97,000"]     # outside the $500 rounding
    assert unmatched_numbers("Down 40%.", {"pct": 41.3}) == ["40%"]      # small round numbers are exact
    assert unmatched_numbers("Down 41%.", {"pct": 41.3}) == []


@pytest.mark.parametrize("text", [
    "This is a scenario estimate, not a guaranteed outcome.",
    "Projections are based on assumptions, not guarantees.",
    "Bonds are not risk-free.",
    "There are no guarantees in investing.",
])
def test_negated_promises_pass(text):
    assert advice_guard.is_safe(text), text


def test_promise_still_caught_after_a_negated_one():
    assert not advice_guard.is_safe("Not guaranteed today, but this fund is a guaranteed winner long term.")
