"""Data shaping for the Streamlit app: chart frames, the free-text action parser,
and the cost badge. Pure Python, so it is tested without a browser.

Every number shown in the app comes from the quant engine; this module only
reshapes it for charts and tables.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from secondlook import budget
from secondlook.quant.fees import FeeReport
from secondlook.quant.models import CASH, TICKER_RE, Action
from secondlook.quant.replay import Scenario, portfolio_path
from secondlook.quant.xray import XRay

SAMPLES: dict[str, tuple[str, str]] = {
    "beginner": ("Starting out", "About $15,000 in a Roth IRA"),
    "midcareer": ("Mid-career", "About $181,000 across a 401(k), a Roth IRA and a taxable account"),
    "retiree": ("Retired", "About $657,000 in traditional and Roth IRAs and a taxable account"),
}
FLUENCY: dict[str, str] = {"New to investing": "new", "Comfortable": "comfortable", "Pro": "pro"}
EXTRA_BUY_TICKERS = ("VOO", "VTI", "QQQ")  # offered alongside current holdings in "Buy more of a stock"


@dataclass
class Inputs:
    """Everything the app needs for one portfolio."""

    key: str                          # "sample:midcareer" or "upload:<hash>"
    label: str
    values: dict[str, float]
    profiles: dict[str, dict]
    adj: pd.DataFrame
    facts: dict
    sample: str | None = None
    extra: dict = field(default_factory=dict)


# --- X-Ray -------------------------------------------------------------------

def treemap_frame(xr: XRay, top: int = 20) -> pd.DataFrame:
    """Rows for a Plotly treemap (ids/labels/parents/values), dollars of the total portfolio.

    Each named company splits into where it comes from ("held directly", "via VOO", ...).
    Fund holdings that funds do not list, and cash, get their own tiles, so the
    tiles add up to the whole portfolio.
    """
    total = xr.total_value_usd
    rows = []
    shown = xr.top_companies[:top]
    for c in shown:
        for source, weight in c.via.items():
            rows.append({"id": f"{c.symbol}/{source}", "parent": c.symbol,
                         "label": "Held directly" if source == "direct" else f"Via {source}",
                         "value": weight * total, "kind": "part"})
        rows.append({"id": c.symbol, "parent": "Portfolio", "label": f"{c.name} ({c.symbol})",
                     "value": sum(w * total for w in c.via.values()), "kind": "company"})
    other = sum(c.value_usd for c in xr.top_companies[top:])
    if other > 0.5:
        rows.append({"id": "other", "parent": "Portfolio", "label": "Other named companies", "value": other, "kind": "other"})
    named = sum(c.value_usd for c in xr.top_companies)
    untraced = total * (1 - xr.cash_weight) - named
    if untraced > 0.5:
        rows.append({"id": "untraced", "parent": "Portfolio", "label": "Fund holdings not listed by the funds",
                     "value": untraced, "kind": "untraced"})
    cash = total * xr.cash_weight
    if cash > 0.5:
        rows.append({"id": "cash", "parent": "Portfolio", "label": "Cash", "value": cash, "kind": "cash"})
    root_value = sum(r["value"] for r in rows if r["parent"] == "Portfolio")
    rows.append({"id": "Portfolio", "parent": "", "label": "Your portfolio", "value": root_value, "kind": "root"})
    frame = pd.DataFrame(rows)
    frame["pct"] = frame["value"] / total * 100
    return frame


def overlap_table(xr: XRay, limit: int = 10) -> pd.DataFrame:
    rows = [{"Funds": f"{o.fund_a} and {o.fund_b}", "Shared holdings (%)": round(100 * o.overlap, 1)}
            for o in xr.overlaps[:limit] if o.overlap > 0]
    return pd.DataFrame(rows, columns=["Funds", "Shared holdings (%)"])


def company_table(xr: XRay, limit: int = 10) -> pd.DataFrame:
    rows = []
    for c in xr.top_companies[:limit]:
        rows.append({"Company": c.name, "Ticker": c.symbol, "Share (%)": round(100 * c.weight, 1),
                     "Dollars": round(c.value_usd), "Direct (%)": round(100 * c.via.get("direct", 0.0), 1)})
    return pd.DataFrame(rows)


# --- Fee Clock -----------------------------------------------------------------

def fee_curves(fees: FeeReport, years: int = 30) -> pd.DataFrame:
    """Value of today's balance year by year with no fees, index-fund fees, and this portfolio's fees."""
    v, g = fees.covered_value_usd, fees.gross_return
    rows = []
    for t in range(years + 1):
        rows.append({
            "year": t,
            "No fees": v * (1 + g) ** t,
            f"Index-fund fees ({100 * fees.baseline_expense_ratio:.2f}%)": v * (1 + g - fees.baseline_expense_ratio) ** t,
            f"Your fees ({100 * fees.weighted_expense_ratio:.2f}%)": v * (1 + g - fees.weighted_expense_ratio) ** t,
        })
    return pd.DataFrame(rows)


# --- Crash Replay --------------------------------------------------------------

def crash_frame(values: dict[str, float], adj: pd.DataFrame, scenario: Scenario, recovery_date: str | None,
                extra_months: int = 6) -> pd.DataFrame:
    """Daily portfolio value ($ at today's size) and drawdown (%) from the start of a crisis
    until it recovered (or until the window end + extra_months if it never did in the data)."""
    path, _, _ = portfolio_path(values, adj, scenario)
    end = pd.Timestamp(recovery_date) if recovery_date else pd.Timestamp(scenario.end)
    path = path[: end + pd.DateOffset(months=extra_months)]
    total = sum(values.values())
    return pd.DataFrame({
        "date": path.index,
        "value_usd": path.values * total,
        "drawdown_pct": (path / path.cummax() - 1).values * 100,
    })


# --- Pause Button --------------------------------------------------------------

_AMOUNT = r"\$?\s*(\d[\d,]*(?:\.\d+)?)\s*(k|thousand)?"


def _amount(number: str, scale: str | None) -> float:
    return float(number.replace(",", "")) * (1000 if scale else 1)


def parse_action(text: str, holdings: list[str]) -> Action | None:
    """Turn "sell everything", "sell 30% of VOO", "buy $5,000 of QQQ" or "move half to cash"
    into an Action. Returns None if the text is not one of these shapes."""
    t = " ".join(text.lower().split())
    if not t:
        return None
    held = {h.upper() for h in holdings}

    def ticker(word: str) -> str | None:
        word = word.upper().strip(".,!?")
        return word if TICKER_RE.fullmatch(word) and word not in {"ALL", "IT", "MY", "THE", "SOME", "MORE", "OF"} else None

    m = re.search(rf"\bbuy\s+(?:more\s+)?(?:of\s+)?{_AMOUNT}\s*(?:worth\s+)?(?:of\s+|in\s+)?(?:more\s+)?([a-z.\-]{{1,6}})\b", t)
    if m and ticker(m.group(3)):
        return Action(kind="buy", ticker=ticker(m.group(3)), amount_usd=_amount(m.group(1), m.group(2)))
    m = re.search(rf"\bbuy\s+(?:more\s+)?([a-z.\-]{{1,6}})\s+(?:for|with)\s+{_AMOUNT}", t)
    if m and ticker(m.group(1)):
        return Action(kind="buy", ticker=ticker(m.group(1)), amount_usd=_amount(m.group(2), m.group(3)))

    if re.search(r"\b(?:half|50 ?%)\b.*\bcash\b|\bmove half\b", t):
        return Action(kind="sell_pct", pct=50)
    m = re.search(r"\b(?:sell|move|put)\s+(\d{1,3}(?:\.\d+)?)\s*%(?:\s+of)?(?:\s+my)?(?:\s+([a-z.\-]{1,6}))?", t)
    if m and 0 < float(m.group(1)) <= 100:
        target = ticker(m.group(2)) if m.group(2) else None
        target = target if target in held else None
        return Action(kind="sell_pct", pct=float(m.group(1)), ticker=target)

    m = re.search(r"\bsell\s+(?:all\s+(?:of\s+)?)?(?:my\s+)?([a-z.\-]{1,6})\b", t)
    if m and ticker(m.group(1)) in held:
        return Action(kind="sell_all", ticker=ticker(m.group(1)))
    if re.search(r"\b(?:sell|dump|liquidate|cash out|get out)\b.*\b(?:everything|all|it all|the market|of it)\b"
                 r"|\b(?:sell everything|move (?:it all |everything )?to cash|go to cash|get out of the market)\b", t):
        return Action(kind="sell_all")
    return None


# --- Cost badge ------------------------------------------------------------------

@dataclass(frozen=True)
class Cost:
    calls: int
    tokens: int
    usd: float


def session_cost(session_id: str, db_path: Path | None = None) -> Cost:
    s = budget.summary(db_path, session_id=session_id)
    rows = s["by_agent"]
    return Cost(
        calls=sum(r["calls"] for r in rows),
        tokens=sum((r["input_tokens"] or 0) + (r["output_tokens"] or 0) for r in rows),
        usd=s["total_usd"],
    )


def friendly_note(note: str | None, demo_mode: bool) -> str | None:
    """Plain-language message for a fallback, or None when the AI answer was shown."""
    if not note:
        return None
    if "spending limit" in note:
        return ("This session has reached its AI spending limit, so you are seeing a saved explanation "
                "of the same numbers. Nothing was charged for it.")
    if demo_mode:
        return ("You are in demo mode and there is no saved AI explanation for this exact case, so this "
                "is the standard summary built directly from the numbers.")
    return "The AI explanation was not available just now, so this is the standard summary built directly from the numbers."


# Query parameters tied to widgets with bind="query-params"; Streamlit refuses to clear these directly.
WIDGET_PARAMS = frozenset({"fluency", "tab"})


def params_to_reset(keys) -> list[str]:
    """Query parameters to drop when a new portfolio starts, keeping widget-bound ones (fluency, tab)."""
    return [k for k in keys if k not in WIDGET_PARAMS]


def holdings_for_buy(values: dict[str, float]) -> list[str]:
    return list(dict.fromkeys([t for t in sorted(values, key=values.get, reverse=True) if t != CASH] + list(EXTRA_BUY_TICKERS)))


# --- Retirement Plan tab -----------------------------------------------------------

PLAN_LABEL = "Planning estimate, not a guarantee."
RISK_RANGES_FILE = Path(__file__).resolve().parents[1] / "data" / "reference" / "risk_profile_ranges.json"
NON_STOCK = {"bonds", "cash", "other", "unclassified"}


def plan_band_frame(plan) -> pd.DataFrame:
    """Balance by age for each scenario plus the target, for the band chart."""
    frame = pd.DataFrame({"age": [p.age for p in plan.scenarios[0].balances]})
    for s in plan.scenarios:
        frame[s.name] = [p.balance_usd for p in s.balances]
    frame["target"] = plan.target_nest_egg_usd
    return frame


def gap_headline(plan, desired_income: float) -> tuple[str, float, float]:
    """Headline for the plan tab and its low/high dollars per year.

    Saving: extra yearly saving needed beyond today's contribution, across the scenarios.
    Retired or retiring now: yearly income shortfall versus the goal, across the scenarios.
    """
    if plan.status == "saving":
        extra = [max(0.0, (s.required_annual_contribution_usd or 0.0) - plan.annual_contribution_usd) for s in plan.scenarios]
        what = "of extra saving per year"
    else:
        extra = [max(0.0, desired_income - s.supported_income_usd) for s in plan.scenarios]
        what = "of income per year"
    lo, hi = min(extra), max(extra)
    if hi <= 0:
        return "On track in every scenario", 0.0, 0.0
    if round(lo) == round(hi):
        return f"Estimated gap: ${hi:,.0f} {what}", lo, hi
    return f"Estimated gap: ${lo:,.0f} to ${hi:,.0f} {what}", lo, hi


def balances_frame(balances_by_type: dict[str, float], tax_rate: float) -> pd.DataFrame:
    from secondlook.quant.accounts import ACCOUNT_TYPE_LABELS, PRE_TAX

    rows = [{"Account type": ACCOUNT_TYPE_LABELS[t], "Balance": v,
             "Counted toward retirement": v * (1 - tax_rate) if t in PRE_TAX else v}
            for t, v in sorted(balances_by_type.items(), key=lambda kv: kv[1], reverse=True)]
    return pd.DataFrame(rows, columns=["Account type", "Balance", "Counted toward retirement"])


def asset_mix(xr: XRay) -> dict[str, float]:
    """Stocks / bonds / cash / other as percent of the portfolio, from the look-through sectors."""
    s = xr.sectors
    return {
        "Stocks": 100 * sum(v for k, v in s.items() if k not in NON_STOCK),
        "Bonds": 100 * s.get("bonds", 0.0),
        "Cash": 100 * s.get("cash", 0.0),
        "Other": 100 * (s.get("other", 0.0) + s.get("unclassified", 0.0)),
    }


def load_risk_ranges(path: Path = RISK_RANGES_FILE) -> dict:
    import json

    return json.loads(Path(path).read_text(encoding="utf-8"))


def mix_vs_range(stock_pct: float, profile_label: str, ranges: dict) -> tuple[tuple[float, float] | None, str]:
    """The reference stock range for a risk profile and where the portfolio sits: below, within or above."""
    band = ranges.get("stock_pct", {}).get(profile_label)
    if not band:
        return None, "no reference"
    lo, hi = band
    position = "below" if stock_pct < lo else "above" if stock_pct > hi else "within"
    return (float(lo), float(hi)), position
