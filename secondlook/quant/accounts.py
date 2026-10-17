"""Account types and the household view.

Each uploaded CSV is tagged with one account type (or, for a file that holds
several accounts, one type per account label). All accounts merge into one
household Portfolio for look-through and concentration.

The only tax handling anywhere: for the retirement plan, pre-tax balances
(401k/403b, traditional IRA) are reduced by a flat RETIREMENT_TAX_RATE; Roth,
HSA and taxable balances count in full.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Literal

from pydantic import BaseModel, Field

from secondlook.quant.models import Holding, Portfolio

AccountType = Literal["taxable", "401k_403b", "traditional_ira", "roth_ira", "hsa"]
ACCOUNT_TYPE_LABELS: dict[str, str] = {
    "taxable": "Taxable brokerage",
    "401k_403b": "401(k)/403(b)",
    "traditional_ira": "Traditional IRA",
    "roth_ira": "Roth IRA",
    "hsa": "HSA",
}
PRE_TAX: set[str] = {"401k_403b", "traditional_ira"}
RETIREMENT_TAX_RATE = 0.15


class TaggedAccount(BaseModel):
    label: str               # e.g. "Upload 1 / Account 2" (never a real account number)
    account_type: AccountType
    holdings: list[Holding]

    @property
    def value(self) -> float:
        return sum(h.market_value for h in self.holdings)


class Household(BaseModel):
    accounts: list[TaggedAccount] = []

    def add_upload(
        self,
        portfolio: Portfolio,
        account_type: AccountType,
        per_account: dict[str, AccountType] | None = None,
    ) -> "Household":
        """Tag one parsed CSV. `per_account` maps its labels ("Account 2") to a different type."""
        upload = f"Upload {len({a.label.split(' / ')[0] for a in self.accounts}) + 1}"
        for account in portfolio.accounts:
            self.accounts.append(
                TaggedAccount(
                    label=f"{upload} / {account}",
                    account_type=(per_account or {}).get(account, account_type),
                    holdings=[h for h in portfolio.holdings if h.account == account],
                )
            )
        return self

    def portfolio(self) -> Portfolio:
        """All accounts merged into one household Portfolio."""
        holdings = [h.model_copy(update={"account": a.label}) for a in self.accounts for h in a.holdings]
        return Portfolio(holdings=holdings)

    def balances_by_type(self) -> dict[str, float]:
        out: dict[str, float] = defaultdict(float)
        for account in self.accounts:
            out[account.account_type] += account.value
        return dict(out)

    @property
    def total_value(self) -> float:
        return sum(a.value for a in self.accounts)


def counted_balance(balances_by_type: dict[str, float], tax_rate: float = RETIREMENT_TAX_RATE) -> float:
    """Balance counted toward retirement: pre-tax accounts reduced by the flat tax rate."""
    if not 0 <= tax_rate < 1:
        raise ValueError("tax_rate must be between 0 and 1")
    return sum(v * (1 - tax_rate) if t in PRE_TAX else v for t, v in balances_by_type.items())


class AccountSummary(BaseModel):
    balances_by_type: dict[str, float]
    total_usd: float
    counted_usd: float
    tax_rate: float = Field(ge=0, lt=1)


def household_from_csvs(uploads: list[tuple[object, AccountType, dict[str, AccountType] | None]]) -> Household:
    """Build a household from (csv source, account type, per-account overrides) uploads."""
    from secondlook.quant.parse import parse_csv

    household = Household()
    for source, account_type, per_account in uploads:
        household.add_upload(parse_csv(source), account_type, per_account)
    return household


def summarize(household: Household, tax_rate: float = RETIREMENT_TAX_RATE) -> AccountSummary:
    balances = household.balances_by_type()
    return AccountSummary(
        balances_by_type=balances,
        total_usd=household.total_value,
        counted_usd=counted_balance(balances, tax_rate),
        tax_rate=tax_rate,
    )
