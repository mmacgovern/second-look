import pytest

from quant_toys import TOY_PROFILES, TOY_VALUES
from secondlook.quant import xray as xr_mod
from secondlook.quant.xray import asset_mix, company_key, sector_key, sector_mix, xray


@pytest.fixture
def toy():
    return xray(TOY_VALUES, TOY_PROFILES)


def test_company_exposures_by_hand(toy):
    # AAA: direct 0.30 + FUNDA 0.40 x 0.50 + FUNDB 0.20 x 0.20 = 0.30 + 0.20 + 0.04 = 0.54
    # BBB: FUNDA 0.40 x 0.30 = 0.12      CCC: FUNDB 0.20 x 0.40 = 0.08
    got = {c.symbol: c.weight for c in toy.top_companies}
    assert got == pytest.approx({"AAA": 0.54, "BBB": 0.12, "CCC": 0.08})
    assert [c.symbol for c in toy.top_companies] == ["AAA", "BBB", "CCC"]
    aaa = toy.top_companies[0]
    assert aaa.value_usd == pytest.approx(5400)
    assert aaa.via == pytest.approx({"direct": 0.30, "FUNDA": 0.20, "FUNDB": 0.04})
    assert list(aaa.via) == ["direct", "FUNDA", "FUNDB"]


def test_sector_weights_by_hand(toy):
    # tech: AAA 0.30 + FUNDA 0.40 x 0.6 + FUNDB 0.20 x 0.5 = 0.64
    # health: 0.40 x 0.4 = 0.16; energy: 0.20 x 0.5 = 0.10; cash 0.10
    assert toy.sectors == pytest.approx({"technology": 0.64, "healthcare": 0.16, "energy": 0.10, "cash": 0.10})
    assert list(toy.sectors)[0] == "technology"
    assert sum(toy.sectors.values()) == pytest.approx(1.0)


def test_overlap_is_sum_of_min_weights(toy):
    # Shared holding AAA: min(0.50, 0.20) = 0.20
    assert len(toy.overlaps) == 1
    o = toy.overlaps[0]
    assert (o.fund_a, o.fund_b, o.overlap) == ("FUNDA", "FUNDB", pytest.approx(0.20))


def test_coverage_by_hand(toy):
    # traced: AAA 0.30 + FUNDA 0.40 x 0.80 + FUNDB 0.20 x 0.60 = 0.74; invested = 0.90
    assert toy.coverage == pytest.approx(0.74 / 0.90)
    assert toy.cash_weight == pytest.approx(0.10)
    assert toy.total_value_usd == 10000


def test_top_n_limits_list():
    assert len(xray(TOY_VALUES, TOY_PROFILES, top_n=2).top_companies) == 2


def test_overlap_three_funds_pairwise():
    profiles = {
        f: {"quote_type": "ETF", "top_holdings": [{"symbol": s, "name": s, "weight": w} for s, w in h]}
        for f, h in {"F1": [("X", 0.5), ("Y", 0.5)], "F2": [("X", 0.3), ("Y", 0.1), ("Z", 0.6)], "F3": [("Z", 1.0)]}.items()
    }
    out = {(o.fund_a, o.fund_b): o.overlap for o in xray({"F1": 1, "F2": 1, "F3": 1}, profiles).overlaps}
    # F1-F2: min(.5,.3) + min(.5,.1) = 0.4; F2-F3: min(.6, 1) = 0.6; F1-F3: nothing shared
    assert out == pytest.approx({("F2", "F3"): 0.6, ("F1", "F2"): 0.4, ("F1", "F3"): 0.0})


def test_share_classes_combined_and_tickers_normalised():
    assert company_key("GOOG") == "GOOGL" and company_key("brk-b") == "BRK.B"
    profiles = {
        "F": {"quote_type": "ETF", "top_holdings": [{"symbol": "GOOGL", "name": "Alphabet A", "weight": 0.1},
                                                     {"symbol": "GOOG", "name": "Alphabet C", "weight": 0.1}]},
        "GOOGL": {"quote_type": "EQUITY", "name": "Alphabet Inc.", "sector": "Communication Services"},
    }
    out = xray({"F": 500, "GOOGL": 500}, profiles)
    # 0.5 direct + 0.5 x (0.1 + 0.1) = 0.6
    assert out.top_companies[0].symbol == "GOOGL"
    assert out.top_companies[0].weight == pytest.approx(0.6)


def test_fund_of_funds_expands_known_subfunds_and_skips_unknown():
    profiles = {
        "TDF": {"quote_type": "MUTUALFUND", "top_holdings": [
            {"symbol": "SUB", "name": "Total Stock Index Fund", "weight": 0.6},
            {"symbol": "BISXX", "name": "BlackRock Cash Funds Instl", "weight": 0.4}]},
        "SUB": {"quote_type": "ETF", "top_holdings": [{"symbol": "AAA", "name": "Aaa", "weight": 0.5}]},
    }
    out = xray({"TDF": 100}, profiles)
    # AAA = 1.0 x 0.6 x 0.5 = 0.30; BISXX is a fund we cannot look into, so it is not a company
    assert {c.symbol: c.weight for c in out.top_companies} == pytest.approx({"AAA": 0.30})
    assert out.coverage == pytest.approx(0.30)


def test_asset_mix_clips_negative_cash_and_rescales():
    mix = asset_mix({"asset_classes": {"stockPosition": 1.0004, "cashPosition": -0.0012, "otherPosition": 0.0007}})
    assert mix["cash"] == 0 and sum(mix.values()) == pytest.approx(1.0)
    assert mix["stock"] == pytest.approx(1.0004 / 1.0011)


def test_sector_mix_for_balanced_bond_and_gold_funds():
    balanced = {"quote_type": "MUTUALFUND", "sector_weights": {"technology": 0.5, "energy": 0.5},
                "asset_classes": {"stockPosition": 0.8, "bondPosition": 0.15, "cashPosition": 0.05}}
    assert sector_mix(balanced) == pytest.approx({"technology": 0.4, "energy": 0.4, "bonds": 0.15, "cash": 0.05})
    bond = {"quote_type": "ETF", "asset_classes": {"bondPosition": 0.99, "cashPosition": 0.01}}
    assert sector_mix(bond) == pytest.approx({"bonds": 0.99, "cash": 0.01})
    gold = {"quote_type": "ETF", "asset_classes": {"otherPosition": 1.0}}
    assert sector_mix(gold) == {"other": 1.0}
    assert sector_mix(None) == {"unclassified": 1.0}


def test_sector_key_matches_fund_style_keys():
    assert sector_key("Communication Services") == "communication_services"
    assert sector_key("Real Estate") == "realestate"
    assert sector_key(None) == "unclassified"


def test_funds_without_holdings_and_unknown_symbols_reported():
    profiles = {"BND": {"quote_type": "ETF", "top_holdings": [], "asset_classes": {"bondPosition": 1.0}}}
    out = xray({"BND": 600, "MYST": 400}, profiles)
    assert out.funds_without_holdings == ["BND"] and out.unknown_symbols == ["MYST"]
    assert out.coverage == 0 and out.top_companies == []
    assert out.sectors == pytest.approx({"bonds": 0.6, "unclassified": 0.4})


def test_empty_portfolio_raises():
    with pytest.raises(ValueError):
        xray({}, {})


def test_midcareer_sample_shows_hidden_concentration():
    from secondlook import market_data as md
    from secondlook.quant.parse import parse_csv

    values = parse_csv(md.PROJECT_ROOT / "data" / "sample_portfolios" / "midcareer.csv").values_by_symbol()
    profiles = {s: md.load_snapshot_profile(s) for s in values if s != "CASH"}
    out = xray(values, profiles)
    aapl = next(c for c in out.top_companies if c.symbol == "AAPL")
    assert aapl.weight > aapl.via["direct"]                     # funds add hidden Apple on top of the shares
    assert {"VOO", "VTI", "QQQ", "XLK"} <= set(aapl.via)
    assert out.overlaps[0].overlap > 0.25
    assert 0 < out.coverage < 1
    assert xr_mod.SECTOR_LABELS[next(iter(out.sectors))] == "Technology"
