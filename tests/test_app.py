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
    assert any("educational information only" in m.value for m in at.markdown)


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
