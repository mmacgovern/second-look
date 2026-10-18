import importlib.util
import json
from pathlib import Path

import pandas as pd

from secondlook import market_data as md

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "build_snapshot.py"
spec = importlib.util.spec_from_file_location("build_snapshot", SCRIPT)
build_snapshot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build_snapshot)


def fake_prices(ticker, start, end):
    if ticker == "BROKEN":
        raise ConnectionError("no data")
    idx = pd.date_range("2007-01-03", periods=3, freq="B", name="date")
    return pd.DataFrame({"close": [1.0, 2.0, 3.0], "adj_close": [1.0, 2.0, 3.0]}, index=idx)


def fake_profile(ticker):
    return {"ticker": ticker, "name": f"{ticker} Fund", "quote_type": "ETF", "expense_ratio": 0.001, "as_of": "2026-10-17",
            "top_holdings": [{"symbol": "X", "name": "X Corp", "weight": 0.07}],
            "sector_weights": {"technology": 0.6, "healthcare": 0.4}}


def test_sample_tickers_reads_all_csvs_and_skips_cash():
    tickers = build_snapshot.sample_tickers()
    assert "CASH" not in tickers
    assert {"VTTSX", "VOO", "VTI", "QQQ", "XLK", "GFACX", "BND"} <= set(tickers)
    assert len(tickers) == len(set(tickers))


def test_build_writes_files_and_manifest(tmp_path):
    manifest = build_snapshot.build(["VOO", "BROKEN"], tmp_path, fetch_prices=fake_prices, fetch_profile=fake_profile)
    assert md.prices_path("VOO", tmp_path).exists()
    assert md.profile_path("BROKEN", tmp_path).exists()  # profile still saved when prices fail
    on_disk = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert on_disk["tickers"]["VOO"]["rows"] == 3
    assert on_disk["tickers"]["VOO"]["first_date"] == "2007-01-03"
    assert manifest["failures"] == [{"ticker": "BROKEN", "part": "prices", "error": "ConnectionError('no data')"}]


def test_main_includes_samples_proxies_and_extras(tmp_path):
    assert build_snapshot.main(
        ["--out", str(tmp_path), "--tickers", "arkk"], fetch_prices=fake_prices, fetch_profile=fake_profile
    ) == 0
    built = set(json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))["tickers"])
    assert {"VOO", "^GSPC", "AGG", "ARKK"} <= built
    assert (tmp_path / "prices" / "_GSPC.parquet").exists()
    bench = json.loads((tmp_path / "sp500_benchmark.json").read_text(encoding="utf-8"))  # not data/reference
    assert bench["fund"] == "VOO" and bench["source"] == "yfinance"


def test_benchmark_falls_back_to_snapshot(tmp_path):
    md.save_profile(fake_profile("VOO"), tmp_path)
    out = tmp_path / "ref.json"

    def broken(ticker):
        raise ConnectionError("offline")

    data = build_snapshot.write_benchmark(out, fetch_profile=broken, snapshot_dir=tmp_path)
    assert data["source"] == "offline snapshot" and data["fund_name"] == "VOO Fund" and data["as_of"] == "2026-10-17"
    assert data["sector_weights"] == {"Information Technology": 0.6, "Health Care": 0.4}
    assert json.loads(out.read_text(encoding="utf-8"))["top_holdings"][0]["symbol"] == "X"
