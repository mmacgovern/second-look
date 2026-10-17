import pytest

from quant_toys import TOY_PROFILES, TOY_VALUES
from secondlook.quant.concentration import concentration, inverse_hhi, reference_sectors, stock_sectors

REFERENCE = {"technology": 0.5, "healthcare": 0.3, "financial_services": 0.2}


@pytest.fixture
def toy():
    return concentration(TOY_VALUES, TOY_PROFILES, reference=REFERENCE)


def test_largest_company(toy):
    assert toy.largest_company.symbol == "AAA"
    assert toy.largest_company.weight == pytest.approx(0.54)


def test_effective_holdings_is_inverse_hhi_by_hand(toy):
    # positions 4,000 / 2,000 / 3,000 (cash left out): HHI = (16 + 4 + 9) / 81 -> 81 / 29 = 2.79
    assert toy.effective_holdings == pytest.approx(81 / 29)
    assert inverse_hhi([1.0] * 10) == pytest.approx(10)
    assert inverse_hhi([]) is None


def test_effective_companies_by_hand(toy):
    # look-through over invested 0.9: AAA .6, BBB .1333, CCC .0889 -> 1 / (.36 + .01778 + .00790) = 2.593
    assert toy.effective_companies_upper_bound == pytest.approx(2.5928297)


def test_flags_over_threshold(toy):
    # AAA 54% (30% held directly), BBB 12% (all via FUNDA); CCC 8% is under 10%
    assert [(f.ticker, round(f.weight, 4), round(f.direct_weight, 4)) for f in toy.flags] == [
        ("AAA", 0.54, 0.30), ("BBB", 0.12, 0.0)]
    stricter = concentration(TOY_VALUES, TOY_PROFILES, reference=REFERENCE, threshold=0.2)
    assert [f.ticker for f in stricter.flags] == ["AAA"]


def test_sectors_vs_market_by_hand(toy):
    # stock part: tech .64, health .16, energy .10 of 0.90 -> 71.1%, 17.8%, 11.1%
    got = {s.sector: (s.portfolio, s.reference, s.difference) for s in toy.sectors_vs_market}
    assert got["technology"] == pytest.approx((0.64 / 0.9, 0.5, 0.64 / 0.9 - 0.5))
    assert got["healthcare"] == pytest.approx((0.16 / 0.9, 0.3, 0.16 / 0.9 - 0.3))
    assert got["energy"] == pytest.approx((0.10 / 0.9, 0.0, 0.10 / 0.9))
    assert [s.sector for s in toy.sectors_vs_market] == ["technology", "healthcare", "energy"]


def test_stock_sectors_drops_bonds_cash_other():
    assert stock_sectors({"technology": 0.3, "bonds": 0.5, "cash": 0.1, "energy": 0.1}) == pytest.approx(
        {"technology": 0.75, "energy": 0.25})
    assert stock_sectors({"bonds": 1.0}) == {}


def test_all_cash_portfolio():
    c = concentration({"CASH": 100.0}, {}, reference=REFERENCE)
    assert c.largest_company is None and c.flags == [] and c.effective_holdings is None
    assert c.sectors_vs_market == []


def test_total_market_reference_from_snapshot():
    ref = reference_sectors()
    assert sum(ref.values()) == pytest.approx(1.0)
    assert max(ref, key=ref.get) == "technology"
