import pytest

from secondlook.quant.plan import ASSUMPTIONS, future_value, make_plan, project, required_contribution, required_nest_egg
from secondlook.quant.profile import UserProfile


def excel_pmt(rate, nper, pv, fv=0.0, when=0):
    """Spreadsheet PMT (Excel / Google Sheets definition), written independently of plan.py."""
    if rate == 0:
        return -(pv + fv) / nper
    growth = (1 + rate) ** nper
    return -(rate * (fv + pv * growth)) / ((growth - 1) * (1 + rate * when))


def user(age=40, retirement_age=65, income=80000, ss=30000, contribution=10000):
    return UserProfile(age=age, retirement_age=retirement_age, desired_income_today_usd=income,
                       social_security_or_pension_usd=ss, annual_contribution_usd=contribution)


def test_required_nest_egg_by_hand():
    # (80,000 - 30,000) / 0.039 = 1,282,051.28
    assert required_nest_egg(80000, 30000, 0.039) == pytest.approx(1282051.28, abs=0.01)
    assert required_nest_egg(80000, 30000, 0.045) == pytest.approx(1111111.11, abs=0.01)
    assert required_nest_egg(30000, 40000, 0.039) == 0  # SS covers everything


@pytest.mark.parametrize("rate", [0.034, 0.046, 0.0])
def test_withdrawal_rate_range_enforced(rate):
    with pytest.raises(ValueError):
        required_nest_egg(80000, 0, rate)


def test_projection_by_year_by_hand():
    # B1 = 100,000 x 1.05 + 10,000 = 115,000; B2 = 115,000 x 1.05 + 10,000 = 130,750
    path = project(100000, 10000, 0.05, 2, start_age=63)
    assert [(p.year, p.age, p.balance_usd) for p in path] == [
        (0, 63, 100000), (1, 64, pytest.approx(115000)), (2, 65, pytest.approx(130750))]
    assert future_value(100000, 10000, 0.05, 2) == pytest.approx(130750)
    assert future_value(1000, 100, 0.0, 3) == 1300


def test_required_contribution_matches_spreadsheet_pmt():
    # =PMT(5%, 20, -100000, 1000000) returns -22,218.33 (a payment out each year)
    assert excel_pmt(0.05, 20, -100000, 1000000) == pytest.approx(-22218.33, abs=0.01)
    assert required_contribution(1000000, 100000, 0.05, 20) == pytest.approx(22218.33, abs=0.01)
    for target, balance, rate, years in [(1282051.28, 50000, 0.04, 25), (500000, 0, 0.03, 30), (800000, 250000, -0.01, 10)]:
        assert required_contribution(target, balance, rate, years) == pytest.approx(
            -excel_pmt(rate, years, -balance, target), rel=1e-12)


def test_required_contribution_edge_cases():
    # zero real return: (120,000 - 20,000) / 10 = 10,000 = -PMT(0, 10, -20000, 120000)
    assert required_contribution(120000, 20000, 0.0, 10) == 10000 == -excel_pmt(0, 10, -20000, 120000)
    assert required_contribution(100, 1000, 0.05, 10) == 0.0      # balance alone gets there
    assert required_contribution(100000, 0, 0.05, 0) is None      # no years left


def test_contributing_the_required_amount_hits_the_target():
    pmt = required_contribution(1000000, 100000, 0.05, 20)
    assert project(100000, pmt, 0.05, 20, 45)[-1].balance_usd == pytest.approx(1000000)


def test_make_plan_scenarios_by_hand():
    # target = 50,000 / 0.039; balance 100,000; contribution 10,000; 25 years; ER 0.25% subtracted
    plan = make_plan(user(), 100000, expense_ratio=0.0025, real_returns={"cautious": 0.03, "middle": 0.05})
    assert plan.status == "saving" and plan.years_to_retirement == 25
    assert plan.target_nest_egg_usd == pytest.approx(1282051.28, abs=0.01)
    cautious, middle = plan.scenarios
    assert cautious.net_return == pytest.approx(0.0275) and middle.net_return == pytest.approx(0.0475)
    # middle: 100,000 x 1.0475^25 + 10,000 x (1.0475^25 - 1) / 0.0475
    g = 1.0475 ** 25
    expected = 100000 * g + 10000 * (g - 1) / 0.0475
    assert middle.projected_at_retirement_usd == pytest.approx(expected)
    assert middle.gap_usd == pytest.approx(plan.target_nest_egg_usd - expected)
    assert len(middle.balances) == 26 and middle.balances[-1].age == 65
    assert middle.required_annual_contribution_usd == pytest.approx(-excel_pmt(0.0475, 25, -100000, plan.target_nest_egg_usd))
    assert middle.supported_income_usd == pytest.approx(expected * 0.039 + 30000)
    assert cautious.projected_at_retirement_usd < middle.projected_at_retirement_usd


def test_already_on_track():
    plan = make_plan(user(contribution=0), 2_000_000, real_returns={"middle": 0.04})
    s = plan.scenarios[0]
    assert s.on_track and s.gap_usd < 0 and s.required_annual_contribution_usd == 0.0


def test_social_security_covers_goal():
    plan = make_plan(user(income=30000, ss=35000), 0, real_returns={"middle": 0.04})
    assert plan.target_nest_egg_usd == 0 and plan.scenarios[0].on_track
    assert plan.income_needed_from_portfolio_usd == 0


def test_already_retired():
    plan = make_plan(user(age=70, retirement_age=65, contribution=5000), 600000)
    assert plan.status == "retired" and plan.years_to_retirement == 0
    assert plan.annual_contribution_usd == 0  # no contributions once retired
    for s in plan.scenarios:
        assert len(s.balances) == 1 and s.projected_at_retirement_usd == 600000
        assert s.required_annual_contribution_usd is None
        assert s.gap_usd == pytest.approx(plan.target_nest_egg_usd - 600000)
        assert s.supported_income_usd == pytest.approx(600000 * 0.039 + 30000)


def test_zero_years_left():
    plan = make_plan(user(age=65, retirement_age=65), 500000)
    assert plan.status == "retiring_now"
    assert all(s.required_annual_contribution_usd is None for s in plan.scenarios)


def test_assumptions_reported_with_overrides():
    plan = make_plan(user(), 100000, withdrawal_rate=0.035, real_returns={"x": 0.01}, tax_rate=0.2)
    assert plan.assumptions["withdrawal_rate"] == 0.035
    assert plan.assumptions["real_returns"] == {"x": 0.01}
    assert plan.assumptions["retirement_tax_rate"] == 0.2
    assert set(ASSUMPTIONS["real_returns"]) == {"cautious", "middle", "strong"}
