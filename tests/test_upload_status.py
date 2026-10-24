"""The upload status panel: progress counts while market data loads, and the 'report is ready' notes."""

import pandas as pd

from secondlook import market_data as md
from secondlook.quant.facts import load_market_inputs
from secondlook.quant.profile import load_persona
from secondlook.ui_data import unpriced_holdings, upload_ready_notes


def test_progress_counts_every_fetch_and_finishes_at_total():
    _, household, _ = load_persona("beginner")
    seen = []
    load_market_inputs(household.portfolio(), prefer_live=False, progress=lambda d, t, label: seen.append((d, t, label)))
    total = seen[0][1]
    assert [d for d, _, _ in seen] == list(range(total + 1))  # 0, 1, ... total: one step per fetch, then done
    assert all(t == total for _, t, _ in seen) and seen[-1][2] == "done"
    labels = [label for _, _, label in seen]
    assert any(label.startswith("fund and company data for") for label in labels)
    assert any(label.startswith("price history for") for label in labels)


def test_get_prices_reports_each_ticker_but_not_cash():
    seen = []
    md.get_prices(["VOO", "CASH", "SPY"], prefer_live=False, on_ticker=seen.append)
    assert seen == ["VOO", "SPY"]


def test_unpriced_holdings_lists_tickers_without_prices_or_data():
    adj = pd.DataFrame({"VOO": [1.0, 2.0], "ZZZ": [None, None]})
    profiles = {"VOO": {}, "ZZZ": {}, "QQQ": {}}
    values = {"VOO": 100.0, "ZZZ": 50.0, "QQQ": 25.0, "CASH": 10.0, "NOPE": 5.0}
    assert unpriced_holdings(values, profiles, adj) == ["ZZZ", "QQQ", "NOPE"]


def test_ready_notes_say_where_to_look_and_name_unpriced_holdings():
    ready, note = upload_ready_notes(12, 3, [])
    assert ready.startswith("Your report is ready — see the tabs below.") and "12 holding(s) across 3 account(s)" in ready
    assert note is None
    _, note = upload_ready_notes(2, 1, ["BRKX", "ZZZ"])
    assert "BRKX, ZZZ" in note and "still counts in your totals" in note and "ticker spelling" in note
