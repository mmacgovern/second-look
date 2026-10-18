""""What you own" vs the S&P 500: top-10 companies and sector mix next to an S&P 500 index fund.

The S&P 500 reference is an index ETF's reported holdings and sector weights, saved in
data/reference/sp500_benchmark.json by scripts/build_snapshot.py (yfinance first, the
offline snapshot as fallback). The fund reports only its top 10 holdings, so a company
outside that list has no S&P 500 weight here ("not in the fund's top 10").

All comparisons are neutral descriptions of weights. Nothing here says what to buy or sell.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from pydantic import BaseModel

from secondlook.config import PROJECT_ROOT
from secondlook.quant.xray import XRay, company_key, sector_key

BENCHMARK_FILE = PROJECT_ROOT / "data" / "reference" / "sp500_benchmark.json"
BENCHMARK_TICKER = "VOO"
MIN_SLICE_PCT = 3.0  # donut slices smaller than this are grouped into "Other"

# yfinance / Morningstar sector keys -> the 11 GICS sector names
GICS = {
    "technology": "Information Technology",
    "communication_services": "Communication Services",
    "consumer_cyclical": "Consumer Discretionary",
    "consumer_defensive": "Consumer Staples",
    "financial_services": "Financials",
    "healthcare": "Health Care",
    "industrials": "Industrials",
    "energy": "Energy",
    "utilities": "Utilities",
    "realestate": "Real Estate",
    "basic_materials": "Materials",
}


class Benchmark(BaseModel):
    fund: str                    # ticker, e.g. VOO
    fund_name: str
    as_of: str
    source: str                  # "yfinance" or "offline snapshot"
    top_holdings: list[dict]     # {"symbol", "name", "weight"} as fractions of the fund
    sector_weights: dict[str, float]  # GICS name -> fraction of the fund

    @property
    def label(self) -> str:
        return f"S&P 500 weights via {self.fund_name} ({self.fund}), as of {self.as_of}"

    def weight_of(self, symbol: str) -> float | None:
        """The company's weight in the fund, share classes combined (GOOG + GOOGL); None if not reported."""
        key = company_key(symbol)
        weights = [h["weight"] for h in self.top_holdings if company_key(h["symbol"]) == key]
        return sum(weights) if weights else None

    @property
    def top10_weight(self) -> float:
        return sum(sorted((h["weight"] for h in self.top_holdings), reverse=True)[:10])


class TopRow(BaseModel):
    symbol: str
    name: str
    weight: float                # fraction of the user's total portfolio
    sp500_weight: float | None   # fraction of the S&P 500 fund, None if outside its reported top 10


class TopTen(BaseModel):
    mode: str                    # "look_through" or "direct"
    rows: list[TopRow]
    weight: float                # sum of the rows, fraction of the total portfolio
    sp500_weight: float          # the fund's own top-10 share


class SectorRow(BaseModel):
    sector: str                  # GICS name
    weight: float                # fraction of the user's stock holdings
    sp500_weight: float          # fraction of the S&P 500 fund

    @property
    def difference(self) -> float:
        return self.weight - self.sp500_weight

    @property
    def ratio(self) -> float | None:
        return self.weight / self.sp500_weight if self.sp500_weight > 0 else None


def benchmark_from_profile(profile: dict, source: str) -> dict:
    """The saved reference file's contents, built from an index fund's profile."""
    weights = {GICS[sector_key(k)]: float(v) for k, v in (profile.get("sector_weights") or {}).items()
               if sector_key(k) in GICS and v}
    total = sum(weights.values())
    return {
        "fund": profile["ticker"],
        "fund_name": profile.get("name") or profile["ticker"],
        "as_of": profile.get("as_of") or date.today().isoformat(),
        "source": source,
        "note": "S&P 500 reference = this index fund's reported top holdings and sector weights. "
                "The fund reports only its top 10 holdings.",
        "top_holdings": [{"symbol": h["symbol"], "name": h.get("name") or h["symbol"], "weight": float(h["weight"])}
                         for h in profile.get("top_holdings") or [] if h.get("weight")],
        "sector_weights": {k: round(v / total, 6) for k, v in weights.items()} if total else {},
    }


def load_benchmark(path: Path = BENCHMARK_FILE) -> Benchmark:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return Benchmark(**{k: data[k] for k in Benchmark.model_fields})


def top_ten(xr: XRay, bench: Benchmark, look_through: bool = True, n: int = 10) -> TopTen:
    """The n largest companies as a share of the whole portfolio. look_through=False counts only
    shares held directly. `xr` must be built with a large top_n so every company is available."""
    if look_through:
        pairs = [(c, c.weight) for c in xr.top_companies]
    else:
        pairs = [(c, c.via.get("direct", 0.0)) for c in xr.top_companies]
    pairs = sorted((p for p in pairs if p[1] > 0), key=lambda p: p[1], reverse=True)[:n]
    rows = [TopRow(symbol=c.symbol, name=c.name, weight=w, sp500_weight=bench.weight_of(c.symbol)) for c, w in pairs]
    return TopTen(mode="look_through" if look_through else "direct", rows=rows,
                  weight=sum(r.weight for r in rows), sp500_weight=bench.top10_weight)


def stock_sector_weights(xr: XRay) -> dict[str, float]:
    """The user's sector weights as fractions of their stock holdings only (bonds, cash, other
    assets and unclassified holdings left out), in GICS names; sums to 1."""
    stocks = {GICS[k]: v for k, v in xr.sectors.items() if k in GICS and v > 0}
    total = sum(stocks.values())
    return {k: v / total for k, v in sorted(stocks.items(), key=lambda kv: kv[1], reverse=True)} if total else {}


def donut_slices(weights: dict[str, float], min_pct: float = MIN_SLICE_PCT) -> dict[str, float]:
    """Sector slices with those under min_pct grouped into "Other"; still sums to 1."""
    out, other = {}, 0.0
    for sector, w in weights.items():
        if 100 * w < min_pct:
            other += w
        else:
            out[sector] = w
    if other > 0:
        out["Other"] = other
    return out


def sector_rows(xr: XRay, bench: Benchmark) -> list[SectorRow]:
    """All 11 sectors, you vs the S&P 500, biggest absolute difference first."""
    mine = stock_sector_weights(xr)
    rows = [SectorRow(sector=s, weight=mine.get(s, 0.0), sp500_weight=bench.sector_weights.get(s, 0.0))
            for s in GICS.values()]
    return sorted(rows, key=lambda r: abs(r.difference), reverse=True)


def comparison_sentence(row: SectorRow) -> str:
    """A neutral description, e.g. "You hold 2.4x the S&P 500's weight in Information Technology"."""
    if row.ratio is None:
        return f"The S&P 500 fund reports no {row.sector} weight; you hold {100 * row.weight:.1f}%."
    if row.weight == 0:
        return f"You hold no {row.sector}; the S&P 500 holds {100 * row.sp500_weight:.1f}%."
    return f"You hold {row.ratio:.1f}x the S&P 500's weight in {row.sector}."


def facts_block(xr: XRay, bench: Benchmark, n_sectors: int = 3) -> dict:
    """Compact comparison numbers for the Facts JSON (the verifier checks every number the AI writes)."""
    top = top_ten(xr, bench)
    rows = sector_rows(xr, bench)[:n_sectors]
    return {
        "reference": bench.label,
        "top10_companies_pct_of_portfolio": round(100 * top.weight, 1),
        "sp500_top10_pct": round(100 * top.sp500_weight, 1),
        "sectors_pct_of_stocks_vs_sp500": [
            {"sector": r.sector, "you_pct": round(100 * r.weight, 1), "sp500_pct": round(100 * r.sp500_weight, 1),
             **({"times_sp500": round(r.ratio, 1)} if r.ratio is not None else {})}
            for r in rows
        ],
    }
