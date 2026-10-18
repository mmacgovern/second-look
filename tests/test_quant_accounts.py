import pytest

from secondlook.quant.accounts import Household, counted_balance, household_from_csvs, summarize
from secondlook.quant.parse import parse_csv
from secondlook.quant.profile import load_persona

TWO_ACCOUNTS = """Account Number,Symbol,Quantity,Price
X-1,VOO,2,500
X-2,AAPL,10,200
"""
ONE_ACCOUNT = """Account Number,Symbol,Quantity,Price
Y-9,VOO,1,500
Y-9,CASH,300,1
"""


@pytest.fixture
def household():
    return household_from_csvs([
        (TWO_ACCOUNTS, "401k_403b", {"Account 2": "roth_ira"}),
        (ONE_ACCOUNT, "hsa", None),
    ])


def test_each_account_tagged(household):
    tagged = {a.label: (a.account_type, a.value) for a in household.accounts}
    assert tagged == {
        "Upload 1 / Account 1": ("401k_403b", 1000.0),
        "Upload 1 / Account 2": ("roth_ira", 2000.0),
        "Upload 2 / Account 1": ("hsa", 800.0),
    }


def test_merged_household_portfolio(household):
    merged = household.portfolio()
    # VOO: 1,000 (401k) + 500 (HSA) combined across uploads
    assert merged.values_by_symbol() == {"VOO": 1500.0, "AAPL": 2000.0, "CASH": 300.0}
    assert merged.total_value == household.total_value == 3800.0
    assert len(merged.accounts) == 3


def test_balances_by_type(household):
    assert household.balances_by_type() == {"401k_403b": 1000.0, "roth_ira": 2000.0, "hsa": 800.0}


def test_counted_balance_by_hand(household):
    # 401k 1,000 x (1 - 0.15) = 850; Roth 2,000 and HSA 800 in full -> 3,650
    assert counted_balance(household.balances_by_type()) == pytest.approx(3650.0)
    # user-edited rate 25%: 750 + 2,800 = 3,550
    assert counted_balance(household.balances_by_type(), 0.25) == pytest.approx(3550.0)


def test_traditional_ira_reduced_and_taxable_counted_in_full():
    balances = {"traditional_ira": 10000.0, "taxable": 5000.0, "401k_403b": 2000.0}
    # 10,000 x .85 + 5,000 + 2,000 x .85 = 8,500 + 5,000 + 1,700
    assert counted_balance(balances) == pytest.approx(15200.0)
    assert counted_balance(balances, 0.0) == pytest.approx(17000.0)


def test_invalid_tax_rate():
    for bad in (-0.1, 1.0, 1.5):
        with pytest.raises(ValueError):
            counted_balance({"taxable": 1.0}, bad)


def test_summary(household):
    s = summarize(household, 0.20)
    assert (s.total_usd, s.counted_usd, s.tax_rate) == (3800.0, 1000 * 0.8 + 2800, 0.20)


def test_add_upload_chains_and_keeps_real_numbers_out():
    h = Household().add_upload(parse_csv(TWO_ACCOUNTS), "taxable")
    assert {a.account_type for a in h.accounts} == {"taxable"}
    assert "X-1" not in h.model_dump_json() and "X-2" not in h.model_dump_json()


def test_midcareer_persona_counts_401k_after_tax():
    _, household, _ = load_persona("midcareer")
    balances = household.balances_by_type()
    assert set(balances) == {"401k_403b", "roth_ira", "taxable"}
    # 401(k) 108,323.80 x 0.85 + Roth 44,837.00 + taxable 27,991.55 = 164,903.78
    assert counted_balance(balances) == pytest.approx(164903.78, abs=0.01)


@pytest.mark.parametrize("name, types", [
    ("midcareer", {"401k_403b", "roth_ira", "taxable"}),
    ("retiree", {"traditional_ira", "taxable", "roth_ira"}),
])
def test_split_personas_have_three_accounts(name, types):
    _, household, _ = load_persona(name)
    assert len(household.accounts) == 3
    assert {a.account_type for a in household.accounts} == types


def test_same_holding_across_accounts_adds_up_in_household_view():
    from secondlook.quant.xray import xray

    _, household, _ = load_persona("midcareer")
    in_accounts = [h.market_value for a in household.accounts for h in a.holdings if h.symbol == "VTI"]
    assert in_accounts == [11222.10, 22444.20]  # VTI sits in the 401(k) and the Roth IRA
    values = household.portfolio().values_by_symbol()
    assert values["VTI"] == pytest.approx(33666.30)
    assert xray(values, {"VTI": {"quote_type": "ETF", "top_holdings": [{"symbol": "AAPL", "name": "Apple", "weight": 0.1}]}}
                ).top_companies[0].value_usd == pytest.approx(3366.63, abs=0.01)
