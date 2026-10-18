"""Build the offline market-data snapshot in data/snapshot/.

For every ticker in data/sample_portfolios/*.csv plus the crash-replay proxies,
downloads daily prices from 2007 to today and the fund/stock profile (top
holdings, sector weights, asset classes, expense ratio) from yfinance.

Also writes the S&P 500 reference (an index fund's top holdings and sector
weights, with its name and "as of" date) to data/reference/sp500_benchmark.json:
live from yfinance, or from the offline snapshot if that fails.

Usage:
    python scripts/build_snapshot.py                 # everything
    python scripts/build_snapshot.py --tickers ARKK  # also include extra tickers
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from secondlook import market_data as md  # noqa: E402
from secondlook.quant import benchmark as bm  # noqa: E402

SAMPLES_DIR = ROOT / "data" / "sample_portfolios"


def sample_tickers(samples_dir: Path = SAMPLES_DIR) -> list[str]:
    tickers: list[str] = []
    for path in sorted(samples_dir.glob("*.csv")):
        with path.open(newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                symbol = (row.get("Symbol") or "").strip().upper()
                if symbol and symbol not in md.CASH_SYMBOLS:
                    tickers.append(symbol)
    return list(dict.fromkeys(tickers))


def build(
    tickers: list[str],
    out_dir: Path,
    start: str = md.PRICE_START,
    fetch_prices=md.fetch_prices_live,
    fetch_profile=md.fetch_profile_live,
) -> dict:
    manifest = {
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": "yfinance",
        "price_start": start,
        "proxies": md.PROXIES,
        "tickers": {},
        "failures": [],
    }
    for ticker in tickers:
        entry: dict = {}
        try:
            prices = fetch_prices(ticker, start, None)
            md.save_prices(ticker, prices, out_dir)
            entry.update(
                rows=len(prices),
                first_date=prices.index.min().date().isoformat(),
                last_date=prices.index.max().date().isoformat(),
            )
        except Exception as exc:
            manifest["failures"].append({"ticker": ticker, "part": "prices", "error": repr(exc)})
        try:
            profile = fetch_profile(ticker)
            md.save_profile(profile, out_dir)
            entry.update(
                quote_type=profile.get("quote_type"),
                expense_ratio=profile.get("expense_ratio"),
                top_holdings=len(profile.get("top_holdings") or []),
                has_sector_weights=bool(profile.get("sector_weights")),
            )
        except Exception as exc:
            manifest["failures"].append({"ticker": ticker, "part": "profile", "error": repr(exc)})
        manifest["tickers"][ticker] = entry
        print(f"{ticker:8} {entry.get('rows', 'no prices')!s:>6} rows  {entry.get('quote_type') or 'no profile'}")

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    return manifest


def write_benchmark(
    path: Path = bm.BENCHMARK_FILE,
    ticker: str = bm.BENCHMARK_TICKER,
    fetch_profile=md.fetch_profile_live,
    snapshot_dir: Path | None = None,
) -> dict:
    """Save the S&P 500 reference file from the index fund's profile (live first, snapshot fallback)."""
    try:
        profile, source = fetch_profile(ticker), "yfinance"
    except Exception as exc:
        print(f"Live profile for {ticker} failed ({exc!r}); using the offline snapshot")
        profile, source = md.load_snapshot_profile(ticker, snapshot_dir), "offline snapshot"
    data = bm.benchmark_from_profile(profile, source)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1), encoding="utf-8")
    print(f"S&P 500 reference: {data['fund_name']} ({data['fund']}), as of {data['as_of']}, from {source} -> {path}")
    return data


def main(argv: list[str] | None = None, **build_kwargs) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, default=md.SNAPSHOT_DIR)
    parser.add_argument("--samples", type=Path, default=SAMPLES_DIR)
    parser.add_argument("--start", default=md.PRICE_START)
    parser.add_argument("--tickers", nargs="*", default=[], help="extra tickers to include")
    parser.add_argument("--benchmark-out", type=Path, default=None,
                        help="default: data/reference/sp500_benchmark.json, or next to --out when --out is elsewhere")
    args = parser.parse_args(argv)

    tickers = list(dict.fromkeys(sample_tickers(args.samples) + list(md.PROXIES) + [t.upper() for t in args.tickers]))
    manifest = build(tickers, args.out, args.start, **build_kwargs)
    print(f"\n{len(tickers)} tickers, {len(manifest['failures'])} failures -> {args.out}")
    for failure in manifest["failures"]:
        print(f"  FAILED {failure['ticker']} {failure['part']}: {failure['error']}")
    bench_out = args.benchmark_out or (bm.BENCHMARK_FILE if Path(args.out) == md.SNAPSHOT_DIR
                                       else Path(args.out) / "sp500_benchmark.json")
    write_benchmark(bench_out, fetch_profile=build_kwargs.get("fetch_profile", md.fetch_profile_live),
                    snapshot_dir=args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
