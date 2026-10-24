"""Class-share tickers (Berkshire Hathaway B and similar) are normalised to Yahoo Finance's BASE-CLASS form."""

import pytest

from secondlook.agents.intake import IntakeRow, to_portfolio
from secondlook.quant.models import Action, Holding, normalize_ticker
from secondlook.quant.parse import parse_csv
from secondlook.quant.xray import company_key


@pytest.mark.parametrize("raw, expected", [
    ("BRK.B", "BRK-B"), ("BRK/B", "BRK-B"), ("BRK B", "BRK-B"), ("brk.b", "BRK-B"), ("BRKB", "BRK-B"),
    ("BRK-B", "BRK-B"), ("BRK.A", "BRK-A"), ("BRKA", "BRK-A"), ("BF.B", "BF-B"), ("BFB", "BF-B"),
    ("LEN.B", "LEN-B"), ("HEI/A", "HEI-A"),
    ("AAPL", "AAPL"), (" voo ", "VOO"), ("QQQ", "QQQ"), ("GOOGL", "GOOGL"),
])
def test_normalize_ticker(raw, expected):
    assert normalize_ticker(raw) == expected


def test_csv_upload_maps_every_berkshire_spelling():
    csv = ("Symbol,Description,Quantity,Price,Market Value,Account Number\n"
           "BRK/B,BERKSHIRE HATHAWAY INC CL B,2,$450.00,\"$900.00\",X-1\n"
           "BRK.B,BERKSHIRE HATHAWAY INC CL B,1,$450.00,\"$450.00\",X-1\n"
           "BRKB,BERKSHIRE HATHAWAY INC CL B,1,$450.00,\"$450.00\",X-1\n"
           "VOO,VANGUARD S&P 500 ETF,1,$500.00,\"$500.00\",X-1\n").encode()
    portfolio = parse_csv(csv)
    assert portfolio.rejected == []  # BRK/B used to be rejected as an invalid ticker
    assert portfolio.values_by_symbol() == {"BRK-B": 1800.0, "VOO": 500.0}


def test_pasted_text_keeps_grounding_on_the_written_spelling():
    text = "I own 3 shares of BRK.B worth $1,350 and 1 share of VOO worth $500."
    rows = [IntakeRow(symbol="BRK.B", quantity=3, market_value=1350), IntakeRow(symbol="VOO", quantity=1, market_value=500)]
    portfolio = to_portfolio(rows, source=text)
    assert portfolio.values_by_symbol() == {"BRK-B": 1350.0, "VOO": 500.0}


def test_pause_button_ticker_and_look_through_match():
    assert Action(kind="buy", ticker="brk.b", amount_usd=1000).ticker == "BRK-B"
    # funds list Berkshire as BRK-B or BRK.B; both match the normalised holding in the look-through
    assert company_key("BRK-B") == company_key("BRK.B") == company_key("BRK/B") == company_key("BRK.A")
    assert Holding(symbol="BRK-B", quantity=1, market_value=1, account="A").symbol == "BRK-B"
