"""Read a brokerage CSV export into a Portfolio.

- Columns are matched flexibly ("Qty", "Shares", "Quantity (Shares)" all mean quantity).
- Preamble lines before the header and disclaimer lines after the data are skipped.
- Account numbers are replaced with "Account 1", "Account 2", ... in order of appearance.
- Tickers must match ^[A-Z.\\-]{1,6}$; other rows are rejected with a reason.
- Free-text fields (description, notes, ...) are dropped and never stored.
"""

from __future__ import annotations

import csv
import io
import re
from pathlib import Path
from typing import IO

from secondlook.quant.models import CASH, TICKER_RE, Holding, Portfolio, RejectedRow, normalize_ticker

MAX_BYTES = 1_000_000
MAX_ROWS = 1_000

ALIASES: dict[str, set[str]] = {
    "symbol": {"symbol", "ticker", "tickersymbol", "sym", "securitysymbol", "symbolcusip", "securityid"},
    "quantity": {"quantity", "qty", "shares", "sharesowned", "units", "quantityshares", "shareqty"},
    "price": {"price", "lastprice", "currentprice", "pricepershare", "priceshare", "marketprice", "lastpricepershare", "closeprice", "shareprice"},
    "market_value": {"marketvalue", "currentvalue", "value", "totalvalue", "mktvalue", "endingvalue", "positionvalue"},
    "account": {"accountnumber", "account", "accountno", "acct", "acctnumber", "accountname", "accountid"},
}

# Money-market sweep funds and cash placeholders count as cash.
CASH_SYMBOLS = {CASH, "USD", "CORE", "FCASH", "SPAXX", "FDRXX", "FZFXX", "SWVXX", "VMFXX", "SNVXX"}

_NUMBER = re.compile(r"-?\d+(?:\.\d+)?")


def _norm(header: str) -> str:
    return re.sub(r"[^a-z0-9]", "", header.lower())


def match_columns(header: list[str]) -> dict[str, int]:
    """Map our field names to column indexes; the first matching column wins."""
    found: dict[str, int] = {}
    for index, cell in enumerate(header):
        key = _norm(cell)
        for field, aliases in ALIASES.items():
            if field not in found and key in aliases:
                found[field] = index
    return found


def parse_number(text: str | None) -> float | None:
    """'$1,234.56' -> 1234.56, '(12.00)' -> -12.0, '--' / '' / 'n/a' -> None."""
    if text is None:
        return None
    raw = text.strip()
    negative = raw.startswith("(") and raw.endswith(")")
    match = _NUMBER.search(raw.replace(",", "").replace("$", ""))
    if not match:
        return None
    value = float(match.group())
    return -abs(value) if negative else value


def _read_text(source: str | bytes | Path | IO) -> str:
    if isinstance(source, Path) or (isinstance(source, str) and "\n" not in source and Path(source).exists()):
        data = Path(source).read_bytes()
    elif isinstance(source, bytes):
        data = source
    elif isinstance(source, str):
        data = source.encode("utf-8")
    else:  # file-like, e.g. a Streamlit UploadedFile
        data = source.getvalue() if hasattr(source, "getvalue") else source.read()
        if isinstance(data, str):
            data = data.encode("utf-8")
    if len(data) > MAX_BYTES:
        raise ValueError("File is too large for a portfolio export (limit 1 MB).")
    return data.decode("utf-8-sig", errors="replace")


def parse_csv(source: str | bytes | Path | IO) -> Portfolio:
    rows = list(csv.reader(io.StringIO(_read_text(source))))

    header_index, columns = None, {}
    for i, row in enumerate(rows):
        columns = match_columns(row)
        if "symbol" in columns and ({"quantity", "market_value"} & columns.keys()):
            header_index = i
            break
    if header_index is None:
        raise ValueError("Could not find a header row with a Symbol column and a Quantity or Market Value column.")

    account_labels: dict[str, str] = {}
    holdings: list[Holding] = []
    rejected: list[RejectedRow] = []

    def cell(row: list[str], field: str) -> str:
        index = columns.get(field)
        return row[index].strip() if index is not None and index < len(row) else ""

    for line_number, row in enumerate(rows[header_index + 1 :], start=header_index + 2):
        if len(holdings) + len(rejected) >= MAX_ROWS:
            rejected.append(RejectedRow(row_number=line_number, reason=f"more than {MAX_ROWS} rows; rest ignored"))
            break
        quantity = parse_number(cell(row, "quantity"))
        price = parse_number(cell(row, "price"))
        value = parse_number(cell(row, "market_value"))
        if quantity is None and value is None:
            continue  # blank line, subtotal label or disclaimer text: not a position

        raw_symbol = cell(row, "symbol").upper()
        is_cash = raw_symbol.endswith("**") or raw_symbol.rstrip("*") in CASH_SYMBOLS
        symbol = CASH if is_cash else normalize_ticker(raw_symbol)
        if not TICKER_RE.fullmatch(symbol):
            rejected.append(RejectedRow(row_number=line_number, reason="invalid or missing ticker"))
            continue

        if value is None and quantity is not None and price is not None:
            value = quantity * price
        if quantity is None and value is not None:
            quantity = value / price if price else value if is_cash else None
        if value is None or quantity is None:
            rejected.append(RejectedRow(row_number=line_number, reason="missing quantity or market value"))
            continue
        if value < 0 or quantity < 0:
            rejected.append(RejectedRow(row_number=line_number, reason="negative positions are not supported"))
            continue

        raw_account = cell(row, "account") or "(none)"
        label = account_labels.setdefault(raw_account, f"Account {len(account_labels) + 1}")
        holdings.append(
            Holding(
                symbol=symbol,
                quantity=quantity,
                price=1.0 if is_cash and price is None else price,
                market_value=round(value, 2),
                account=label,
                is_cash=is_cash,
            )
        )

    if not holdings:
        raise ValueError("No valid positions found in the file.")
    return Portfolio(holdings=holdings, rejected=rejected)
