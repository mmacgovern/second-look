"""Possible shifts: rule-based suggestions to move money between industries, asset types and account types.

Every suggestion is computed here in Python from the portfolio's numbers; the shifts agent only words
them for the reader's fluency level. Suggestions name sectors, asset types (stocks, bonds, cash, broad
index funds) and account types, never a specific stock, fund or other security.

Rules, in order:
  sector          a sector at least SECTOR_GAP_PTS above its S&P 500 weight (share of the user's stocks),
                  pointing toward the sectors furthest below the S&P 500
  asset_mix       stock share outside the planning rule-of-thumb range for the user's risk profile
  single_company  one company (counting funds) above the concentration threshold, without naming it
  fees            weighted fund fees well above index-fund levels
  account_placement  bonds held in Roth or taxable accounts while the household also has a pre-tax account
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel

from secondlook.config import PROJECT_ROOT
from secondlook.quant.accounts import ACCOUNT_TYPE_LABELS, PRE_TAX, Household
from secondlook.quant.benchmark import Benchmark, sector_rows
from secondlook.quant.concentration import CONCENTRATION_THRESHOLD
from secondlook.quant.fees import BASELINE_EXPENSE_RATIO, FeeReport
from secondlook.quant.models import CASH
from secondlook.quant.xray import XRay, sector_mix

RISK_RANGES_FILE = PROJECT_ROOT / "data" / "reference" / "risk_profile_ranges.json"
SECTOR_GAP_PTS = 8.0         # percentage points above the S&P 500 weight before a sector shift is suggested
UNDERWEIGHT_GAP_PTS = 3.0    # sectors at least this far below the S&P 500 are suggested as destinations
FEE_MULTIPLE = 2.0           # weighted fee at least this many times the index-fund baseline ...
FEE_MIN_EXTRA_30Y_USD = 1000  # ... and at least this much extra over 30 years
MIN_BONDS_USD = 1000
MAX_SHIFTS = 4


class Shift(BaseModel):
    kind: str
    title: str
    detail: str               # plain-English fallback wording; every number in it is in `numbers`
    numbers: dict[str, float | int | str]


def _pct(x: float) -> float:
    return round(100 * x, 1)


def _sector_shift(xr: XRay, bench: Benchmark) -> Shift | None:
    rows = sector_rows(xr, bench)
    over = [r for r in rows if 100 * r.difference >= SECTOR_GAP_PTS]
    if not over:
        return None
    top = max(over, key=lambda r: r.difference)
    under = sorted((r for r in rows if -100 * r.difference >= UNDERWEIGHT_GAP_PTS), key=lambda r: r.difference)[:2]
    toward = " and ".join(f"{r.sector} ({_pct(r.weight)}% vs. {_pct(r.sp500_weight)}%)" for r in under)
    detail = (f"Consider shifting part of your stock money out of {top.sector}: it is {_pct(top.weight)}% of your "
              f"stocks vs. {_pct(top.sp500_weight)}% of the S&P 500.")
    if toward:
        detail += f" Sectors furthest below the S&P 500 in your portfolio: {toward}."
    numbers = {"sector": top.sector, "you_pct": _pct(top.weight), "sp500_pct": _pct(top.sp500_weight)}
    for i, r in enumerate(under, 1):
        numbers.update({f"toward_{i}": r.sector, f"toward_{i}_you_pct": _pct(r.weight),
                        f"toward_{i}_sp500_pct": _pct(r.sp500_weight)})
    return Shift(kind="sector", title=f"Shift some stock money out of {top.sector}", detail=detail, numbers=numbers)


def _asset_mix_shift(xr: XRay, risk_label: str | None, ranges: dict | None) -> Shift | None:
    band = (ranges or {}).get("stock_pct", {}).get(risk_label or "")
    if not band:
        return None
    lo, hi = band
    stock = _pct(sum(v for k, v in xr.sectors.items() if k not in {"bonds", "cash", "other", "unclassified"}))
    if lo <= stock <= hi:
        return None
    numbers = {"stock_pct": stock, "range_low_pct": lo, "range_high_pct": hi, "risk_profile": risk_label}
    if stock > hi:
        return Shift(kind="asset_mix", title="Shift some stocks toward bonds or cash", numbers=numbers,
                     detail=f"Stocks are {stock}% of your portfolio, above the {lo}% to {hi}% planning rule of thumb "
                            f"for a {risk_label.lower()} profile. Moving some money into bonds or cash would bring it closer.")
    return Shift(kind="asset_mix", title="Shift some bonds or cash toward stocks", numbers=numbers,
                 detail=f"Stocks are {stock}% of your portfolio, below the {lo}% to {hi}% planning rule of thumb "
                        f"for a {risk_label.lower()} profile. Moving some money into broad stock funds would bring it closer.")


def _single_company_shift(xr: XRay, threshold: float) -> Shift | None:
    if not xr.top_companies or xr.top_companies[0].weight <= threshold:
        return None
    w = _pct(xr.top_companies[0].weight)
    return Shift(kind="single_company", title="Spread out single-company exposure",
                 numbers={"largest_company_pct": w, "threshold_pct": _pct(threshold)},
                 detail=f"One company is {w}% of your portfolio, counting the shares inside your funds, above the "
                        f"{_pct(threshold)}% concentration threshold. Broad index funds spread money across hundreds of companies.")


def _fee_shift(fees: FeeReport) -> Shift | None:
    extra = fees.projections[-1].cost_vs_baseline_usd if fees.projections else 0
    if fees.weighted_expense_ratio < FEE_MULTIPLE * BASELINE_EXPENSE_RATIO or extra < FEE_MIN_EXTRA_30Y_USD:
        return None
    er, base, years = round(100 * fees.weighted_expense_ratio, 3), round(100 * BASELINE_EXPENSE_RATIO, 3), fees.projections[-1].years
    return Shift(kind="fees", title="Shift from higher-cost funds toward low-cost index funds",
                 numbers={"weighted_fee_pct": er, "index_fee_pct": base, "extra_cost_usd": int(round(extra)), "years": years},
                 detail=f"Your funds charge a weighted {er}% a year vs. about {base}% for index funds. Over {years} years "
                        f"that difference is about ${int(round(extra)):,} (an estimate).")


def _account_shift(household: Household | None, profiles: dict[str, dict]) -> Shift | None:
    if household is None or not any(a.account_type in PRE_TAX for a in household.accounts):
        return None
    bonds = 0.0
    kinds = set()
    for account in household.accounts:
        if account.account_type in PRE_TAX:
            continue
        for h in account.holdings:
            if h.symbol == CASH or h.is_cash:
                continue
            b = h.market_value * sector_mix(profiles.get(h.symbol)).get("bonds", 0.0)
            if b > 0:
                bonds += b
                kinds.add(ACCOUNT_TYPE_LABELS[account.account_type])
    if bonds < MIN_BONDS_USD:
        return None
    where = " and ".join(sorted(kinds))
    return Shift(kind="account_placement", title="Hold bonds in pre-tax accounts",
                 numbers={"bonds_outside_pre_tax_usd": int(round(bonds)), "accounts": where},
                 detail=f"About ${int(round(bonds)):,} of bonds sit in your {where} account(s). A common approach is to hold "
                        "bonds in pre-tax accounts such as a 401(k) or traditional IRA and stocks in Roth or taxable accounts.")


def load_ranges(path: Path = RISK_RANGES_FILE) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def possible_shifts(xr: XRay, fees: FeeReport, bench: Benchmark, *, profiles: dict[str, dict],
                    household: Household | None = None, risk_label: str | None = None, ranges: dict | None = None,
                    threshold: float = CONCENTRATION_THRESHOLD) -> list[Shift]:
    """Up to MAX_SHIFTS suggestions, in rule order. An empty list means no rule fired."""
    found = [_sector_shift(xr, bench), _asset_mix_shift(xr, risk_label, ranges), _single_company_shift(xr, threshold),
             _fee_shift(fees), _account_shift(household, profiles)]
    return [s for s in found if s is not None][:MAX_SHIFTS]


def shifts_facts(shifts: list[Shift], bench: Benchmark) -> dict:
    """What the shifts agent sees: the computed suggestions and their numbers, nothing else."""
    return {
        "about": "Suggested shifts computed in Python. Word them; do not add new suggestions or numbers.",
        "reference": bench.label,
        "shifts": [{"kind": s.kind, "title": s.title, **s.numbers} for s in shifts],
    }


GENERIC_NAME_WORDS = {"the", "inc", "corp", "corporation", "company", "co", "class", "fund", "funds", "index", "trust",
                      "etf", "shares", "group", "holdings", "international", "american", "total", "stock", "market",
                      "bond", "portfolio", "admiral", "investor", "select", "sector", "spdr", "growth", "value"}


def banned_names(values: dict[str, float], profiles: dict[str, dict], bench: Benchmark | None = None) -> list[str]:
    """Tickers and distinctive name words of every security the user holds, their funds hold, or the S&P 500
    fund lists, which the Possible-shifts wording must never mention."""
    names: set[str] = set()

    def add(symbol: str | None, name: str | None) -> None:
        if symbol and symbol != CASH:
            names.add(symbol.upper())
        first = (name or "").replace(",", " ").replace(".", " ").split()
        if first and len(first[0]) >= 4 and first[0].lower() not in GENERIC_NAME_WORDS:
            names.add(first[0])

    for symbol in values:
        profile = profiles.get(symbol) or {}
        add(symbol, profile.get("name"))
        for h in profile.get("top_holdings") or []:
            add(h.get("symbol"), h.get("name"))
    for h in (bench.top_holdings if bench else []):
        add(h["symbol"], h.get("name"))
    if bench:
        add(bench.fund, bench.fund_name)
    return sorted(names)


def prepare(values: dict[str, float], profiles: dict[str, dict], bench: Benchmark, *, household: Household | None = None,
            risk_label: str | None = None) -> tuple[dict, list[str], list[str]]:
    """Everything the shifts agent needs, built the same way in the app and in demo recording
    (so the demo keys match): (shifts facts, Python wording per shift, names the wording must avoid)."""
    from secondlook.quant.fees import fee_report
    from secondlook.quant.xray import xray

    found = possible_shifts(xray(values, profiles), fee_report(values, profiles), bench, profiles=profiles,
                            household=household, risk_label=risk_label, ranges=load_ranges())
    return shifts_facts(found, bench), [s.detail for s in found], banned_names(values, profiles, bench)
