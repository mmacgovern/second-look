import pandas as pd
import pytest

from quant_toys import TOY_PROFILES, TOY_VALUES
from secondlook.quant.models import Action
from secondlook.quant.pause import apply_action, market_events, pause_check
from secondlook.quant.replay import Scenario

GFC = Scenario(key="gfc", label="2008-09 financial crisis", start="2007-10-01", end="2009-03-31")
SPY = pd.Series(
    [100.0, 80.0, 50.0, 75.0, 100.0, 101.0],
    index=pd.to_datetime(["2007-10-01", "2008-06-02", "2009-03-09", "2010-03-09", "2012-03-09", "2012-06-01"]),
)


def test_sell_all_moves_everything_to_cash():
    assert apply_action(TOY_VALUES, Action(kind="sell_all")) == {"CASH": 10000.0}


def test_sell_all_of_one_ticker():
    after = apply_action(TOY_VALUES, Action(kind="sell_all", ticker="FUNDB"))
    assert after == {"FUNDA": 4000.0, "AAA": 3000.0, "CASH": 3000.0}


def test_sell_pct_of_portfolio_and_of_ticker():
    after = apply_action(TOY_VALUES, Action(kind="sell_pct", pct=25))
    # 25% of 9,000 invested = 2,250 to cash
    assert after == pytest.approx({"FUNDA": 3000.0, "FUNDB": 1500.0, "AAA": 2250.0, "CASH": 3250.0})
    after = apply_action(TOY_VALUES, Action(kind="sell_pct", ticker="AAA", pct=50))
    assert after["AAA"] == 1500.0 and after["CASH"] == 2500.0


def test_buy_adds_new_money():
    after = apply_action(TOY_VALUES, Action(kind="buy", ticker="BBB", amount_usd=1000))
    assert after["BBB"] == 1000.0 and sum(after.values()) == 11000.0
    after = apply_action(TOY_VALUES, Action(kind="buy", ticker="AAA", amount_usd=500))
    assert after["AAA"] == 3500.0


def test_selling_something_not_held_raises():
    with pytest.raises(ValueError):
        apply_action(TOY_VALUES, Action(kind="sell_all", ticker="ZZZ"))
    with pytest.raises(ValueError):
        apply_action(TOY_VALUES, Action(kind="sell_pct", ticker="CASH", pct=10))


def test_market_events_by_hand():
    (event,) = market_events(pd.DataFrame({"SPY": SPY}), [GFC])
    # peak 100 on 2007-10-01, bottom 50 on 2009-03-09 -> drop 50%
    assert (event.peak_date, event.bottom_date, event.drop) == ("2007-10-01", "2009-03-09", 0.5)
    # 1 year later 75 -> +50%; 3 years later 100 -> +100%
    assert event.gain_1y_after_bottom == pytest.approx(0.5)
    assert event.gain_3y_after_bottom == pytest.approx(1.0)
    # back to 100 on 2012-03-09: 1,096 days / 30.4375 = 36.0 months
    assert event.months_bottom_to_recovery == 36.0


def test_market_events_need_market_series():
    assert market_events(pd.DataFrame({"X": SPY}), [GFC]) == []


def test_pause_check_before_after_by_hand():
    dates = pd.date_range("2007-10-01", periods=4, freq="MS")
    adj = pd.DataFrame({t: [100.0, 50.0, 100.0, 100.0] for t in ("FUNDA", "FUNDB", "AAA", "SPY")}, index=dates)
    scenario = Scenario(key="gfc", label="Toy", start="2007-10-01", end="2007-12-31")
    result = pause_check(TOY_VALUES, Action(kind="sell_all"), TOY_PROFILES, adj, [scenario])
    assert result.action == "Sell all of the whole portfolio to cash"
    # before: 90% invested falls 50% -> 45% drawdown = $4,500; after: all cash -> 0
    assert result.before.replay[0].max_drawdown == pytest.approx(0.45)
    assert result.before.replay[0].max_drawdown_usd == pytest.approx(4500)
    assert result.after.replay[0].max_drawdown == 0
    assert result.before.top_companies[0].symbol == "AAA" and result.after.top_companies == []
    assert result.after.cash_weight == 1.0 and result.after.sectors == {"cash": 1.0}
    assert result.history[0].drop == pytest.approx(0.5)


def test_pause_check_buy_shifts_exposure():
    dates = pd.date_range("2020-02-01", periods=3, freq="MS")
    adj = pd.DataFrame({t: [1.0, 1.0, 1.0] for t in ("FUNDA", "FUNDB", "AAA", "BBB")}, index=dates)
    scenario = Scenario(key="covid", label="Toy", start="2020-02-01", end="2020-03-31")
    result = pause_check(TOY_VALUES, Action(kind="buy", ticker="BBB", amount_usd=10000), TOY_PROFILES, adj, [scenario])
    # after: BBB = (10,000 direct + 4,000 x 0.30) / 20,000 = 0.56
    bbb = next(c for c in result.after.top_companies if c.symbol == "BBB")
    assert bbb.weight == pytest.approx(0.56)
    assert result.after.total_value_usd == 20000
    assert result.history == []  # no SPY in toy data
