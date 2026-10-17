from types import SimpleNamespace

import pandas as pd
import pytest

from secondlook import market_data as md
from secondlook.market_data import DataUnavailable


def frame(start="2008-09-01", periods=5, base=100.0):
    idx = pd.date_range(start, periods=periods, freq="B", name="date")
    values = [base + i for i in range(periods)]
    return pd.DataFrame({"close": values, "adj_close": [v * 0.9 for v in values]}, index=idx)


def failing_fetch(*_args, **_kwargs):
    raise ConnectionError("offline")


@pytest.fixture
def snap(tmp_path):
    md.save_prices("VOO", frame(base=200), tmp_path)
    md.save_prices("^GSPC", frame(base=1200), tmp_path)
    md.save_profile({"ticker": "VOO", "name": "Snapshot VOO", "quote_type": "ETF"}, tmp_path)
    return tmp_path


def test_file_stem_makes_index_tickers_safe():
    assert md.file_stem("^GSPC") == "_GSPC"
    assert md.file_stem("brk.b") == "BRK.B"


def test_prefers_live_data(snap):
    live = lambda t, s, e: frame(base=999)
    data = md.get_prices(["VOO"], fetch=live, snapshot_dir=snap)
    assert data.sources == {"VOO": "live"}
    assert data.close["VOO"].iloc[0] == 999


def test_falls_back_to_snapshot_when_live_fails(snap):
    data = md.get_prices(["VOO", "^GSPC"], fetch=failing_fetch, snapshot_dir=snap)
    assert data.sources == {"VOO": "snapshot", "^GSPC": "snapshot"}
    assert data.close["^GSPC"].iloc[0] == 1200
    assert data.adj_close["VOO"].iloc[0] == pytest.approx(180)


def test_prefer_live_false_never_calls_live(snap):
    def must_not_run(*_):
        raise AssertionError("live fetch called")

    assert md.get_prices(["VOO"], prefer_live=False, fetch=must_not_run, snapshot_dir=snap).sources["VOO"] == "snapshot"


def test_missing_everywhere_is_reported_not_raised(snap):
    data = md.get_prices(["VOO", "NOPE", "CASH"], fetch=failing_fetch, snapshot_dir=snap)
    assert data.missing == ["NOPE"]
    assert "CASH" not in data.sources
    assert list(data.close.columns) == ["VOO"]


def test_snapshot_is_sliced_to_requested_dates(snap):
    df, source = md.get_ticker_prices("VOO", "2008-09-03", "2008-09-04", fetch=failing_fetch, snapshot_dir=snap)
    assert source == "snapshot" and len(df) == 2


def test_duplicate_and_lowercase_tickers_fetched_once(snap):
    calls = []

    def live(t, s, e):
        calls.append(t)
        return frame()

    md.get_prices(["voo", "VOO"], fetch=live, snapshot_dir=snap)
    assert calls == ["VOO"]


def test_profile_live_then_snapshot(snap):
    profile, source = md.get_profile("VOO", fetch=lambda t: {"ticker": t, "name": "Live"}, snapshot_dir=snap)
    assert (profile["name"], source) == ("Live", "live")
    profile, source = md.get_profile("VOO", fetch=failing_fetch, snapshot_dir=snap)
    assert (profile["name"], source) == ("Snapshot VOO", "snapshot")
    with pytest.raises(DataUnavailable):
        md.get_profile("NOPE", fetch=failing_fetch, snapshot_dir=snap)


def test_expense_ratio_prefers_annual_report_then_net_percent():
    ops = pd.DataFrame({"VOO": [0.00029999999, 0.02], "Category Average": [0.007, 0.9]},
                       index=["Annual Report Expense Ratio", "Annual Holdings Turnover"])
    assert md.expense_ratio_from({}, ops, "VOO") == 0.0003
    # netExpenseRatio is reported in percent by yfinance
    assert md.expense_ratio_from({"netExpenseRatio": 1.35}, None, "GFACX") == pytest.approx(0.0135)
    assert md.expense_ratio_from({}, None, "AAPL") is None


class FakeTicker:
    def __init__(self, info, funds_data=None):
        self.info = info
        self.funds_data = funds_data


def test_fetch_profile_live_parses_fund_data():
    holdings = pd.DataFrame({"Name": ["NVIDIA Corp", "Apple Inc"], "Holding Percent": [0.155, 0.136]},
                            index=pd.Index(["NVDA", "AAPL"], name="Symbol"))
    ops = pd.DataFrame({"XLK": [0.0008]}, index=["Annual Report Expense Ratio"])
    fd = SimpleNamespace(top_holdings=holdings, fund_operations=ops,
                         sector_weightings={"technology": 0.99}, asset_classes={"stockPosition": 1.0})
    info = {"quoteType": "ETF", "longName": "Technology Select Sector SPDR Fund"}
    profile = md.fetch_profile_live("XLK", ticker_factory=lambda t: FakeTicker(info, fd))
    assert profile["expense_ratio"] == 0.0008
    assert profile["top_holdings"][0] == {"symbol": "NVDA", "name": "NVIDIA Corp", "weight": 0.155}
    assert profile["sector_weights"] == {"technology": 0.99}


def test_fetch_profile_live_stock_and_partial_fund_data():
    stock = md.fetch_profile_live("AAPL", ticker_factory=lambda t: FakeTicker(
        {"quoteType": "EQUITY", "longName": "Apple Inc.", "sector": "Technology"}))
    assert stock["sector"] == "Technology" and stock["top_holdings"] == []

    class Broken:
        @property
        def top_holdings(self):
            raise ValueError("not available")

        fund_operations = None
        sector_weightings = None
        asset_classes = None

    bond = md.fetch_profile_live("BND", ticker_factory=lambda t: FakeTicker(
        {"quoteType": "ETF", "netExpenseRatio": 0.03}, Broken()))
    assert bond["top_holdings"] == [] and bond["expense_ratio"] == pytest.approx(0.0003)


def test_fetch_profile_live_without_info_raises():
    with pytest.raises(DataUnavailable):
        md.fetch_profile_live("ZZZZ", ticker_factory=lambda t: FakeTicker({}))


def test_real_snapshot_has_every_sample_and_proxy_ticker():
    import json

    manifest = json.loads((md.SNAPSHOT_DIR / "manifest.json").read_text(encoding="utf-8"))
    for ticker in ["VOO", "QQQ", "XLK", "GFACX", "VTTSX", *md.PROXIES]:
        assert md.prices_path(ticker).exists(), ticker
        assert md.profile_path(ticker).exists(), ticker
    assert manifest["failures"] == []
    voo = md.load_snapshot_prices("VOO")
    assert voo.index.min() >= pd.Timestamp("2007-01-01") and {"close", "adj_close"} <= set(voo.columns)
