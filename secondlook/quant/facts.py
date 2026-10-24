"""Combine the quant results into one compact, labeled Facts JSON for the LLM to explain.

Every number is rounded, and its key says the unit (_pct, _usd, _months,
_years). Percentages are of the total portfolio unless the key says otherwise.
Every assumption used is listed under "assumptions". The JSON is kept under
TOKEN_TARGET by trimming list lengths if needed.
"""

from __future__ import annotations

import json
import math

import pandas as pd

from secondlook.quant.accounts import ACCOUNT_TYPE_LABELS, RETIREMENT_TAX_RATE, AccountSummary, Household, summarize
from secondlook.quant.benchmark import BENCHMARK_FILE, Benchmark, facts_block, load_benchmark
from secondlook.quant.concentration import CONCENTRATION_THRESHOLD, Concentration, concentration, stock_sectors
from secondlook.quant.fees import BASELINE_EXPENSE_RATIO, fee_report
from secondlook.quant.models import CASH, Action, Portfolio
from secondlook.quant.pause import PauseResult, pause_check
from secondlook.quant.plan import ASSUMPTIONS, PlanResult, make_plan
from secondlook.quant.profile import GlidePath, RiskProfile, UserProfile, load_glide_path, risk_profile
from secondlook.quant.replay import PROXY_MAP, SCENARIOS, ScenarioResult, replay
from secondlook.quant.xray import SECTOR_LABELS, XRay, xray

TOKEN_TARGET = 2000

# List lengths, from most to least detailed; the first level that fits wins.
DETAIL_LEVELS = [
    {"positions": 5, "companies": 10, "via": 10, "sectors": 8, "overlaps": 5, "funds": 3, "flags": 5},
    {"positions": 5, "companies": 8, "via": 5, "sectors": 6, "overlaps": 3, "funds": 3, "flags": 5},
    {"positions": 3, "companies": 5, "via": 3, "sectors": 5, "overlaps": 3, "funds": 2, "flags": 3},
    {"positions": 3, "companies": 5, "via": 0, "sectors": 4, "overlaps": 2, "funds": 1, "flags": 3},
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


def _pause_context(user: UserProfile, risk: RiskProfile | None, result: PauseResult) -> dict:
    """Time horizon and risk profile next to how long past recoveries took, for the Pause Button."""
    months = [e.months_bottom_to_recovery for e in result.history if e.months_bottom_to_recovery is not None]
    out = {
        "years_to_retirement": user.years_to_retirement,
        "status": "retired" if user.is_retired else "saving",
        "slowest_recovery_shown_months": max(months) if months else None,
        "fastest_recovery_shown_months": min(months) if months else None,
    }
    if risk is not None:
        out["risk_profile"] = risk.label
    return out


def _concentration(c: Concentration, n_flags: int) -> dict:
    out = {
        "largest_company": {"ticker": c.largest_company.symbol, "pct": pct(c.largest_company.weight)}
        if c.largest_company else None,
        "effective_holdings": round(c.effective_holdings, 1) if c.effective_holdings else None,
        "effective_companies_upper_bound": round(c.effective_companies_upper_bound, 1)
        if c.effective_companies_upper_bound else None,
        "companies_over_threshold": [
            {"ticker": f.ticker, "pct": pct(f.weight), "direct_pct": pct(f.direct_weight)} for f in c.flags[:n_flags]
        ],
        "stock_sectors_vs_market_pct_of_stocks": [
            {"sector": sector_name(s.sector), "portfolio": pct(s.portfolio), "market": pct(s.reference),
             "difference": pct(s.difference)}
            for s in c.sectors_vs_market
        ],
        "market_reference": c.reference,
    }
    if c.employer_stock is not None:
        e = c.employer_stock
        out["employer_stock"] = {"ticker": e.ticker, "pct": pct(e.weight), "direct_pct": pct(e.direct_weight),
                                 "over_threshold": e.over_threshold}
    return out


def _investor(user: UserProfile, risk: RiskProfile | None, xr: XRay, glide: GlidePath | None) -> dict:
    stock_share = sum(xr.sectors.get(k, 0.0) for k in stock_sectors(xr.sectors))
    out = {
        "age": user.age,
        "retirement_age": user.retirement_age,
        "years_to_retirement": user.years_to_retirement,
        "portfolio_stock_pct": pct(stock_share),
    }
    if risk is not None:
        out.update(risk_profile=risk.label, capacity_1to5=risk.capacity, tolerance_1to5=risk.tolerance,
                   risk_level_1to5=risk.level)
    reference = glide.stock_share_for(user.retirement_age - user.age) if glide is not None else None
    if reference is None:
        out["reference_glide_path"] = "not filled in yet"
    else:
        out["reference_stock_pct"] = pct(reference)
        out["reference_source"] = glide.source
    return out


def _plan(p: PlanResult, user: UserProfile) -> dict:
    return {
        "status": p.status,
        "desired_income_usd": usd(user.desired_income_today_usd),
        "social_security_or_pension_usd": usd(user.guaranteed_income_usd),
        "income_needed_from_portfolio_usd": usd(p.income_needed_from_portfolio_usd),
        "target_nest_egg_usd": usd(p.target_nest_egg_usd),
        "counted_balance_usd": usd(p.counted_balance_usd),
        "annual_contribution_usd": usd(p.annual_contribution_usd),
        "scenarios": [
            {
                "scenario": s.name,
                "net_return_pct": pct(s.net_return, 2),
                "projected_at_retirement_usd": usd(s.projected_at_retirement_usd),
                "gap_usd": usd(s.gap_usd),
                "on_track": s.on_track,
                "required_annual_contribution_usd": usd(s.required_annual_contribution_usd),
                "supported_income_usd": usd(s.supported_income_usd),
            }
            for s in p.scenarios
        ],
    }


def _accounts(a: AccountSummary) -> dict:
    return {
        "by_type_usd": {ACCOUNT_TYPE_LABELS[t]: usd(v) for t, v in a.balances_by_type.items()},
        "counted_for_retirement_usd": usd(a.counted_usd),
    }


def _assumptions(fees, plan: PlanResult | None, threshold: float, tax_rate: float) -> dict:
    out = {
        "all_dollars": "today's dollars" if plan else "current market value",
        "fee_projection_gross_return_pct": pct(fees.gross_return),
        "fee_baseline_expense_ratio_pct": pct(BASELINE_EXPENSE_RATIO, 3),
        "concentration_threshold_pct": pct(threshold),
        "crash_replay_method": "today's weights held constant; missing history uses stated proxies",
    }
    if plan is not None:
        out.update(
            withdrawal_rate_pct=pct(plan.withdrawal_rate, 2),
            real_returns_before_fees_pct={k: pct(v) for k, v in plan.assumptions["real_returns"].items()},
            fund_expense_ratio_subtracted_pct=pct(plan.expense_ratio, 3),
            pre_tax_accounts_reduced_by_pct=pct(tax_rate),
            contributions=plan.assumptions["contributions"],
        )
    return out


def assemble(portfolio: Portfolio, xr: XRay, fees, replays, pause, level: dict, extras: dict) -> dict:
    values = portfolio.values_by_symbol()
    total = portfolio.total_value
    largest = sorted(((s, v) for s, v in values.items() if s != CASH), key=lambda kv: kv[1], reverse=True)
    facts = {
        "about": "Computed in Python from the user's files and market data. pct = percent of total portfolio unless the key says otherwise.",
        "assumptions": _assumptions(fees, extras.get("plan"), extras["threshold"], extras["tax_rate"]),
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
        "concentration": _concentration(extras["concentration"], level["flags"]),
        "fees": {
            "weighted_expense_ratio_pct": pct(fees.weighted_expense_ratio, 3),
            "annual_fees_usd": usd(fees.annual_cost_usd),
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
    if extras.get("accounts") is not None:
        facts["accounts"] = _accounts(extras["accounts"])
    if extras.get("user") is not None:
        facts["investor"] = _investor(extras["user"], extras.get("risk"), xr, extras.get("glide"))
        facts["retirement_plan"] = _plan(extras["plan"], extras["user"])
    if extras.get("benchmark") is not None:
        facts["look_through"]["vs_sp500"] = facts_block(xr, extras["benchmark"], 3 if level["sectors"] >= 6 else 2)
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
        if extras.get("user") is not None:
            facts["pause_check"]["investor_context"] = _pause_context(extras["user"], extras.get("risk"), pause)
    return facts


def build_facts(
    portfolio: Portfolio | None,
    profiles: dict[str, dict],
    adj: pd.DataFrame,
    action: Action | None = None,
    gross_return: float = 0.06,
    token_target: int = TOKEN_TARGET,
    *,
    household: Household | None = None,
    user: UserProfile | None = None,
    risk_answers: list[int] | None = None,
    withdrawal_rate: float = ASSUMPTIONS["withdrawal_rate"],
    real_returns: dict[str, float] | None = None,
    tax_rate: float = RETIREMENT_TAX_RATE,
    threshold: float = CONCENTRATION_THRESHOLD,
    market_sectors: dict[str, float] | None = None,
    glide_path: GlidePath | None = None,
    benchmark: Benchmark | None = None,
) -> dict:
    """Facts for one portfolio, or for a household (all accounts merged) plus an optional plan."""
    if household is not None:
        portfolio = household.portfolio()
    if portfolio is None:
        raise ValueError("Pass a portfolio or a household")
    values = portfolio.values_by_symbol()
    xr = xray(values, profiles)
    fees = fee_report(values, profiles, gross_return=gross_return)
    replays = replay(values, adj, SCENARIOS, PROXY_MAP)
    pause = pause_check(values, action, profiles, adj) if action is not None else None

    if benchmark is None and BENCHMARK_FILE.exists():
        benchmark = load_benchmark()
    extras: dict = {
        "benchmark": benchmark,
        "threshold": threshold,
        "tax_rate": tax_rate,
        "concentration": concentration(values, profiles, reference=market_sectors, threshold=threshold,
                                       employer_ticker=user.employer_ticker if user is not None else None),
    }
    if household is not None:
        extras["accounts"] = summarize(household, tax_rate)
    if user is not None:
        counted = extras["accounts"].counted_usd if household is not None else portfolio.total_value
        extras.update(
            user=user,
            risk=risk_profile(user, risk_answers) if risk_answers is not None else None,
            glide=glide_path if glide_path is not None else load_glide_path(),
            plan=make_plan(user, counted, fees.weighted_expense_ratio, withdrawal_rate, real_returns, tax_rate),
        )

    facts = {}
    for level in DETAIL_LEVELS:
        facts = assemble(portfolio, xr, fees, replays, pause, level, extras)
        if estimate_tokens(facts_json(facts)) <= token_target:
            break
    return facts


def facts_json(facts: dict) -> str:
    return json.dumps(facts, separators=(",", ":"), ensure_ascii=False)


def load_market_inputs(
    portfolio: Portfolio, prefer_live: bool = False, extra: list[str] | tuple[str, ...] = (), progress=None,
) -> tuple[dict[str, dict], pd.DataFrame]:
    """Profiles for every holding (plus `extra` tickers, e.g. one being bought) and adjusted
    closes for those and the replay proxies. progress(done, total, label) is called before each
    fetch, so the app can show a live count."""
    from secondlook import market_data as md

    symbols = list(dict.fromkeys([s for s in portfolio.values_by_symbol() if s != CASH] + [t.upper() for t in extra]))
    proxy_tickers = {p for proxy in PROXY_MAP.values() for p in proxy} | {"SPY"}
    price_tickers = list(dict.fromkeys(t.upper() for t in symbols + sorted(proxy_tickers) if t.upper() not in md.CASH_SYMBOLS))
    total, done = len(symbols) + len(price_tickers), 0

    def step(label: str) -> None:
        nonlocal done
        if progress is not None:
            progress(done, total, label)
        done += 1

    profiles = {}
    for symbol in symbols:
        step(f"fund and company data for {symbol}")
        try:
            profiles[symbol], _ = md.get_profile(symbol, prefer_live=prefer_live)
        except md.DataUnavailable:
            continue
    prices = md.get_prices(price_tickers, prefer_live=prefer_live, on_ticker=lambda t: step(f"price history for {t}"))
    if progress is not None:
        progress(total, total, "done")
    return profiles, prices.adj_close
