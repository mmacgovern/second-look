from pathlib import Path

import pytest
from pydantic import ValidationError

from secondlook.quant.models import Action, Holding
from secondlook.quant.parse import match_columns, parse_csv, parse_number

SAMPLES = Path(__file__).resolve().parents[1] / "data" / "sample_portfolios"

# A messy export: preamble, different column names, money-market sweep, pending row, disclaimer.
MESSY = """\
Brokerage Positions as of 10/16/2026
,,,,,,
Acct #,Ticker,Security Name,Qty,Last Price,Current Value,Notes
Z12-345678,spaxx**,HELD IN MONEY MARKET,,,"$2,500.00",call me at 555-0100
Z12-345678,aapl,APPLE INC,10,$200.00,"$2,000.00",
Z12-345678,VOO,VANGUARD S&P 500 ETF,4,$500.00,,
Z98-765432,AAPL,APPLE INC,5,$200.00,"$1,000.00",
Z98-765432,BRK.B,BERKSHIRE HATHAWAY CL B,2,$450.00,$900.00,
Z98-765432,Pending Activity,,,,$123.45,
Z98-765432,^GSPC,S&P INDEX,1,$7000,$7000,
Z98-765432,TOOLONGX,BAD,1,$1,$1,
Z98-765432,SHORT,SHORTED STOCK,(3),$10,($30.00),
"The data and information in this spreadsheet is provided for informational purposes only."
"Date downloaded 10/16/2026"
"""


@pytest.fixture
def messy():
    return parse_csv(MESSY)


def test_parse_number_formats():
    assert parse_number("$1,234.56") == 1234.56
    assert parse_number("(12.00)") == -12.0
    assert parse_number("-3") == -3.0
    assert parse_number("12.5%") == 12.5
    for blank in ("", "--", "n/a", None):
        assert parse_number(blank) is None


def test_flexible_column_matching():
    cols = match_columns(["Acct #", "Ticker", "Security Name", "Qty", "Last Price", "Current Value"])
    assert cols == {"account": 0, "symbol": 1, "quantity": 3, "price": 4, "market_value": 5}
    cols = match_columns(["Symbol", "Quantity (Shares)", "Price/Share", "Market Value ($)", "Account Number"])
    assert set(cols) == {"symbol", "quantity", "price", "market_value", "account"}


def test_messy_export_positions(messy):
    by_symbol = messy.values_by_symbol()
    # cash 2,500 + AAPL 2,000 + 1,000 + VOO 4 x 500 + BRK.B 900
    assert by_symbol == {"CASH": 2500.0, "AAPL": 3000.0, "VOO": 2000.0, "BRK.B": 900.0}
    assert messy.total_value == 8400.0


def test_cash_sweep_detected_and_quantity_inferred(messy):
    cash = [h for h in messy.holdings if h.is_cash][0]
    assert (cash.symbol, cash.quantity, cash.price) == ("CASH", 2500.0, 1.0)


def test_market_value_computed_from_quantity_and_price(messy):
    voo = [h for h in messy.holdings if h.symbol == "VOO"][0]
    assert voo.market_value == 2000.0


def test_account_numbers_redacted_in_order(messy):
    assert messy.accounts == ["Account 1", "Account 2"]
    dumped = messy.model_dump_json()
    for real in ("Z12-345678", "Z98-765432", "345678", "765432"):
        assert real not in dumped


def test_free_text_dropped(messy):
    dumped = messy.model_dump_json()
    for text in ("APPLE INC", "MONEY MARKET", "555-0100", "informational", "BERKSHIRE"):
        assert text not in dumped
    assert set(Holding.model_fields) == {"symbol", "quantity", "price", "market_value", "account", "is_cash"}


def test_bad_rows_rejected_with_reasons_not_text(messy):
    reasons = {r.row_number: r.reason for r in messy.rejected}
    # line numbers in MESSY: 9 Pending Activity, 10 ^GSPC, 11 TOOLONGX, 12 SHORT
    assert reasons == {
        9: "invalid or missing ticker",
        10: "invalid or missing ticker",
        11: "invalid or missing ticker",
        12: "negative positions are not supported",
    }
    assert "GSPC" not in messy.model_dump_json()


def test_disclaimer_and_blank_lines_are_not_rejections(messy):
    assert len(messy.rejected) == 4


def test_ticker_validation_rules():
    for good in ("A", "AAPL", "BRK.B", "BF-B", "GFACX", "ABCDEF"):
        Holding(symbol=good, quantity=1, market_value=1, account="Account 1")
    for bad in ("ABCDEFG", "^GSPC", "AAPL1", "aapl", "", "A B"):
        with pytest.raises(ValidationError):
            Holding(symbol=bad, quantity=1, market_value=1, account="Account 1")


def test_no_header_or_no_positions_raise():
    with pytest.raises(ValueError, match="header"):
        parse_csv("Name,Amount\nfoo,1\n")
    with pytest.raises(ValueError, match="No valid positions"):
        parse_csv("Symbol,Quantity\n^BAD,1\n")


def test_accepts_bytes_and_file_like_with_bom():
    import io

    data = "﻿Symbol,Quantity,Price\nMSFT,2,100\n".encode("utf-8")
    for source in (data, io.BytesIO(data)):
        assert parse_csv(source).values_by_symbol() == {"MSFT": 200.0}


def test_too_large_file_rejected():
    with pytest.raises(ValueError, match="too large"):
        parse_csv(b"Symbol,Quantity\n" + b"A,1\n" * 300_000)


@pytest.mark.parametrize("name, total, accounts", [
    ("beginner", 15000.82, 1), ("midcareer", 181152.35, 3), ("retiree", 657234.30, 3),
])
def test_sample_files_parse(name, total, accounts):
    p = parse_csv(SAMPLES / f"{name}.csv")
    assert p.total_value == pytest.approx(total)
    assert len(p.accounts) == accounts and p.rejected == []
    assert "DEMO-0000" not in p.model_dump_json()


def test_action_validation():
    assert Action(kind="buy", ticker=" voo ", amount_usd=500).ticker == "VOO"
    assert Action(kind="sell_pct", pct=25).describe() == "Sell 25% of the whole portfolio to cash"
    for bad in (dict(kind="buy", ticker="VOO"), dict(kind="sell_pct"), dict(kind="sell_pct", pct=150),
                dict(kind="buy", ticker="^GSPC", amount_usd=1)):
        with pytest.raises(ValidationError):
            Action(**bad)
