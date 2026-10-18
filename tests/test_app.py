"""The Streamlit app driven headlessly with streamlit.testing (demo mode, no key, no network)."""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from secondlook import orchestrator

APP = str(Path(__file__).resolve().parents[1] / "app.py")


def no_ai(*_args, **_kwargs):
    raise AssertionError("the AI was called")


def open_app(monkeypatch, sample="midcareer", tab="Retirement Plan") -> AppTest:
    monkeypatch.setattr(orchestrator, "run_analysis", no_ai)
    monkeypatch.setattr(orchestrator, "run_pause", no_ai)
    at = AppTest.from_file(APP, default_timeout=120)
    at.query_params["sample"] = sample
    at.query_params["tab"] = tab
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def headline(at: AppTest) -> str:
    return next(m.value for m in at.markdown if m.value.startswith("<div class=\"sl-headline\">"))


def test_landing_without_sample(monkeypatch):
    monkeypatch.setattr(orchestrator, "run_analysis", no_ai)
    at = AppTest.from_file(APP, default_timeout=120).run()
    assert not at.exception
    assert any("See what you really own" in m.value for m in at.markdown)
    assert any("processed in memory" in m.value for m in at.markdown)
    assert any("generative AI and is not a professional financial advisor" in m.value for m in at.markdown)


def test_assumption_sliders_recompute_plan_without_ai(monkeypatch):
    at = open_app(monkeypatch)
    before = headline(at)
    assert "Estimated gap: &#36;5,437 to &#36;36,381 of extra saving per year" in before
    next(s for s in at.slider if s.key.startswith("wr-")).set_value(4.5).run()
    assert not at.exception
    assert headline(at) != before  # recomputed in Python; no_ai would have raised if the AI ran
    next(s for s in at.slider if s.key.startswith("tax-")).set_value(30).run()
    assert not at.exception


def test_every_plan_number_is_labeled_as_an_estimate(monkeypatch):
    at = open_app(monkeypatch)
    text = " ".join(m.value for m in at.markdown) + " ".join(c.value for c in at.caption)
    assert "Planning estimate, not a guarantee." in text
    assert "planning estimate, not a guarantee" in text.lower()
    assert all(m.help and "not a guarantee" in m.help for m in at.metric if m.label in ("Target nest egg", "Counted balance today"))


def test_sidebar_keeps_fluency_and_risk_profile_separate(monkeypatch):
    at = open_app(monkeypatch)
    fluency = next(r for r in at.sidebar.radio if r.key == "fluency")
    assert fluency.options == ["New to investing", "Comfortable", "Pro"] and fluency.value == "Comfortable"
    badge = next(m.value for m in at.sidebar.markdown if "sl-risk" in m.value)
    assert "Moderately aggressive" in badge and "Fluency" not in badge


def test_about_you_is_prefilled_and_editable(monkeypatch):
    at = open_app(monkeypatch)
    age = next(n for n in at.number_input if n.label == "Age")
    assert age.value == 42  # midcareer persona
    assert len([r for r in at.radio if r.key.startswith("q-")]) == 5  # the risk quiz (the sidebar page picker is a radio too)
    age.set_value(55)
    at.button[[b.label for b in at.button].index("Update")].click().run()
    assert not at.exception
    years = next(m for m in at.metric if m.label == "Years to retirement")
    assert years.value == "10"


def test_xray_has_concentration_section(monkeypatch):
    at = open_app(monkeypatch, tab="X-Ray")
    labels = {m.label for m in at.metric}
    assert {"Largest single company", "Effective number of holdings", "Effective number of companies"} <= labels
    assert any("Companies above 10% of the portfolio" in m.value for m in at.markdown)


def test_retiree_plan_shows_income_gap(monkeypatch):
    at = open_app(monkeypatch, sample="retiree")
    assert "of income per year" in headline(at)
    assert next(m for m in at.metric if m.label == "Years to retirement").value == "Retired"


def test_employer_stock_field_flows_to_concentration(monkeypatch):
    at = open_app(monkeypatch, tab="X-Ray")
    at.text_input[[t.label for t in at.text_input].index("Your employer's stock ticker, if you hold any (optional)")].set_value("aapl")
    at.button[[b.label for b in at.button].index("Update")].click().run()
    assert not at.exception
    assert any("Your employer&#x27;s stock (AAPL)" in m.value or "Your employer's stock (AAPL)" in m.value for m in at.markdown)


def test_landing_has_hero_steps_and_report_link(monkeypatch):
    monkeypatch.setattr(orchestrator, "run_analysis", no_ai)
    at = AppTest.from_file(APP, default_timeout=120).run()
    assert not at.exception
    text = " ".join(m.value for m in at.markdown)
    assert "sl-hero" in text and "Python does the math" in text and "About this project" in text
    assert not at.sidebar.radio  # no sidebar controls until a portfolio is chosen
    assert (Path(APP).parent / "assets" / "logo.svg").exists()


def test_sidebar_controls_appear_with_a_portfolio(monkeypatch):
    at = open_app(monkeypatch, tab="X-Ray")
    assert [r.key for r in at.sidebar.radio] == ["fluency"]


def test_black_and_gold_theme_with_mascot(monkeypatch):
    monkeypatch.setattr(orchestrator, "run_analysis", no_ai)
    at = AppTest.from_file(APP, default_timeout=120).run()
    assert any('class="sl-mascot"' in m.value for m in at.markdown)  # green Labubu, bottom right
    root = Path(APP).parent
    assert (root / "assets" / "labubu.svg").exists()
    config = (root / ".streamlit" / "config.toml").read_text(encoding="utf-8")
    assert 'base = "dark"' in config and 'primaryColor = "#D4AF37"' in config


def test_risk_ranges_are_labelled_rule_of_thumb(monkeypatch):
    from secondlook.ui_data import RANGE_LABEL, load_risk_ranges

    assert RANGE_LABEL == "Planning rule of thumb, not a recommendation."
    assert load_risk_ranges()["label"] == RANGE_LABEL
    at = open_app(monkeypatch, tab="Retirement Plan")  # the stock-mix comparison lives on the plan tab
    text = " ".join(m.value for m in at.markdown)
    assert RANGE_LABEL in text and "rule-of-thumb range" in text


def test_what_you_own_headline_and_direct_toggle(monkeypatch):
    at = open_app(monkeypatch, tab="X-Ray")
    text = " ".join(m.value for m in at.markdown)
    assert "Top 10 = 57.4% of your money vs. 39.1% of the S&P 500" in text
    assert any("S&P 500 weights via Vanguard S&P 500 ETF (VOO), as of" in c.value for c in at.caption)
    toggle = next(t for t in at.toggle if t.label == "Count shares inside funds")
    assert toggle.value is True
    toggle.set_value(False).run()
    assert not at.exception
    direct = " ".join(m.value for m in at.markdown)
    assert "(direct holdings only)" in direct and "Top 10 = 57.4%" not in direct


def test_possible_shifts_tab_has_ai_disclaimer_and_no_securities(monkeypatch):
    at = open_app(monkeypatch, tab="Possible Shifts")
    text = " ".join(m.value for m in at.markdown)
    assert "Generative AI, not a financial advisor." in text and "never name a specific" in text
    assert "Information Technology" in text  # the mid-career sample's sector shift
    cards = [m.value for m in at.markdown if 'class="sl-card"' in m.value]
    assert 1 <= len(cards) <= 4
    for card in cards:
        assert "AAPL" not in card and "Apple" not in card and "VOO" not in card
