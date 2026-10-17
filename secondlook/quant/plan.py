"""Retirement plan in today's dollars.

    target nest egg = (desired income - Social Security/pension) / withdrawal rate

The counted balance (pre-tax accounts already reduced by the flat tax rate, see
accounts.py) grows at each scenario's real return minus the portfolio's
weighted expense ratio, with the annual contribution added at the end of each
year:

    B[t+1] = B[t] * (1 + r) + contribution

Required annual contribution to reach the target in n years (the same as a
spreadsheet =PMT(r, n, -balance, target)):

    (target - balance * (1+r)^n) * r / ((1+r)^n - 1)      (r = 0: (target - balance) / n)
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from secondlook.quant.accounts import RETIREMENT_TAX_RATE
from secondlook.quant.profile import UserProfile

ASSUMPTIONS: dict = {
    "withdrawal_rate": 0.039,
    "withdrawal_rate_min": 0.035,
    "withdrawal_rate_max": 0.045,
    "real_returns": {"cautious": 0.02, "middle": 0.04, "strong": 0.06},  # after inflation, before fees
    "retirement_tax_rate": RETIREMENT_TAX_RATE,
    "contributions": "added at the end of each year, constant in today's dollars",
    "dollars": "today's dollars (returns are after inflation)",
}


class YearPoint(BaseModel):
    year: int          # 0 = today
    age: int
    balance_usd: float


class ScenarioPlan(BaseModel):
    name: str
    real_return: float
    net_return: float                                # real return minus expense ratio
    balances: list[YearPoint]
    projected_at_retirement_usd: float
    gap_usd: float                                   # target minus projected; 0 or less means on track
    on_track: bool
    required_annual_contribution_usd: float | None   # None when no years are left
    supported_income_usd: float                      # projected * withdrawal rate + SS/pension


class PlanResult(BaseModel):
    status: Literal["saving", "retiring_now", "retired"]
    years_to_retirement: int
    target_nest_egg_usd: float
    income_needed_from_portfolio_usd: float
    withdrawal_rate: float
    expense_ratio: float
    counted_balance_usd: float
    annual_contribution_usd: float
    scenarios: list[ScenarioPlan]
    assumptions: dict


def required_nest_egg(desired_income: float, guaranteed_income: float, withdrawal_rate: float) -> float:
    lo, hi = ASSUMPTIONS["withdrawal_rate_min"], ASSUMPTIONS["withdrawal_rate_max"]
    if not lo <= withdrawal_rate <= hi:
        raise ValueError(f"withdrawal_rate must be between {lo} and {hi}")
    return max(0.0, desired_income - guaranteed_income) / withdrawal_rate


def future_value(balance: float, contribution: float, rate: float, years: int) -> float:
    growth = (1 + rate) ** years
    annuity = years if rate == 0 else (growth - 1) / rate
    return balance * growth + contribution * annuity


def required_contribution(target: float, balance: float, rate: float, years: int) -> float | None:
    if years <= 0:
        return None
    need = target - balance * (1 + rate) ** years
    if need <= 0:
        return 0.0
    if rate == 0:
        return need / years
    return need * rate / ((1 + rate) ** years - 1)


def project(balance: float, contribution: float, rate: float, years: int, start_age: int) -> list[YearPoint]:
    points, b = [YearPoint(year=0, age=start_age, balance_usd=balance)], balance
    for t in range(1, years + 1):
        b = b * (1 + rate) + contribution
        points.append(YearPoint(year=t, age=start_age + t, balance_usd=b))
    return points


def make_plan(
    user: UserProfile,
    counted_balance_usd: float,
    expense_ratio: float = 0.0,
    withdrawal_rate: float = ASSUMPTIONS["withdrawal_rate"],
    real_returns: dict[str, float] | None = None,
    tax_rate: float = RETIREMENT_TAX_RATE,
) -> PlanResult:
    returns = real_returns or ASSUMPTIONS["real_returns"]
    guaranteed = user.guaranteed_income_usd
    target = required_nest_egg(user.desired_income_today_usd, guaranteed, withdrawal_rate)
    years = user.years_to_retirement
    status = "retired" if user.age > user.retirement_age else "retiring_now" if years == 0 else "saving"
    contribution = user.annual_contribution_usd if years > 0 else 0.0

    scenarios = []
    for name, real in returns.items():
        net = real - expense_ratio
        path = project(counted_balance_usd, contribution, net, years, user.age)
        projected = path[-1].balance_usd
        gap = target - projected
        scenarios.append(
            ScenarioPlan(
                name=name,
                real_return=real,
                net_return=net,
                balances=path,
                projected_at_retirement_usd=projected,
                gap_usd=gap,
                on_track=gap <= 0,
                required_annual_contribution_usd=required_contribution(target, counted_balance_usd, net, years),
                supported_income_usd=projected * withdrawal_rate + guaranteed,
            )
        )
    return PlanResult(
        status=status,
        years_to_retirement=years,
        target_nest_egg_usd=target,
        income_needed_from_portfolio_usd=max(0.0, user.desired_income_today_usd - guaranteed),
        withdrawal_rate=withdrawal_rate,
        expense_ratio=expense_ratio,
        counted_balance_usd=counted_balance_usd,
        annual_contribution_usd=contribution,
        scenarios=scenarios,
        assumptions={**ASSUMPTIONS, "withdrawal_rate": withdrawal_rate, "real_returns": dict(returns),
                     "retirement_tax_rate": tax_rate},
    )
