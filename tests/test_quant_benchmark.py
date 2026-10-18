"""secondlook/quant/benchmark.py: top-10 and sector math against the S&P 500 reference."""

import pytest

from quant_toys import TOY_PROFILES, TOY_VALUES
from secondlook.quant import benchmark as bm
from secondlook.quant.xray import xray

BENCH = bm.Benchmark(
    fund="IDX", fund_name="Toy Index Fund", as_of="2026-10-17", source="test",
    top_holdings=[{"symbol": "AAA", "name": "AAA Corp", "weight": 0.10},
                  {"symbol": "GOOG", "name": "Alphabet C", "weight": 0.02},
                  {"symbol": "GOOGL", "name": "Alphabet A", "weight": 0.03}],
    sector_weights={"Information Technology": 0.5, "Health Care": 0.3, "Financials": 0.2},
)


@pytest.fixture
def xr():
    return xray(TOY_VALUES, TOY_PROFILES, top_n=1_000_000)


def test_top_ten_look_through_sorted_and_summed(xr):
    top = bm.top_ten(xr, BENCH)
    weights = [r.weight for r in top.rows]
    assert weights == sorted(weights, reverse=True) and len(top.rows) <= 10
    assert top.weight == pytest.approx(sum(weights))
    assert top.rows[0].symbol == "AAA" and top.rows[0].weight == pytest.approx(5400 / sum(TOY_VALUES.values()))
    assert top.rows[0].sp500_weight == 0.10 and top.sp500_weight == pytest.approx(0.15)


def test_direct_only_counts_direct_shares(xr):
    direct = bm.top_ten(xr, BENCH, look_through=False)
    assert direct.mode == "direct"
    for r in direct.rows:
        company = next(c for c in xr.top_companies if c.symbol == r.symbol)
        assert r.weight == pytest.approx(company.via["direct"])
    assert direct.weight <= bm.top_ten(xr, BENCH).weight


def test_share_classes_combined_and_missing_is_none():
    assert BENCH.weight_of("GOOGL") == pytest.approx(0.05) and BENCH.weight_of("goog") == pytest.approx(0.05)
    assert BENCH.weight_of("ZZZ") is None


def test_stock_sector_weights_sum_to_100_and_use_gics(xr):
    mine = bm.stock_sector_weights(xr)
    assert sum(mine.values()) == pytest.approx(1.0)
    assert set(mine) <= set(bm.GICS.values())
    assert list(mine.values()) == sorted(mine.values(), reverse=True)


def test_donut_groups_small_slices_and_still_sums_to_100():
    slices = bm.donut_slices({"Information Technology": 0.6, "Health Care": 0.35, "Energy": 0.03, "Utilities": 0.02},
                             min_pct=3.0)
    assert slices == {"Information Technology": 0.6, "Health Care": 0.35, "Energy": 0.03, "Other": 0.02}
    assert sum(slices.values()) == pytest.approx(1.0)


def test_sector_rows_cover_all_11_sorted_by_difference(xr):
    rows = bm.sector_rows(xr, BENCH)
    assert [r.sector for r in rows if True] and len(rows) == 11 == len(set(r.sector for r in rows))
    diffs = [abs(r.difference) for r in rows]
    assert diffs == sorted(diffs, reverse=True)
    assert sum(r.weight for r in rows) == pytest.approx(1.0) and sum(r.sp500_weight for r in rows) == pytest.approx(1.0)


def test_comparison_sentences_are_neutral():
    row = bm.SectorRow(sector="Information Technology", weight=0.6, sp500_weight=0.25)
    assert bm.comparison_sentence(row) == "You hold 2.4x the S&P 500's weight in Information Technology."
    assert "no Energy" in bm.comparison_sentence(bm.SectorRow(sector="Energy", weight=0.0, sp500_weight=0.03))
    for text in (bm.comparison_sentence(row),):
        assert not any(w in text.lower() for w in ("should", "buy", "sell", "rebalance", "too much", "better"))


def test_benchmark_from_profile_normalises_to_gics():
    data = bm.benchmark_from_profile({"ticker": "VOO", "name": "Vanguard S&P 500 ETF", "as_of": "2026-10-17",
                                      "top_holdings": [{"symbol": "AAA", "name": "AAA", "weight": 0.1}],
                                      "sector_weights": {"technology": 0.3, "financial_services": 0.1}}, "yfinance")
    assert data["sector_weights"] == {"Information Technology": 0.75, "Financials": 0.25}
    assert data["fund"] == "VOO" and data["as_of"] == "2026-10-17"


def test_facts_block_numbers_match_the_math(xr):
    block = bm.facts_block(xr, BENCH)
    assert block["top10_companies_pct_of_portfolio"] == round(100 * bm.top_ten(xr, BENCH).weight, 1)
    assert block["sp500_top10_pct"] == 15.0 and "Toy Index Fund" in block["reference"]
    first = bm.sector_rows(xr, BENCH)[0]
    assert block["sectors_pct_of_stocks_vs_sp500"][0]["sector"] == first.sector


def test_real_reference_file_is_valid():
    real = bm.load_benchmark()
    assert real.fund in {"VOO", "SPY"} and real.as_of and real.fund_name
    assert sum(real.sector_weights.values()) == pytest.approx(1.0, abs=1e-4)
    assert set(real.sector_weights) <= set(bm.GICS.values()) and len(real.top_holdings) >= 10
