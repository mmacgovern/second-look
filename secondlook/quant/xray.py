"""Look-through exposure: what companies and sectors a portfolio really owns.

Each fund is expanded into its reported holdings by weight and combined with
directly held stocks. Profiles use the snapshot format from market_data
(quote_type, sector, top_holdings, sector_weights, asset_classes).

Fund holdings are usually only the top 10, so company exposures and fund
overlaps are minimums. `coverage` says how much of the invested (non-cash)
portfolio could be traced to individual companies.
"""

from __future__ import annotations

import re
from collections import defaultdict
from itertools import combinations

from pydantic import BaseModel

from secondlook.quant.models import CASH

FUND_TYPES = {"ETF", "MUTUALFUND"}
STOCK_TYPES = {"EQUITY"}

# Share classes of the same company are combined.
COMPANY_ALIASES = {"GOOG": "GOOGL", "BRK.A": "BRK.B"}

# Holdings that are themselves funds (target-date funds, cash sweeps) are not companies.
SUBFUND_NAME = re.compile(r"\b(funds?|idx|index|etf|trust)\b", re.IGNORECASE)

SECTOR_LABELS = {
    "technology": "Technology",
    "communication_services": "Communication services",
    "consumer_cyclical": "Consumer cyclical",
    "consumer_defensive": "Consumer defensive",
    "financial_services": "Financial services",
    "healthcare": "Healthcare",
    "industrials": "Industrials",
    "energy": "Energy",
    "utilities": "Utilities",
    "realestate": "Real estate",
    "basic_materials": "Basic materials",
    "bonds": "Bonds",
    "cash": "Cash",
    "other": "Other assets",
    "unclassified": "Unclassified",
}


class CompanyExposure(BaseModel):
    symbol: str
    name: str
    weight: float                 # fraction of the total portfolio
    value_usd: float
    via: dict[str, float]         # "direct" or fund ticker -> fraction of the total portfolio


class FundOverlap(BaseModel):
    fund_a: str
    fund_b: str
    overlap: float                # sum over shared holdings of min(weight in A, weight in B)


class XRay(BaseModel):
    total_value_usd: float
    cash_weight: float
    top_companies: list[CompanyExposure]
    sectors: dict[str, float]     # sector key -> fraction of the total portfolio, largest first
    overlaps: list[FundOverlap]   # largest first
    coverage: float               # fraction of the invested portfolio traced to companies
    funds_without_holdings: list[str]
    unknown_symbols: list[str]


def company_key(symbol: str) -> str:
    key = symbol.strip().upper().replace("-", ".").replace("/", ".")
    return COMPANY_ALIASES.get(key, key)


def sector_key(name: str | None) -> str:
    if not name:
        return "unclassified"
    key = re.sub(r"[^a-z]+", "_", name.lower()).strip("_")
    return {"real_estate": "realestate", "financial": "financial_services"}.get(key, key)


def classify(profile: dict | None) -> str:
    quote_type = (profile or {}).get("quote_type")
    if quote_type in FUND_TYPES:
        return "fund"
    if quote_type in STOCK_TYPES:
        return "stock"
    return "unknown"


def fund_holdings(profile: dict, profiles: dict[str, dict], depth: int = 1) -> tuple[dict[str, float], dict[str, str]]:
    """Company -> weight inside this fund, expanding sub-funds we have profiles for (one level)."""
    weights: dict[str, float] = defaultdict(float)
    names: dict[str, str] = {}
    for holding in profile.get("top_holdings") or []:
        weight = holding.get("weight") or 0.0
        symbol = (holding.get("symbol") or "").upper()
        if weight <= 0 or not symbol:
            continue
        if SUBFUND_NAME.search(holding.get("name") or ""):
            sub = profiles.get(symbol)
            if depth > 0 and classify(sub) == "fund":
                sub_weights, sub_names = fund_holdings(sub, profiles, depth - 1)
                for key, sub_weight in sub_weights.items():
                    weights[key] += weight * sub_weight
                names.update(sub_names)
            continue
        key = company_key(symbol)
        weights[key] += weight
        names.setdefault(key, holding.get("name") or key)
    return dict(weights), names


def asset_mix(profile: dict) -> dict[str, float]:
    """Stock / bond / cash / other fractions of a fund, negatives clipped and rescaled to sum to 1."""
    ac = profile.get("asset_classes") or {}
    if not ac:
        return {"stock": 1.0} if profile.get("sector_weights") else {}
    mix = {
        "stock": ac.get("stockPosition") or 0.0,
        "bond": ac.get("bondPosition") or 0.0,
        "cash": ac.get("cashPosition") or 0.0,
        "other": sum(ac.get(k) or 0.0 for k in ("otherPosition", "preferredPosition", "convertiblePosition")),
    }
    mix = {k: max(v, 0.0) for k, v in mix.items()}
    total = sum(mix.values())
    return {k: v / total for k, v in mix.items()} if total else {}


def sector_mix(profile: dict | None) -> dict[str, float]:
    """Sector fractions of one holding (sum to 1)."""
    kind = classify(profile)
    if kind == "stock":
        return {sector_key(profile.get("sector")): 1.0}
    if kind != "fund":
        return {"unclassified": 1.0}
    mix = asset_mix(profile)
    if not mix:
        return {"unclassified": 1.0}
    raw = {sector_key(k): v or 0.0 for k, v in (profile.get("sector_weights") or {}).items()}
    raw_total = sum(raw.values())
    out: dict[str, float] = defaultdict(float)
    if raw_total > 0:
        for key, value in raw.items():
            out[key] += mix["stock"] * value / raw_total
    else:
        out["unclassified"] += mix["stock"]
    out["bonds"] += mix["bond"]
    out["cash"] += mix["cash"]
    out["other"] += mix["other"]
    return {k: v for k, v in out.items() if v > 0}


def overlap(a: dict[str, float], b: dict[str, float]) -> float:
    return sum(min(a[k], b[k]) for k in a.keys() & b.keys())


def xray(values: dict[str, float], profiles: dict[str, dict], top_n: int = 10) -> XRay:
    """Look-through exposure for a portfolio given market value per symbol."""
    total = sum(values.values())
    if total <= 0:
        raise ValueError("Portfolio has no value.")
    cash_weight = values.get(CASH, 0.0) / total
    invested_weight = 1.0 - cash_weight

    exposure: dict[str, float] = defaultdict(float)
    via: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    names: dict[str, str] = {}
    sectors: dict[str, float] = defaultdict(float)
    fund_weights: dict[str, dict[str, float]] = {}
    looked_through = 0.0
    funds_without_holdings, unknown = [], []

    for symbol, value in values.items():
        w = value / total
        if symbol == CASH:
            sectors["cash"] += w
            continue
        profile = profiles.get(symbol)
        for key, frac in sector_mix(profile).items():
            sectors[key] += w * frac
        kind = classify(profile)
        if kind == "stock":
            key = company_key(symbol)
            exposure[key] += w
            via[key]["direct"] += w
            names[key] = profile.get("name") or key
            looked_through += w
        elif kind == "fund":
            holdings, holding_names = fund_holdings(profile, profiles)
            if not holdings:
                funds_without_holdings.append(symbol)
                continue
            fund_weights[symbol] = holdings
            for key, hw in holdings.items():
                exposure[key] += w * hw
                via[key][symbol] += w * hw
                names.setdefault(key, holding_names.get(key, key))
            looked_through += w * sum(holdings.values())
        else:
            unknown.append(symbol)

    ranked = sorted(exposure.items(), key=lambda kv: kv[1], reverse=True)[:top_n]
    top = [
        CompanyExposure(
            symbol=key,
            name=names.get(key, key),
            weight=weight,
            value_usd=weight * total,
            via=dict(sorted(via[key].items(), key=lambda kv: kv[1], reverse=True)),
        )
        for key, weight in ranked
    ]
    overlaps = sorted(
        (FundOverlap(fund_a=a, fund_b=b, overlap=overlap(fund_weights[a], fund_weights[b]))
         for a, b in combinations(sorted(fund_weights), 2)),
        key=lambda o: o.overlap,
        reverse=True,
    )
    return XRay(
        total_value_usd=total,
        cash_weight=cash_weight,
        top_companies=top,
        sectors=dict(sorted(sectors.items(), key=lambda kv: kv[1], reverse=True)),
        overlaps=overlaps,
        coverage=looked_through / invested_weight if invested_weight > 0 else 0.0,
        funds_without_holdings=funds_without_holdings,
        unknown_symbols=unknown,
    )
