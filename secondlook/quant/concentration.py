"""Concentration: how much rides on a few companies or sectors.

- Largest single-company exposure, from the look-through (direct + via funds).
- Sector weights of the stock part of the portfolio versus a total-market
  reference (VTI's reported sector weights from the snapshot by default).
- Effective number of holdings = 1 / HHI, where HHI is the sum of squared
  position weights (cash left out). 10 equal positions -> 10.
- Effective number of companies uses the look-through weights. Fund holdings
  beyond the reported top 10 are treated as fully spread out, so this is an
  upper bound.
- Flags every company whose look-through exposure is above CONCENTRATION_THRESHOLD.
- Reports the user's employer stock (if they mark one): look-through share, direct
  share, and whether it is above the threshold.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel

from secondlook.quant.models import CASH
from secondlook.quant.xray import CompanyExposure, sector_key, xray

CONCENTRATION_THRESHOLD = 0.10
REFERENCE_TICKER = "VTI"
NON_STOCK = {"bonds", "cash", "other", "unclassified"}


class SectorComparison(BaseModel):
    sector: str
    portfolio: float       # fraction of the portfolio's stocks
    reference: float       # fraction of the total-market reference
    difference: float      # portfolio - reference


class ConcentrationFlag(BaseModel):
    ticker: str
    name: str
    weight: float          # look-through, fraction of total portfolio
    direct_weight: float   # held directly as a stock


class EmployerStock(BaseModel):
    ticker: str
    weight: float          # look-through, fraction of total portfolio (0 if not held)
    direct_weight: float
    over_threshold: bool


class Concentration(BaseModel):
    largest_company: CompanyExposure | None
    sectors_vs_market: list[SectorComparison]
    effective_holdings: float | None
    effective_companies_upper_bound: float | None
    threshold: float
    flags: list[ConcentrationFlag]
    reference: str
    employer_stock: EmployerStock | None = None


def inverse_hhi(weights: list[float]) -> float | None:
    total = sum(weights)
    if total <= 0:
        return None
    return 1.0 / sum((w / total) ** 2 for w in weights)


def stock_sectors(sectors: dict[str, float]) -> dict[str, float]:
    """Sector weights of the stock part only, rescaled to sum to 1."""
    stocks = {k: v for k, v in sectors.items() if k not in NON_STOCK and v > 0}
    total = sum(stocks.values())
    return {k: v / total for k, v in stocks.items()} if total else {}


def reference_sectors(snapshot_dir: Path | None = None, ticker: str = REFERENCE_TICKER) -> dict[str, float]:
    from secondlook import market_data as md

    profile = md.load_snapshot_profile(ticker, snapshot_dir)
    return stock_sectors({sector_key(k): v or 0.0 for k, v in (profile.get("sector_weights") or {}).items()})


def concentration(
    values: dict[str, float],
    profiles: dict[str, dict],
    reference: dict[str, float] | None = None,
    threshold: float = CONCENTRATION_THRESHOLD,
    top_sectors: int = 5,
    reference_name: str = f"{REFERENCE_TICKER} (total US market) sector weights",
    employer_ticker: str | None = None,
) -> Concentration:
    xr = xray(values, profiles, top_n=1_000_000)
    ref = reference if reference is not None else reference_sectors()
    invested = [v for s, v in values.items() if s != CASH and v > 0]
    invested_weight = 1.0 - xr.cash_weight

    mine = stock_sectors(xr.sectors)
    keys = sorted(mine, key=mine.get, reverse=True)[:top_sectors]
    comparisons = [
        SectorComparison(sector=k, portfolio=mine[k], reference=ref.get(k, 0.0), difference=mine[k] - ref.get(k, 0.0))
        for k in keys
    ]

    company_weights = [c.weight / invested_weight for c in xr.top_companies] if invested_weight > 0 else []
    effective_companies = (
        1.0 / sum(w * w for w in company_weights) if company_weights and sum(company_weights) > 0 else None
    )
    flags = [
        ConcentrationFlag(ticker=c.symbol, name=c.name, weight=c.weight, direct_weight=c.via.get("direct", 0.0))
        for c in xr.top_companies
        if c.weight > threshold
    ]
    employer = None
    if employer_ticker:
        from secondlook.quant.xray import company_key

        key = company_key(employer_ticker)
        match = next((c for c in xr.top_companies if c.symbol == key), None)
        weight = match.weight if match else 0.0
        employer = EmployerStock(ticker=key, weight=weight, direct_weight=match.via.get("direct", 0.0) if match else 0.0,
                                 over_threshold=weight > threshold)
    return Concentration(
        employer_stock=employer,
        largest_company=xr.top_companies[0] if xr.top_companies else None,
        sectors_vs_market=comparisons,
        effective_holdings=inverse_hhi(invested),
        effective_companies_upper_bound=effective_companies,
        threshold=threshold,
        flags=flags,
        reference=reference_name,
    )
