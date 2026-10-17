"""Combine the quant results into one compact, labeled Facts JSON for the LLM to explain.

Every number is rounded, and its key says the unit (_pct, _usd, _months,
_years). Percentages are of the total portfolio unless the key says otherwise.
The JSON is kept under TOKEN_TARGET by trimming list lengths if needed.
"""

from __future__ import annotations

import json
import math

import pandas as pd

from secondlook.quant.fees import fee_report
from secondlook.quant.models import CASH, Action, Portfolio
from secondlook.quant.pause import PauseResult, pause_check
from secondlook.quant.replay import PROXY_MAP, SCENARIOS, ScenarioResult, replay
from secondlook.quant.xray import SECTOR_LABELS, XRay, xray

TOKEN_TARGET = 1500

# List lengths, from most to least detailed; the first level that fits wins.
DETAIL_LEVELS = [
    {"positions": 5, "companies": 10, "via": 10, "sectors": 8, "overlaps": 5, "funds": 3},
    {"positions": 5, "companies": 8, "via": 5, "sectors": 6, "overlaps": 3, "funds": 3},
    {"positions": 3, "companies": 5, "via": 3, "sectors": 5, "overlaps": 3, "funds": 2},
    {"positions": 3, "companies": 5, "via": 0, "sectors": 4, "overlaps": 2, "funds": 1},
]


def estimate_tokens(text: str) -> int:
    return math.ceil(len(text) / 4)


def pct(fraction: float | None, digits: int = 1) -> float | None:
    return None if fraction is None else round(100 * fraction, digits)


def usd(amount: float | None) -> int | None:
    return None if amount is None else int(round(amount))


def sector_name(key: str) -> str:
    return SECTOR_LABELS.get(key, key.replace("_", " ").capitalize())


def _companies(xr_companies, n: int, via_n: int) -> list[dict]:
    out = []
    for i, c in enumerate(xr_companies[:n]):
        item = {"ticker": c.symbol, "name": c.name, "pct": pct(c.weight), "usd": usd(c.value_usd)}
        if i < via_n and len(c.via) > 1:
            item["via_pct"] = {k: pct(v) for k, v in c.via.items()}
        out.append(item)
    return out


def _sectors(sectors: dict[str, float], n: int) -> dict[str, float]:
    return {sector_name(k): pct(v) for k, v in list(sectors.items())[:n]}


def _replays(results: list[ScenarioResult], with_proxies: bool = True) -> list[dict]:
    out = []
    for r in results:
        item = {
            "scenario": r.label,
            "window": f"{r.start[:7]} to {r.end[:7]}",
            "max_drawdown_pct": pct(r.max_drawdown),
            "max_drawdown_usd": usd(r.max_drawdown_usd),
            "months_to_recover": r.months_to_recover,
        }
        if r.months_to_recover is None:
            item["recovered"] = False
        if with_proxies and r.proxies_used:
            item["proxies"] = r.proxies_used
        if r.excluded:
            item["left_out"] = r.excluded
        out.append(item)
    return out


def _pause(result: PauseResult, n: int) -> dict:
    def side(v) -> dict:
        return {
            "total_usd": usd(v.total_value_usd),
            "cash_pct": pct(v.cash_weight),
            "top_companies_pct": {c.symbol: pct(c.weight) for c in v.top_companies[:n]},
            "top_sectors_pct": _sectors(v.sectors, n),
            "max_drawdown_pct": {r.key: pct(r.max_drawdown) for r in v.replay},
            "max_drawdown_usd": {r.key: usd(r.max_drawdown_usd) for r in v.replay},
        }

    return {
        "proposed_action": result.action,
        "before": side(result.before),
        "after": side(result.after),
        "market_history": [
            {
                "event": e.event,
                "peak_to_bottom_drop_pct": pct(e.drop),
                "bottom_date": e.bottom_date,
                "gain_1y_after_bottom_pct": pct(e.gain_1y_after_bottom),
                "gain_3y_after_bottom_pct": pct(e.gain_3y_after_bottom),
                "months_bottom_to_recovery": e.months_bottom_to_recovery,
            }
            for e in result.history
        ],
    }


def assemble(portfolio: Portfolio, xr: XRay, fees, replays, pause: PauseResult | None, level: dict) -> dict:
    values = portfolio.values_by_symbol()
    total = portfolio.total_value
    largest = sorted(((s, v) for s, v in values.items() if s != CASH), key=lambda kv: kv[1], reverse=True)
    facts = {
        "about": "Computed in Python from the user's file and market data. pct = percent of total portfolio unless the key says otherwise.",
        "portfolio": {
            "total_usd": usd(total),
            "holdings_count": len([s for s in values if s != CASH]),
            "accounts_count": len(portfolio.accounts),
            "cash_pct": pct(values.get(CASH, 0.0) / total),
            "largest_holdings": [{"ticker": s, "pct": pct(v / total), "usd": usd(v)} for s, v in largest[: level["positions"]]],
        },
        "look_through": {
            "coverage_pct_of_invested": pct(xr.coverage),
            "note": "Funds report only top holdings, so company exposures and overlaps are minimums.",
            "top_companies": _companies(xr.top_companies, level["companies"], level["via"]),
            "sectors_pct": _sectors(xr.sectors, level["sectors"]),
            "fund_overlap_pct": [
                {"funds": f"{o.fund_a}+{o.fund_b}", "overlap_pct": pct(o.overlap)}
                for o in xr.overlaps[: level["overlaps"]]
                if o.overlap > 0
            ],
        },
        "fees": {
            "weighted_expense_ratio_pct": pct(fees.weighted_expense_ratio, 3),
            "annual_fees_usd": usd(fees.annual_cost_usd),
            "baseline_expense_ratio_pct": pct(fees.baseline_expense_ratio, 3),
            "assumed_gross_return_pct": pct(fees.gross_return),
            "projection": [
                {
                    "years": p.years,
                    "fees_cost_usd": usd(p.cost_vs_no_fees_usd),
                    "extra_vs_baseline_usd": usd(p.cost_vs_baseline_usd),
                }
                for p in fees.projections
            ],
            "costliest_funds": [
                {"ticker": f.symbol, "expense_ratio_pct": pct(f.expense_ratio, 3), "annual_usd": usd(f.annual_cost_usd)}
                for f in fees.funds[: level["funds"]]
            ],
        },
        "crash_replays": _replays(replays, with_proxies=level["via"] > 0),
    }
    if xr.funds_without_holdings:
        facts["look_through"]["funds_without_holdings"] = xr.funds_without_holdings
    if xr.unknown_symbols:
        facts["look_through"]["unknown_tickers"] = xr.unknown_symbols
    if fees.unknown_symbols:
        facts["fees"]["no_expense_ratio"] = fees.unknown_symbols
    if portfolio.rejected:
        facts["portfolio"]["rows_skipped"] = len(portfolio.rejected)
    if pause is not None:
        facts["pause_check"] = _pause(pause, 3 if level["companies"] > 5 else 2)
    return facts


def build_facts(
    portfolio: Portfolio,
    profiles: dict[str, dict],
    adj: pd.DataFrame,
    action: Action | None = None,
    gross_return: float = 0.06,
    token_target: int = TOKEN_TARGET,
) -> dict:
    values = portfolio.values_by_symbol()
    xr = xray(values, profiles)
    fees = fee_report(values, profiles, gross_return=gross_return)
    replays = replay(values, adj, SCENARIOS, PROXY_MAP)
    pause = pause_check(values, action, profiles, adj) if action is not None else None

    facts = {}
    for level in DETAIL_LEVELS:
        facts = assemble(portfolio, xr, fees, replays, pause, level)
        if estimate_tokens(facts_json(facts)) <= token_target:
            break
    return facts


def facts_json(facts: dict) -> str:
    return json.dumps(facts, separators=(",", ":"), ensure_ascii=False)


def load_market_inputs(portfolio: Portfolio, prefer_live: bool = False) -> tuple[dict[str, dict], pd.DataFrame]:
    """Profiles for every holding plus adjusted closes for holdings and proxies."""
    from secondlook import market_data as md

    symbols = [s for s in portfolio.values_by_symbol() if s != CASH]
    profiles = {}
    for symbol in symbols:
        try:
            profiles[symbol], _ = md.get_profile(symbol, prefer_live=prefer_live)
        except md.DataUnavailable:
            continue
    proxy_tickers = {p for proxy in PROXY_MAP.values() for p in proxy} | {"SPY"}
    prices = md.get_prices(symbols + sorted(proxy_tickers), prefer_live=prefer_live)
    return profiles, prices.adj_close
