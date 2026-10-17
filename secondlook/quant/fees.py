"""Fund fees: weighted expense ratio and what fees cost over 10, 20 and 30 years.

Stocks and cash have no expense ratio (0%). Funds use the expense ratio from
their profile; funds without one are left out and listed in `unknown_symbols`.

Projections grow today's value with annual compounding and no new deposits:
    value after n years = V * (1 + gross_return - expense_ratio) ** n
and compare against no fees and against a 0.05% index-fund baseline.
"""

from __future__ import annotations

from pydantic import BaseModel

from secondlook.quant.models import CASH
from secondlook.quant.xray import classify

BASELINE_EXPENSE_RATIO = 0.0005
DEFAULT_GROSS_RETURN = 0.06
HORIZONS = (10, 20, 30)


class FundFee(BaseModel):
    symbol: str
    value_usd: float
    expense_ratio: float
    annual_cost_usd: float


class FeeProjection(BaseModel):
    years: int
    value_no_fees_usd: float
    value_baseline_usd: float
    value_portfolio_usd: float
    cost_vs_no_fees_usd: float       # what this portfolio's fees take out
    cost_vs_baseline_usd: float      # extra versus 0.05% index funds (negative = cheaper)


class FeeReport(BaseModel):
    weighted_expense_ratio: float     # fraction, e.g. 0.0012 = 0.12%
    annual_cost_usd: float
    baseline_expense_ratio: float
    gross_return: float
    covered_value_usd: float          # value with a known expense ratio (stocks and cash count as 0%)
    funds: list[FundFee]              # costliest first
    projections: list[FeeProjection]
    unknown_symbols: list[str]


def fee_report(
    values: dict[str, float],
    profiles: dict[str, dict],
    gross_return: float = DEFAULT_GROSS_RETURN,
    baseline_expense_ratio: float = BASELINE_EXPENSE_RATIO,
    horizons: tuple[int, ...] = HORIZONS,
) -> FeeReport:
    covered = annual = 0.0
    funds: list[FundFee] = []
    unknown: list[str] = []
    for symbol, value in values.items():
        profile = profiles.get(symbol)
        kind = "cash" if symbol == CASH else classify(profile)
        if kind in ("cash", "stock"):
            covered += value
            continue
        ratio = (profile or {}).get("expense_ratio")
        if kind != "fund" or ratio is None:
            unknown.append(symbol)
            continue
        covered += value
        annual += value * ratio
        funds.append(FundFee(symbol=symbol, value_usd=value, expense_ratio=ratio, annual_cost_usd=value * ratio))

    weighted = annual / covered if covered else 0.0
    projections = []
    for years in horizons:
        no_fees = covered * (1 + gross_return) ** years
        baseline = covered * (1 + gross_return - baseline_expense_ratio) ** years
        actual = covered * (1 + gross_return - weighted) ** years
        projections.append(
            FeeProjection(
                years=years,
                value_no_fees_usd=no_fees,
                value_baseline_usd=baseline,
                value_portfolio_usd=actual,
                cost_vs_no_fees_usd=no_fees - actual,
                cost_vs_baseline_usd=baseline - actual,
            )
        )
    return FeeReport(
        weighted_expense_ratio=weighted,
        annual_cost_usd=annual,
        baseline_expense_ratio=baseline_expense_ratio,
        gross_return=gross_return,
        covered_value_usd=covered,
        funds=sorted(funds, key=lambda f: f.annual_cost_usd, reverse=True),
        projections=projections,
        unknown_symbols=unknown,
    )
