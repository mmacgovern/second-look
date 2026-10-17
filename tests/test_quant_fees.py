import pytest

from quant_toys import TOY_PROFILES, TOY_VALUES
from secondlook.quant.fees import fee_report


@pytest.fixture
def toy():
    return fee_report(TOY_VALUES, TOY_PROFILES)


def test_weighted_expense_ratio_by_hand(toy):
    # FUNDA 4,000 x 0.10% = $4; FUNDB 2,000 x 1.00% = $20; AAA and cash 0%
    # weighted = 24 / 10,000 = 0.24%
    assert toy.annual_cost_usd == pytest.approx(24.0)
    assert toy.weighted_expense_ratio == pytest.approx(0.0024)
    assert toy.covered_value_usd == 10000
    assert [f.symbol for f in toy.funds] == ["FUNDB", "FUNDA"]
    assert toy.funds[0].annual_cost_usd == pytest.approx(20.0)


@pytest.mark.parametrize("years, no_fees, baseline, actual", [
    # 10,000 x 1.06^n,  10,000 x 1.0595^n,  10,000 x 1.0576^n
    (10, 17908.48, 17824.18, 17507.11),
    (20, 32071.35, 31770.15, 30649.88),
    (30, 57434.91, 56627.69, 53659.09),
])
def test_projections_by_hand(toy, years, no_fees, baseline, actual):
    p = next(p for p in toy.projections if p.years == years)
    assert p.value_no_fees_usd == pytest.approx(no_fees, abs=0.01)
    assert p.value_baseline_usd == pytest.approx(baseline, abs=0.01)
    assert p.value_portfolio_usd == pytest.approx(actual, abs=0.01)
    assert p.cost_vs_no_fees_usd == pytest.approx(no_fees - actual, abs=0.02)
    assert p.cost_vs_baseline_usd == pytest.approx(baseline - actual, abs=0.02)


def test_gross_return_is_a_parameter():
    report = fee_report({"FUNDB": 1000.0}, TOY_PROFILES, gross_return=0.0, horizons=(1,))
    # 1,000 x (1 + 0 - 0.01) = 990  vs no fees 1,000  vs baseline 1,000 x 0.9995 = 999.50
    p = report.projections[0]
    assert (p.value_portfolio_usd, p.value_no_fees_usd, p.value_baseline_usd) == pytest.approx((990.0, 1000.0, 999.5))
    assert p.cost_vs_baseline_usd == pytest.approx(9.5)


def test_cheaper_than_baseline_is_negative():
    report = fee_report({"AAA": 1000.0}, TOY_PROFILES, horizons=(10,))
    assert report.weighted_expense_ratio == 0
    assert report.projections[0].cost_vs_baseline_usd < 0
    assert report.projections[0].cost_vs_no_fees_usd == pytest.approx(0)


def test_funds_without_expense_ratio_left_out():
    profiles = {"NOER": {"quote_type": "ETF", "expense_ratio": None}, **TOY_PROFILES}
    report = fee_report({"NOER": 5000.0, "FUNDB": 5000.0, "MYST": 100.0}, profiles)
    assert report.unknown_symbols == ["NOER", "MYST"]
    assert report.covered_value_usd == 5000
    assert report.weighted_expense_ratio == pytest.approx(0.01)


def test_retiree_sample_flags_high_cost_fund():
    from secondlook import market_data as md
    from secondlook.quant.parse import parse_csv

    values = parse_csv(md.PROJECT_ROOT / "data" / "sample_portfolios" / "retiree.csv").values_by_symbol()
    profiles = {s: md.load_snapshot_profile(s) for s in values if s != "CASH"}
    report = fee_report(values, profiles)
    assert report.funds[0].symbol == "GFACX"
    assert report.funds[0].expense_ratio == pytest.approx(0.0135)
    assert report.projections[-1].cost_vs_baseline_usd > 0
