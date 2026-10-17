"""Intake agent (MODEL_FAST): pasted, messy text -> Portfolio.

Only used for pasted text; uploaded CSVs go through quant/parse.py with no AI.
The text is scrubbed of account-number and ID-like strings and wrapped in
<user_data> tags before it reaches the model. The model only proposes rows;
Python validates every ticker and number before building the Portfolio.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field

from secondlook.agents.base import AgentContext, AgentSpec, call, tagged
from secondlook.quant.models import CASH, TICKER_RE, Holding, Portfolio, RejectedRow
from secondlook.quant.parse import CASH_SYMBOLS

SPEC = AgentSpec(name="intake", model="fast", max_tokens=1200)
MAX_CHARS = 20_000

_ACCOUNT = re.compile(r"(?i)\b(acc(?:oun)?t\.?\s*(?:number|no\.?|num|#)?\s*[:#]?\s*)[A-Z0-9][A-Z0-9\-*xX]{3,}")
_SSN = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_LONG_ID = re.compile(r"\b(?=[A-Z0-9-]*\d)[A-Z]{0,4}\d{2,}-\d{3,}(?:-\d+)*\b")


class IntakeInput(BaseModel):
    text: str = Field(min_length=1)


class IntakeRow(BaseModel):
    symbol: str
    quantity: float | None = None
    price: float | None = None
    market_value: float | None = None


class IntakeOutput(BaseModel):
    rows: list[IntakeRow] = Field(max_length=500)


def scrub(text: str) -> str:
    """Remove account numbers and ID-like strings before the text leaves the app."""
    text = _ACCOUNT.sub(lambda m: m.group(1) + "[REDACTED]", text)
    text = _SSN.sub("[REDACTED]", text)
    return _LONG_ID.sub("[REDACTED]", text)


def to_portfolio(rows: list[IntakeRow]) -> Portfolio:
    """Python validation of the model's rows (same rules as the CSV parser)."""
    holdings, rejected = [], []
    for i, row in enumerate(rows, start=1):
        raw = row.symbol.strip().upper()
        is_cash = raw.endswith("**") or raw.rstrip("*") in CASH_SYMBOLS
        symbol = CASH if is_cash else raw
        value, qty, price = row.market_value, row.quantity, row.price
        if not TICKER_RE.fullmatch(symbol):
            rejected.append(RejectedRow(row_number=i, reason="invalid or missing ticker"))
            continue
        if value is None and qty is not None and price is not None:
            value = qty * price
        if qty is None and value is not None:
            qty = value / price if price else value if is_cash else None
        if value is None or qty is None or value < 0 or qty < 0:
            rejected.append(RejectedRow(row_number=i, reason="missing or negative quantity or value"))
            continue
        holdings.append(Holding(symbol=symbol, quantity=qty, price=1.0 if is_cash and price is None else price,
                                market_value=round(value, 2), account="Account 1", is_cash=is_cash))
    if not holdings:
        raise ValueError("No valid positions found in the pasted text.")
    return Portfolio(holdings=holdings, rejected=rejected)


def run(data: IntakeInput, ctx: AgentContext) -> Portfolio:
    if len(data.text) > MAX_CHARS:
        raise ValueError(f"Pasted text is too long (limit {MAX_CHARS:,} characters).")
    output: IntakeOutput = call(SPEC, tagged("user_data", scrub(data.text)), IntakeOutput, ctx)
    return to_portfolio(output.rows)
