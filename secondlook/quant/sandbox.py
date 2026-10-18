"""Try a shift: a pretend $1,000 in three mixes of asset types, replayed through real market history.

Each mix is a fixed split between asset types, tracked by index stand-ins (an S&P 500 fund for US
stocks, a developed-markets fund for international stocks, an aggregate bond fund for US bonds) and
cash, which is assumed not to grow. The mixes are illustrations of risk levels, not recommendations,
and the user never sees the stand-ins' tickers: only the asset types.

Method: the same as the crash replay. Weights are held constant (rebalanced daily), starting on
the first trading day of the period, using adjusted closes from the offline snapshot or live data.
The user's own portfolio is replayed the same way, with today's weights, for comparison.
"""

from __future__ import annotations

import pandas as pd
from pydantic import BaseModel

from secondlook.quant.models import CASH
from secondlook.quant.replay import SCENARIOS, Scenario, portfolio_path, replay

AMOUNT_USD = 1000
START = "2007-01-01"

# Asset types and the index stand-in that tracks each one (tickers stay internal).
ASSET_TYPES: dict[str, str] = {
    "SPY": "US stocks",
    "EFA": "International stocks",
    "AGG": "US bonds",
    CASH: "Cash",
}
STAND_INS = {"SPY": "an S&P 500 index fund", "EFA": "a developed-markets index fund",
             "AGG": "a US aggregate bond index fund", CASH: "cash, assumed not to grow"}

MIXES: dict[str, dict[str, float]] = {
    "Conservative": {"SPY": 0.20, "EFA": 0.05, "AGG": 0.60, CASH: 0.15},
    "Moderate": {"SPY": 0.45, "EFA": 0.15, "AGG": 0.35, CASH: 0.05},
    "Risky": {"SPY": 0.70, "EFA": 0.25, "AGG": 0.05},
}
YOURS = "Your portfolio"
HINDSIGHT_NOTE = ("Your portfolio is replayed with today's holdings, so it has hindsight built in: holdings that did "
                  "well are more likely to be in a portfolio today. It is not a fair comparison with the mixes.")


class StrategyResult(BaseModel):
    name: str
    mix_pct: dict[str, float]          # asset type -> percent of the mix (empty for the user's portfolio)
    end_usd: float                      # what the pretend amount grew to by the end of the data
    annual_return_pct: float            # compound yearly return over the whole period
    worst_drop_pct: float               # largest peak-to-trough fall over the whole period
    crash_drop_pct: dict[str, float]    # scenario label -> largest fall in that crash


class SandboxResult(BaseModel):
    amount_usd: float
    start: str
    end: str
    years: float
    strategies: list[StrategyResult]   # the three mixes, then the user's portfolio
    paths: dict[str, list[float]]      # name -> value of the pretend amount on each date
    dates: list[str]


def _result(name: str, values: dict[str, float], adj: pd.DataFrame, amount: float,
            mix_pct: dict[str, float]) -> tuple[StrategyResult, pd.Series]:
    path, _, _ = portfolio_path(values, adj, Scenario(key="all", label="All", start=START, end="2100-01-01"))
    years = (path.index[-1] - path.index[0]).days / 365.25
    worst = float((1 - path / path.cummax()).max())
    crashes = {r.label: round(100 * r.max_drawdown, 1) for r in replay(values, adj, SCENARIOS)}
    end = float(path.iloc[-1]) * amount
    return StrategyResult(
        name=name, mix_pct=mix_pct, end_usd=round(end, 0),
        annual_return_pct=round(100 * ((end / amount) ** (1 / years) - 1), 1) if years > 0 else 0.0,
        worst_drop_pct=round(100 * worst, 1), crash_drop_pct=crashes,
    ), path * amount


def run_sandbox(adj: pd.DataFrame, your_values: dict[str, float] | None = None, amount: float = AMOUNT_USD) -> SandboxResult:
    """Replay each mix (and the user's portfolio, if given) from START to the end of the price data."""
    results, paths = [], {}
    for name, mix in MIXES.items():
        pct = {ASSET_TYPES[t]: round(100 * w, 0) for t, w in mix.items()}
        r, p = _result(name, mix, adj, amount, pct)
        results.append(r)
        paths[name] = p
    if your_values:
        r, p = _result(YOURS, your_values, adj, amount, {})
        results.append(r)
        paths[YOURS] = p
    index = next(iter(paths.values())).index
    # the start day, then one point per month end: every line starts at the pretend amount, and the chart stays light
    month_ends = index.to_series().groupby(index.to_period("M")).last()
    monthly = [index[0]] + [d for d in month_ends if d != index[0]]
    return SandboxResult(
        amount_usd=amount, start=index[0].date().isoformat(), end=index[-1].date().isoformat(),
        years=round((index[-1] - index[0]).days / 365.25, 1), strategies=results,
        paths={k: [round(float(v.loc[d]), 2) for d in monthly] for k, v in paths.items()},
        dates=[d.date().isoformat() for d in monthly],
    )


def sandbox_facts(result: SandboxResult) -> dict:
    """What the sandbox agent sees: the computed results and how they were made, nothing else."""
    return {
        "about": "Pretend money replayed through real market history in Python. Explain; do not add numbers.",
        "pretend_amount_usd": int(result.amount_usd),
        "period": f"{result.start[:7]} to {result.end[:7]}",
        "years": result.years,
        "method": "weights held constant; mixes use index stand-ins for each asset type; cash assumed not to grow",
        "past_results_note": "Past results do not predict future results.",
        "your_portfolio_note": HINDSIGHT_NOTE,
        "strategies": [
            {"name": s.name, **({"mix_pct": s.mix_pct} if s.mix_pct else {}), "value_today_usd": int(s.end_usd),
             "yearly_return_pct": s.annual_return_pct, "worst_drop_pct": s.worst_drop_pct,
             "crash_drops_pct": s.crash_drop_pct}
            for s in result.strategies
        ],
    }


def prepare(values: dict[str, float], profiles: dict[str, dict], adj: pd.DataFrame,
            bench=None) -> tuple[SandboxResult, dict, list[str]]:
    """Everything the sandbox tab and agent need, built the same way in the app and in demo recording:
    (replay result, facts for the agent, names the explanation must avoid, including the stand-ins' tickers)."""
    from secondlook.quant.shifts import banned_names

    result = run_sandbox(adj, values)
    banned = sorted(set(banned_names(values, profiles, bench)) | {t for t in ASSET_TYPES if t != CASH})
    return result, sandbox_facts(result), banned
