"""Crash replay: today's weights applied to past market declines.

Method: the portfolio's current weights are held constant (rebalanced daily),
so each day's portfolio return is the weighted sum of holdings' daily returns
from adjusted closes. Cash returns 0%. A holding with no price history at the
start of a scenario uses the proxy stated in PROXY_MAP (default: SPY), and the
proxy is used for that scenario's whole path. Holdings with neither data nor
a usable proxy are left out and the rest re-weighted; they are listed in
`excluded`.

Max drawdown is the largest peak-to-trough fall inside the scenario window.
Months to recover counts from the trough until the portfolio regains the
earlier peak, using data after the window; None if not recovered in the data.
"""

from __future__ import annotations

import pandas as pd
from pydantic import BaseModel

from secondlook.quant.models import CASH

DAYS_PER_MONTH = 30.4375


class Scenario(BaseModel):
    key: str
    label: str
    start: str
    end: str


SCENARIOS = [
    Scenario(key="gfc", label="2008-09 financial crisis", start="2007-10-01", end="2009-03-31"),
    Scenario(key="covid", label="Covid crash", start="2020-02-01", end="2020-03-31"),
    Scenario(key="y2022", label="2022 stock and bond decline", start="2022-01-01", end="2022-12-31"),
]

# Stated proxies for holdings that did not exist yet: ticker -> {proxy ticker: weight}.
PROXY_MAP: dict[str, dict[str, float]] = {
    "VOO": {"SPY": 1.0},                                # S&P 500 fund, launched 2010
    "SCHD": {"SPY": 1.0},                               # US dividend stocks, launched 2011
    "VXUS": {"EFA": 1.0},                               # international stocks, launched 2011
    "BND": {"AGG": 1.0},                                # US bonds, launched April 2007
    "VTTSX": {"SPY": 0.55, "EFA": 0.36, "AGG": 0.09},   # 2060 target-date fund, launched 2012
    "META": {"QQQ": 1.0},                               # listed 2012
    "TSLA": {"QQQ": 1.0},                               # listed 2010
}
DEFAULT_PROXY = {"SPY": 1.0}


class ScenarioResult(BaseModel):
    key: str
    label: str
    start: str
    end: str
    max_drawdown: float               # fraction, e.g. 0.42 = a 42% fall
    max_drawdown_usd: float           # at today's portfolio value
    peak_date: str
    trough_date: str
    recovery_date: str | None
    months_to_recover: float | None   # trough to regained peak
    proxies_used: dict[str, str]      # ticker -> proxy description
    excluded: list[str]


def describe_proxy(proxy: dict[str, float]) -> str:
    if len(proxy) == 1:
        return next(iter(proxy))
    return " + ".join(f"{w:.0%} {t}" for t, w in proxy.items())


def _has_data_at(adj: pd.DataFrame, ticker: str, day: pd.Timestamp) -> bool:
    if ticker not in adj:
        return False
    first = adj[ticker].first_valid_index()
    return first is not None and first <= day


def holding_returns(
    ticker: str, adj: pd.DataFrame, day0: pd.Timestamp, proxy_map: dict[str, dict[str, float]]
) -> tuple[pd.Series | None, str | None]:
    """Daily returns for a holding from day0 on, its own or its proxy's. (None, None) if neither."""
    if _has_data_at(adj, ticker, day0):
        return adj[ticker].pct_change(fill_method=None).fillna(0.0), None
    proxy = proxy_map.get(ticker, DEFAULT_PROXY)
    if all(_has_data_at(adj, p, day0) for p in proxy):
        blended = sum(w * adj[p].pct_change(fill_method=None).fillna(0.0) for p, w in proxy.items())
        return blended, describe_proxy(proxy)
    return None, None


def portfolio_path(
    values: dict[str, float], adj: pd.DataFrame, scenario: Scenario, proxy_map: dict[str, dict[str, float]] = PROXY_MAP
) -> tuple[pd.Series, dict[str, str], list[str]]:
    """Portfolio value (1.0 on the first trading day of the scenario) through the end of the data."""
    dates = adj.index[adj.index >= pd.Timestamp(scenario.start)]
    if len(dates) == 0:
        raise ValueError(f"No price data on or after {scenario.start}")
    day0 = dates[0]

    weights, series, proxies, excluded = {}, {}, {}, []
    for ticker, value in values.items():
        if ticker == CASH:
            weights[CASH] = value
            continue
        returns, proxy = holding_returns(ticker, adj, day0, proxy_map)
        if returns is None:
            excluded.append(ticker)
            continue
        weights[ticker], series[ticker] = value, returns
        if proxy:
            proxies[ticker] = proxy
    total = sum(weights.values())
    if total <= 0:
        raise ValueError("No holdings with price data for this scenario")

    daily = pd.Series(0.0, index=dates)
    for ticker, returns in series.items():
        daily = daily + (weights[ticker] / total) * returns.reindex(dates).fillna(0.0)
    daily.iloc[0] = 0.0
    return (1.0 + daily).cumprod(), proxies, excluded


def replay_scenario(
    values: dict[str, float], adj: pd.DataFrame, scenario: Scenario, proxy_map: dict[str, dict[str, float]] = PROXY_MAP
) -> ScenarioResult:
    path, proxies, excluded = portfolio_path(values, adj, scenario, proxy_map)
    window = path[: pd.Timestamp(scenario.end)]
    drawdown = 1.0 - window / window.cummax()
    trough = drawdown.idxmax()
    max_dd = float(drawdown.max())
    peak = window[:trough].idxmax()
    peak_value = window[peak]

    recovery = None
    if max_dd > 0:
        after = path[path.index > trough]
        regained = after[after >= peak_value * (1 - 1e-12)]
        recovery = regained.index[0] if len(regained) else None
    months = 0.0 if max_dd == 0 else (
        round((recovery - trough).days / DAYS_PER_MONTH, 1) if recovery is not None else None
    )
    return ScenarioResult(
        key=scenario.key,
        label=scenario.label,
        start=scenario.start,
        end=scenario.end,
        max_drawdown=max_dd,
        max_drawdown_usd=max_dd * sum(values.values()),
        peak_date=peak.date().isoformat(),
        trough_date=trough.date().isoformat(),
        recovery_date=recovery.date().isoformat() if recovery is not None else None,
        months_to_recover=months,
        proxies_used=proxies,
        excluded=excluded,
    )


def replay(
    values: dict[str, float],
    adj: pd.DataFrame,
    scenarios: list[Scenario] = SCENARIOS,
    proxy_map: dict[str, dict[str, float]] = PROXY_MAP,
) -> list[ScenarioResult]:
    """Run every scenario the price data covers; scenarios without data are skipped."""
    results = []
    for scenario in scenarios:
        try:
            results.append(replay_scenario(values, adj, scenario, proxy_map))
        except ValueError:
            continue
    return results
