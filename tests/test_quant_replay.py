import math

import pandas as pd
import pytest

from secondlook.quant.replay import Scenario, describe_proxy, portfolio_path, replay, replay_scenario

DATES = pd.to_datetime(["2020-01-01", "2020-02-01", "2020-03-01", "2020-04-01", "2020-05-01", "2020-06-01"])
#                 day0   d1    d2    d3     d4     d5
X = pd.Series([100.0, 90.0, 80.0, 100.0, 110.0, 120.0], index=DATES)
Y = pd.Series([50.0, 50.0, 50.0, 50.0, 50.0, 50.0], index=DATES)
LATE = pd.Series([math.nan, math.nan, 10.0, 11.0, 12.0, 13.0], index=DATES)
ADJ = pd.DataFrame({"X": X, "Y": Y, "LATE": LATE})
WINDOW = Scenario(key="toy", label="Toy crash", start="2020-01-01", end="2020-03-15")


def test_constant_weight_path_by_hand():
    path, proxies, excluded = portfolio_path({"X": 5000, "CASH": 5000}, ADJ, WINDOW)
    # daily rebalanced 50/50: V1 = 1 + .5(-.10) = .95
    # V2 = .95 x (1 + .5 x (80/90 - 1)) = .95 x 0.944444 = 0.897222
    # V3 = 0.897222 x (1 + .5 x .25) = 1.009375
    assert path.iloc[:4].tolist() == pytest.approx([1.0, 0.95, 0.897222, 1.009375], abs=1e-6)
    assert proxies == {} and excluded == []


def test_drawdown_dollars_and_recovery_by_hand():
    r = replay_scenario({"X": 5000, "CASH": 5000}, ADJ, WINDOW)
    # peak 1.0 on day0, trough 0.897222 on 2020-03-01 -> max drawdown 10.2778%
    assert r.max_drawdown == pytest.approx(1 - 0.95 * (1 + 0.5 * (80 / 90 - 1)))
    assert r.max_drawdown_usd == pytest.approx(r.max_drawdown * 10000)
    assert (r.peak_date, r.trough_date, r.recovery_date) == ("2020-01-01", "2020-03-01", "2020-04-01")
    # 31 days / 30.4375 = 1.02 -> 1.0 month
    assert r.months_to_recover == 1.0


def test_all_stock_drawdown_is_the_stock_drawdown():
    r = replay_scenario({"X": 1000}, ADJ, WINDOW)
    assert r.max_drawdown == pytest.approx(0.20)  # 100 -> 80
    assert r.recovery_date == "2020-04-01"       # back to 100


def test_proxy_used_for_holding_without_history():
    proxy_map = {"LATE": {"X": 0.5, "Y": 0.5}}
    r = replay_scenario({"LATE": 1000}, ADJ, WINDOW, proxy_map)
    # proxy return = .5 x X return + .5 x 0 -> the same path as 50% X / 50% cash
    assert r.max_drawdown == pytest.approx(replay_scenario({"X": 1, "CASH": 1}, ADJ, WINDOW).max_drawdown)
    assert r.proxies_used == {"LATE": "50% X + 50% Y"}


def test_holding_with_history_does_not_use_proxy():
    later = Scenario(key="later", label="Later", start="2020-03-01", end="2020-06-01")
    r = replay_scenario({"LATE": 1000}, ADJ, later, {"LATE": {"X": 1.0}})
    assert r.proxies_used == {} and r.max_drawdown == 0 and r.months_to_recover == 0


def test_missing_data_and_proxy_is_excluded_and_reweighted():
    r = replay_scenario({"X": 1000, "GHOST": 1000}, ADJ, WINDOW, proxy_map={})
    # GHOST has no data and the default proxy SPY is not in ADJ: left out, X becomes 100%
    assert r.excluded == ["GHOST"]
    assert r.max_drawdown == pytest.approx(0.20)
    assert r.max_drawdown_usd == pytest.approx(0.20 * 2000)  # dollars still on today's full value


def test_not_recovered_in_data():
    falling = pd.DataFrame({"X": [100.0, 90.0, 80.0, 85.0, 86.0, 87.0]}, index=DATES)
    r = replay_scenario({"X": 1}, falling, WINDOW)
    assert r.recovery_date is None and r.months_to_recover is None


def test_scenarios_without_data_are_skipped():
    old = Scenario(key="old", label="Before data", start="2030-01-01", end="2030-02-01")
    results = replay({"X": 1}, ADJ, [WINDOW, old])
    assert [r.key for r in results] == ["toy"]


def test_describe_proxy():
    assert describe_proxy({"SPY": 1.0}) == "SPY"
    assert describe_proxy({"SPY": 0.55, "EFA": 0.36, "AGG": 0.09}) == "55% SPY + 36% EFA + 9% AGG"


def test_real_snapshot_spy_crash_numbers_are_historically_sensible():
    from secondlook import market_data as md

    adj = md.get_prices(["SPY", "VOO", "AGG"], prefer_live=False).adj_close
    gfc, covid, y2022 = replay({"VOO": 1000}, adj)
    # VOO launched 2010, so the GFC uses SPY; the S&P 500 fell about 55% (total return) to March 2009
    assert gfc.proxies_used == {"VOO": "SPY"}
    assert 0.50 < gfc.max_drawdown < 0.58 and gfc.trough_date == "2009-03-09"
    assert 0.30 < covid.max_drawdown < 0.36 and covid.trough_date == "2020-03-23"
    assert 0.20 < y2022.max_drawdown < 0.28
    # Adding bonds should soften the 2008-09 fall
    mixed = replay({"VOO": 600, "AGG": 400}, adj)[0]
    assert mixed.max_drawdown < gfc.max_drawdown
