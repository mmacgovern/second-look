"""Market data: live from yfinance first, offline snapshot in data/snapshot/ as fallback.

Prices are stored per ticker as parquet (date, close, adj_close). Profiles are
stored per ticker as JSON: name, type, sector, expense ratio, and for funds the
top holdings, sector weights and asset classes that yfinance reports.

Note: `top_holdings` is only the fund's largest positions (usually the top 10),
not its full holdings list. `sector_weights` and `asset_classes` cover the
whole fund.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Callable

import pandas as pd

from secondlook.config import PROJECT_ROOT, get_logger

SNAPSHOT_DIR = PROJECT_ROOT / "data" / "snapshot"
PRICE_START = "2007-01-01"
CASH_SYMBOLS = {"CASH"}

# Long-history proxies used to replay past crashes for holdings that did not exist yet.
PROXIES: dict[str, str] = {
    "^GSPC": "S&P 500 index",
    "^IXIC": "Nasdaq Composite index",
    "SPY": "S&P 500 ETF (since 1993)",
    "QQQ": "Nasdaq-100 ETF (since 1999)",
    "IWM": "Russell 2000 small-cap ETF",
    "EFA": "Developed international stocks ETF",
    "AGG": "US aggregate bond ETF",
    "TLT": "Long-term Treasury ETF",
    "GLD": "Gold ETF",
}

log = get_logger(__name__)


class DataUnavailable(Exception):
    """Neither live data nor the snapshot has this ticker."""


@dataclass
class PriceData:
    adj_close: pd.DataFrame                              # index: date, one column per ticker
    close: pd.DataFrame
    sources: dict[str, str] = field(default_factory=dict)  # ticker -> "live" | "snapshot" | "missing"

    @property
    def missing(self) -> list[str]:
        return [t for t, s in self.sources.items() if s == "missing"]


def file_stem(ticker: str) -> str:
    """Filesystem-safe name: '^GSPC' -> '_GSPC'."""
    return re.sub(r"[^A-Za-z0-9.\-]", "_", ticker.upper())


def prices_path(ticker: str, snapshot_dir: Path | None = None) -> Path:
    return Path(snapshot_dir or SNAPSHOT_DIR) / "prices" / f"{file_stem(ticker)}.parquet"


def profile_path(ticker: str, snapshot_dir: Path | None = None) -> Path:
    return Path(snapshot_dir or SNAPSHOT_DIR) / "profiles" / f"{file_stem(ticker)}.json"


# --- live fetchers (yfinance) -----------------------------------------------

def fetch_prices_live(ticker: str, start: str = PRICE_START, end: str | None = None) -> pd.DataFrame:
    import yfinance as yf

    raw = yf.download(
        ticker, start=start, end=end, auto_adjust=False, progress=False, multi_level_index=False
    )
    if raw is None or raw.empty or "Close" not in raw:
        raise DataUnavailable(f"No live prices for {ticker}")
    df = pd.DataFrame(
        {"close": raw["Close"].astype(float), "adj_close": raw.get("Adj Close", raw["Close"]).astype(float)}
    ).dropna()
    df.index = pd.to_datetime(df.index).tz_localize(None).normalize()
    df.index.name = "date"
    if df.empty:
        raise DataUnavailable(f"No live prices for {ticker}")
    return df


def _clean_number(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(number) else round(number, 6)  # drop float32 noise like 0.00029999999


def expense_ratio_from(info: dict, fund_operations: pd.DataFrame | None, ticker: str) -> float | None:
    """Expense ratio as a fraction (0.0003 = 0.03%)."""
    if fund_operations is not None and not fund_operations.empty:
        try:
            row = fund_operations.loc["Annual Report Expense Ratio"]
            value = _clean_number(row.get(ticker, row.iloc[0]))
            if value is not None:
                return value
        except KeyError:
            pass
    net = _clean_number(info.get("netExpenseRatio"))  # yfinance reports this one in percent
    if net is not None:
        return net / 100
    return _clean_number(info.get("annualReportExpenseRatio"))


def fetch_profile_live(ticker: str, ticker_factory: Callable | None = None) -> dict:
    if ticker_factory is None:
        import yfinance as yf

        ticker_factory = yf.Ticker
    t = ticker_factory(ticker)
    info = t.info or {}
    quote_type = info.get("quoteType")
    if not info or quote_type is None:
        raise DataUnavailable(f"No live profile for {ticker}")

    profile = {
        "ticker": ticker,
        "name": info.get("longName") or info.get("shortName") or ticker,
        "quote_type": quote_type,
        "sector": info.get("sector"),
        "industry": info.get("industry"),
        "category": info.get("category"),
        "expense_ratio": None,
        "top_holdings": [],
        "sector_weights": {},
        "asset_classes": {},
        "as_of": date.today().isoformat(),
    }
    if quote_type in ("ETF", "MUTUALFUND"):
        fd = t.funds_data
        operations = None
        try:
            operations = fd.fund_operations
        except Exception:
            log.info("No fund operations for %s", ticker)
        profile["expense_ratio"] = expense_ratio_from(info, operations, ticker)
        try:
            holdings = fd.top_holdings
            profile["top_holdings"] = [
                {"symbol": str(sym), "name": str(row.get("Name", "")), "weight": _clean_number(row.get("Holding Percent"))}
                for sym, row in holdings.iterrows()
            ]
        except Exception:
            log.info("No top holdings for %s", ticker)
        for key, attr in (("sector_weights", "sector_weightings"), ("asset_classes", "asset_classes")):
            try:
                values = getattr(fd, attr) or {}
                profile[key] = {k: _clean_number(v) for k, v in values.items()}
            except Exception:
                log.info("No %s for %s", attr, ticker)
    return profile


# --- snapshot read / write ---------------------------------------------------

def save_prices(ticker: str, df: pd.DataFrame, snapshot_dir: Path | None = None) -> Path:
    path = prices_path(ticker, snapshot_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    df[["close", "adj_close"]].to_parquet(path)
    return path


def save_profile(profile: dict, snapshot_dir: Path | None = None) -> Path:
    path = profile_path(profile["ticker"], snapshot_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(profile, indent=1, sort_keys=True), encoding="utf-8")
    return path


def load_snapshot_prices(ticker: str, snapshot_dir: Path | None = None) -> pd.DataFrame:
    path = prices_path(ticker, snapshot_dir)
    if not path.exists():
        raise DataUnavailable(f"{ticker} is not in the snapshot")
    return pd.read_parquet(path)


def load_snapshot_profile(ticker: str, snapshot_dir: Path | None = None) -> dict:
    path = profile_path(ticker, snapshot_dir)
    if not path.exists():
        raise DataUnavailable(f"{ticker} is not in the snapshot")
    return json.loads(path.read_text(encoding="utf-8"))


# --- public API: live first, snapshot fallback ---------------------------------

def _slice(df: pd.DataFrame, start: str | None, end: str | None) -> pd.DataFrame:
    return df.loc[pd.Timestamp(start) if start else None : pd.Timestamp(end) if end else None]


def get_ticker_prices(
    ticker: str,
    start: str = PRICE_START,
    end: str | None = None,
    *,
    prefer_live: bool = True,
    fetch: Callable = fetch_prices_live,
    snapshot_dir: Path | None = None,
) -> tuple[pd.DataFrame, str]:
    if prefer_live:
        try:
            return fetch(ticker, start, end), "live"
        except Exception as exc:
            log.warning("Live prices failed for %s (%r); using snapshot", ticker, exc)
    return _slice(load_snapshot_prices(ticker, snapshot_dir), start, end), "snapshot"


def get_prices(
    tickers: list[str],
    start: str = PRICE_START,
    end: str | None = None,
    *,
    prefer_live: bool = True,
    fetch: Callable = fetch_prices_live,
    snapshot_dir: Path | None = None,
    on_ticker: Callable[[str], None] | None = None,
) -> PriceData:
    """Daily prices for each ticker (cash symbols skipped); missing tickers are reported, not raised.
    on_ticker(ticker) is called before each ticker is fetched (for progress displays)."""
    adj, close, sources = {}, {}, {}
    for ticker in dict.fromkeys(t.upper() for t in tickers):
        if ticker in CASH_SYMBOLS:
            continue
        if on_ticker is not None:
            on_ticker(ticker)
        try:
            df, source = get_ticker_prices(
                ticker, start, end, prefer_live=prefer_live, fetch=fetch, snapshot_dir=snapshot_dir
            )
        except DataUnavailable:
            log.warning("No prices for %s from live data or snapshot", ticker)
            sources[ticker] = "missing"
            continue
        adj[ticker], close[ticker], sources[ticker] = df["adj_close"], df["close"], source
    return PriceData(pd.DataFrame(adj).sort_index(), pd.DataFrame(close).sort_index(), sources)


def get_profile(
    ticker: str,
    *,
    prefer_live: bool = True,
    fetch: Callable = fetch_profile_live,
    snapshot_dir: Path | None = None,
) -> tuple[dict, str]:
    """A ticker's profile and where it came from ("live" or "snapshot"). Raises DataUnavailable."""
    if prefer_live:
        try:
            return fetch(ticker), "live"
        except Exception as exc:
            log.warning("Live profile failed for %s (%r); using snapshot", ticker, exc)
    return load_snapshot_profile(ticker, snapshot_dir), "snapshot"
