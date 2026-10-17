"""Pause check: what a proposed trade would change, with historical context.

Given an Action (sell everything, sell X%, or buy $N of a ticker), returns the
before/after top company exposures, sector weights and crash replays, plus
what happened to the market after the 2009 and 2020 bottoms. It describes
consequences only; it never says whether to make the trade.

Sells move proceeds to cash. Buys are funded with new money (the portfolio grows).
"""

from __future__ import annotations

import pandas as pd
from pydantic import BaseModel

from secondlook.quant.models import CASH, Action
from secondlook.quant.replay import DAYS_PER_MONTH, PROXY_MAP, SCENARIOS, Scenario, ScenarioResult, replay
from secondlook.quant.xray import CompanyExposure, xray

MARKET = "SPY"  # total-return proxy for "the market"
CONTEXT_SCENARIOS = ("gfc", "covid")


class PortfolioView(BaseModel):
    total_value_usd: float
    cash_weight: float
    top_companies: list[CompanyExposure]
    sectors: dict[str, float]
    replay: list[ScenarioResult]


class MarketEvent(BaseModel):
    event: str
    peak_date: str
    bottom_date: str
    drop: float                       # peak to bottom, fraction
    gain_1y_after_bottom: float | None
    gain_3y_after_bottom: float | None
    months_bottom_to_recovery: float | None


class PauseResult(BaseModel):
    action: str
    before: PortfolioView
    after: PortfolioView
    history: list[MarketEvent]


def apply_action(values: dict[str, float], action: Action) -> dict[str, float]:
    after = dict(values)
    if action.kind == "buy":
        after[action.ticker] = after.get(action.ticker, 0.0) + action.amount_usd
        return after

    fraction = 1.0 if action.kind == "sell_all" else action.pct / 100
    if action.ticker is not None:
        if action.ticker not in values or action.ticker == CASH:
            raise ValueError(f"{action.ticker} is not a holding that can be sold")
        targets = [action.ticker]
    else:
        targets = [t for t in values if t != CASH]
    for ticker in targets:
        sold = after[ticker] * fraction
        after[ticker] -= sold
        after[CASH] = after.get(CASH, 0.0) + sold
    return {t: v for t, v in after.items() if v > 1e-9}


def _value_after(series: pd.Series, day: pd.Timestamp, days: int) -> float | None:
    later = series[series.index >= day + pd.Timedelta(days=days)]
    return float(later.iloc[0]) if len(later) else None


def market_events(adj: pd.DataFrame, scenarios: list[Scenario] = SCENARIOS, market: str = MARKET) -> list[MarketEvent]:
    """Peak, bottom and what came next for the market in the 2008-09 and 2020 crashes."""
    if market not in adj:
        return []
    series = adj[market].dropna()
    events = []
    for scenario in scenarios:
        if scenario.key not in CONTEXT_SCENARIOS:
            continue
        window = series[pd.Timestamp(scenario.start) : pd.Timestamp(scenario.end)]
        if window.empty:
            continue
        bottom = window.idxmin()
        peak = window[:bottom].idxmax()
        bottom_value, peak_value = window[bottom], window[peak]
        one_year, three_year = _value_after(series, bottom, 365), _value_after(series, bottom, 3 * 365)
        regained = series[(series.index > bottom) & (series >= peak_value)]
        events.append(
            MarketEvent(
                event=f"{scenario.label} ({market})",
                peak_date=peak.date().isoformat(),
                bottom_date=bottom.date().isoformat(),
                drop=float(1 - bottom_value / peak_value),
                gain_1y_after_bottom=one_year / bottom_value - 1 if one_year is not None else None,
                gain_3y_after_bottom=three_year / bottom_value - 1 if three_year is not None else None,
                months_bottom_to_recovery=round((regained.index[0] - bottom).days / DAYS_PER_MONTH, 1)
                if len(regained) else None,
            )
        )
    return events


def view(values: dict[str, float], profiles: dict[str, dict], adj: pd.DataFrame, scenarios, proxy_map, top_n: int) -> PortfolioView:
    xr = xray(values, profiles, top_n=top_n)
    return PortfolioView(
        total_value_usd=xr.total_value_usd,
        cash_weight=xr.cash_weight,
        top_companies=xr.top_companies,
        sectors=xr.sectors,
        replay=replay(values, adj, scenarios, proxy_map),
    )


def pause_check(
    values: dict[str, float],
    action: Action,
    profiles: dict[str, dict],
    adj: pd.DataFrame,
    scenarios: list[Scenario] = SCENARIOS,
    proxy_map: dict[str, dict[str, float]] = PROXY_MAP,
    top_n: int = 5,
) -> PauseResult:
    after = apply_action(values, action)
    return PauseResult(
        action=action.describe(),
        before=view(values, profiles, adj, scenarios, proxy_map, top_n),
        after=view(after, profiles, adj, scenarios, proxy_map, top_n),
        history=market_events(adj, scenarios),
    )
