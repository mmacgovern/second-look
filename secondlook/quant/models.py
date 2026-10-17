"""Shared pydantic models for the quant engine."""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

TICKER_RE = re.compile(r"^[A-Z.\-]{1,6}$")
CASH = "CASH"


class Holding(BaseModel):
    symbol: str
    quantity: float
    price: float | None = None
    market_value: float = Field(ge=0)
    account: str  # redacted label such as "Account 1", never the real number
    is_cash: bool = False

    @field_validator("symbol")
    @classmethod
    def _valid_symbol(cls, value: str) -> str:
        if not TICKER_RE.fullmatch(value):
            raise ValueError("invalid ticker")
        return value


class RejectedRow(BaseModel):
    row_number: int  # 1-based line number in the uploaded file
    reason: str


class Portfolio(BaseModel):
    holdings: list[Holding]
    rejected: list[RejectedRow] = []

    @property
    def total_value(self) -> float:
        return sum(h.market_value for h in self.holdings)

    @property
    def accounts(self) -> list[str]:
        return list(dict.fromkeys(h.account for h in self.holdings))

    def values_by_symbol(self) -> dict[str, float]:
        """Market value per symbol, combined across accounts; all cash is under CASH."""
        values: dict[str, float] = defaultdict(float)
        for h in self.holdings:
            values[CASH if h.is_cash else h.symbol] += h.market_value
        return dict(values)


class Action(BaseModel):
    """A proposed change for the pause check: sell everything, sell a %, or buy $N of a ticker."""

    kind: Literal["sell_all", "sell_pct", "buy"]
    ticker: str | None = None      # None for sells means "the whole portfolio"
    pct: float | None = Field(default=None, gt=0, le=100)
    amount_usd: float | None = Field(default=None, gt=0)

    @field_validator("ticker")
    @classmethod
    def _valid_ticker(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip().upper()
        if not TICKER_RE.fullmatch(value):
            raise ValueError("invalid ticker")
        return value

    @model_validator(mode="after")
    def _required_fields(self) -> "Action":
        if self.kind == "sell_pct" and self.pct is None:
            raise ValueError("sell_pct needs pct")
        if self.kind == "buy" and (self.ticker is None or self.amount_usd is None):
            raise ValueError("buy needs ticker and amount_usd")
        return self

    def describe(self) -> str:
        target = self.ticker or "the whole portfolio"
        if self.kind == "sell_all":
            return f"Sell all of {target} to cash"
        if self.kind == "sell_pct":
            return f"Sell {self.pct:g}% of {target} to cash"
        return f"Buy ${self.amount_usd:,.0f} of {self.ticker} with new money"
