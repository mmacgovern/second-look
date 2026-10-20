"""Second Look: an AI second opinion on an investment portfolio.

Run:  python -m streamlit run app.py

All numbers come from secondlook/quant (Python). The AI only explains them,
through secondlook/orchestrator.py, and every AI answer is checked before it
is shown. Uploaded files are processed in memory and never written to disk.
"""

from __future__ import annotations

import base64
import hashlib
import html
import json
import math
import uuid
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from secondlook import budget, demo_runs, orchestrator
from secondlook.agents.base import AgentContext
from secondlook.config import get_settings
from secondlook.quant import sandbox as sandbox_engine
from secondlook.quant import shifts as shift_rules
from secondlook.quant.accounts import ACCOUNT_TYPE_LABELS, RETIREMENT_TAX_RATE, counted_balance, household_from_csvs
from secondlook.quant.benchmark import (
    BENCHMARK_FILE, comparison_sentence, donut_slices, load_benchmark, sector_rows, stock_sector_weights, top_ten,
)
from secondlook.quant.concentration import CONCENTRATION_THRESHOLD, concentration
from secondlook.quant.facts import build_facts, load_market_inputs
from secondlook.quant.fees import fee_report
from secondlook.quant.models import CASH, Action
from secondlook.quant.plan import ASSUMPTIONS, make_plan
from secondlook.quant.profile import QUIZ, UserProfile, load_persona, risk_profile
from secondlook.quant.replay import SCENARIOS, replay
from secondlook.quant.xray import SECTOR_LABELS, xray
from secondlook.ui_data import (
    FLUENCY, PLAN_LABEL, RANGE_LABEL, SAMPLES, Inputs, asset_mix, balances_frame, company_table, crash_frame,
    fee_curves, friendly_note, gap_headline, holdings_for_buy, load_risk_ranges, mix_vs_range, overlap_table,
    params_to_reset, parse_action, plan_band_frame, session_cost, treemap_frame,
)

ROOT = Path(__file__).resolve().parent
ASSETS, DOCS = ROOT / "assets", ROOT / "docs"
# Black and gold theme: INK is the main text colour, ACCENT the gold, SURFACE the card background.
BG, SURFACE, LINE = "#0A0A0A", "#161616", "#2E2A1E"
INK, ACCENT, GOLD_LIGHT, SLATE, MIST = "#F5F1E6", "#D4AF37", "#F1D67A", "#B8B0A0", "#5A5548"
TABS = ["X-Ray", "Fee Clock", "Crash Replay", "Retirement Plan", "Top 3 Things Worth Knowing", "Possible Shifts",
        "Try a Shift"]
AI_DISCLAIMER = ("Suggestions are general shifts between "
                 "industries, asset types and account types computed from your numbers; they never name a specific "
                 "stock or security. Check any change with a licensed, fiduciary financial advisor.")
DISCLAIMER = ("Second Look uses generative AI and is not a professional financial advisor. It provides educational "
              "information and general suggestions only, never recommendations of specific stocks or securities, and "
              "it is not investment, tax or legal advice. Past results do not predict future results.")

st.set_page_config(page_title="Second Look", page_icon=":material/travel_explore:", layout="wide",
                   initial_sidebar_state="auto")

st.markdown(f"""
<style>
  #MainMenu, footer, [data-testid="stDecoration"] {{ display: none; }}
  .stApp {{ background: radial-gradient(1200px 500px at 85% -10%, rgba(212,175,55,0.10), transparent 60%), {BG}; }}
  header[data-testid="stHeader"] {{ background: #000000; border-bottom: 1px solid {ACCENT}55; }}
  .block-container {{ max-width: 1120px; padding-top: 4.6rem; padding-bottom: 2rem; }}
  h1, h2, h3, h4, h5 {{ color: {INK}; letter-spacing: -0.01em; }}
  h1 {{ font-weight: 700; }}
  .stButton button[kind="primary"], .stDownloadButton button[kind="primary"] {{ color: #0A0A0A; font-weight: 600; }}
  .sl-hero {{ background: linear-gradient(135deg, #000000 0%, #141109 65%, #2A2210 100%); color: {INK};
              border: 1px solid {ACCENT}66; border-radius: 18px; padding: 2.6rem 2.8rem 2.3rem; margin: 0.4rem 0 1.8rem;
              box-shadow: 0 10px 40px rgba(0,0,0,0.5); }}
  .sl-hero h1 {{ color: {INK}; font-size: 2.7rem; margin: 0.2rem 0 0.6rem; line-height: 1.15; padding: 0; }}
  .sl-hero h1 em {{ color: {ACCENT}; font-style: normal; }}
  .sl-kicker {{ color: {ACCENT}; font-size: 0.82rem; font-weight: 600; letter-spacing: 0.09em; text-transform: uppercase; margin: 0; }}
  .sl-promise {{ font-size: 1.2rem; color: #D9D2C3; margin: 0 0 1.4rem 0; line-height: 1.55; max-width: 720px; }}
  .sl-trust {{ display: flex; flex-wrap: wrap; gap: 0.6rem; }}
  .sl-trust span {{ background: rgba(212,175,55,0.08); border: 1px solid {ACCENT}66; border-radius: 999px;
                    padding: 0.35rem 0.9rem; font-size: 0.86rem; color: {GOLD_LIGHT}; }}
  .sl-section {{ font-size: 1.25rem; font-weight: 700; color: {INK}; margin: 1.6rem 0 0.2rem; }}
  .sl-section-sub {{ color: {SLATE}; font-size: 0.95rem; margin: 0 0 0.8rem; }}
  .sl-step {{ display: flex; gap: 0.8rem; align-items: flex-start; }}
  .sl-step .n {{ flex: 0 0 2rem; height: 2rem; border-radius: 50%; background: {ACCENT}; color: #0A0A0A; font-weight: 700;
                 display: flex; align-items: center; justify-content: center; }}
  .sl-step b {{ color: {INK}; display: block; margin-bottom: 0.15rem; }}
  .sl-step span {{ color: {SLATE}; font-size: 0.92rem; line-height: 1.5; }}
  .sl-sample-title {{ font-size: 1.1rem; font-weight: 700; color: {GOLD_LIGHT}; margin: 0; }}
  .sl-sample-blurb {{ color: {SLATE}; font-size: 0.92rem; min-height: 2.8rem; margin: 0.3rem 0 0.4rem; }}
  .sl-muted {{ color: {SLATE}; font-size: 0.92rem; }}
  .sl-privacy {{ color: {SLATE}; font-size: 0.9rem; border-left: 3px solid {ACCENT}; padding: 0.3rem 0 0.3rem 0.8rem; margin-top: 0.6rem; }}
  .sl-badge {{ display: inline-block; border: 1px solid {ACCENT}66; border-radius: 999px; padding: 0.3rem 0.9rem;
               font-size: 0.85rem; color: {GOLD_LIGHT}; background: {SURFACE}; }}
  .sl-card {{ border: 1px solid {LINE}; border-left: 3px solid {ACCENT}; border-radius: 12px; padding: 1.1rem 1.3rem;
              margin-bottom: 0.9rem; background: {SURFACE}; }}
  .sl-card h4 {{ margin: 0 0 0.4rem 0; font-size: 1.08rem; color: {GOLD_LIGHT}; }}
  .sl-card p {{ margin: 0; color: {INK}; line-height: 1.6; }}
  .sl-callout {{ background: {SURFACE}; border: 1px solid {LINE}; border-radius: 12px; padding: 1.1rem 1.3rem; }}
  .sl-callout .big {{ font-size: 1.9rem; font-weight: 700; color: {ACCENT}; }}
  .sl-source {{ font-size: 0.8rem; color: {SLATE}; margin-bottom: 0.8rem; }}
  .sl-footer {{ margin-top: 3rem; border-top: 1px solid {ACCENT}44; padding: 1.2rem 9rem 0.4rem; color: {SLATE};
                font-size: 0.8rem; line-height: 1.6; text-align: center; }}
  .sl-footer b {{ color: {ACCENT}; }}
  div[data-testid="stMetricValue"] {{ color: {ACCENT}; }}
  .sl-risk {{ border: 1px solid {ACCENT}55; border-radius: 10px; padding: 0.6rem 0.8rem; margin-top: 0.3rem; }}
  .sl-risk .label {{ font-size: 1.05rem; font-weight: 700; color: {GOLD_LIGHT}; }}
  .sl-risk .sub {{ font-size: 0.8rem; opacity: 0.8; }}
  .sl-estimate {{ font-size: 0.8rem; color: {SLATE}; font-style: italic; }}
  .sl-headline {{ font-size: 1.6rem; font-weight: 700; color: {ACCENT}; margin: 0.2rem 0 0.1rem 0; }}
  .sl-portfolio {{ background: {SURFACE}; border: 1px solid {LINE}; border-left: 3px solid {ACCENT}; border-radius: 14px;
                   padding: 1rem 1.3rem; margin: 0.4rem 0 1rem; }}
  .sl-portfolio h3 {{ margin: 0; padding: 0; font-size: 1.3rem; }}
  .sl-portfolio p {{ margin: 0.2rem 0 0; }}
  .sl-ai-warning {{ background: rgba(212,175,55,0.08); border: 1px solid {ACCENT}88; border-radius: 12px;
                    padding: 0.8rem 1.1rem; margin: 0.2rem 0 1rem; color: {INK}; font-size: 0.92rem; line-height: 1.55; }}
  .sl-ai-warning b {{ color: {GOLD_LIGHT}; }}
  .sl-mascot {{ position: fixed; right: 18px; bottom: 12px; width: 104px; z-index: 999990; pointer-events: none;
                filter: drop-shadow(0 6px 14px rgba(0,0,0,0.6)); }}
  @media (max-width: 640px) {{
    .sl-hero {{ padding: 1.6rem 1.3rem; border-radius: 14px; }}
    .sl-hero h1 {{ font-size: 1.85rem; }}
    .sl-promise {{ font-size: 1.02rem; }}
    .sl-mascot {{ width: 64px; right: 8px; bottom: 8px; }}
    .sl-footer {{ padding: 1.2rem 4.5rem 0.4rem 0; text-align: left; }}
  }}
</style>
""", unsafe_allow_html=True)
st.logo(str(ASSETS / "logo.svg"), size="large")
MASCOT = base64.b64encode((ASSETS / "labubu.svg").read_bytes()).decode()
st.markdown(f'<img class="sl-mascot" src="data:image/svg+xml;base64,{MASCOT}" alt="Green Labubu mascot">',
            unsafe_allow_html=True)


def footer() -> None:
    st.markdown(f'<div class="sl-footer"><b>Second Look</b> &middot; AI Foundations final project<br>'
                f'{html.escape(DISCLAIMER)}</div>', unsafe_allow_html=True)


def safe(text: str) -> str:
    """Escape AI text for st.markdown: HTML-escape, and keep "$" from being read as LaTeX math."""
    return html.escape(text).replace("$", "&#36;")


def money(x: float) -> str:
    return f"${x:,.0f}"


settings = get_settings()
state = st.session_state
state.setdefault("sid", f"web-{uuid.uuid4().hex[:12]}")
state.setdefault("analyses", {})
state.setdefault("pauses", {})
state.setdefault("uploads", {})
state.setdefault("inputs", {})
state.setdefault("about", {})
params = st.query_params


# --- data loading (cached) ------------------------------------------------------

@st.cache_resource(show_spinner=False)
def sample_base(name: str):
    """A sample persona's household, market data, and pre-filled About-you answers."""
    user, household, answers = load_persona(name)
    profiles, adj = load_market_inputs(household.portfolio(), prefer_live=False, extra=["VOO", "VTI", "QQQ"])
    return household, profiles, adj, user, list(answers)


@st.cache_resource(show_spinner=False)
def default_persona_facts(name: str) -> dict:
    """Facts for a sample with its pre-filled answers; shared by all visitors (matches the demo recordings)."""
    return demo_runs.persona_facts(name)


def save_upload(files: list[tuple[bytes, str]]) -> str:
    """Parse uploaded CSVs (in memory) into a household; returns its key."""
    digest = hashlib.sha256(b"".join(data + kind.encode() for data, kind in files)).hexdigest()[:16]
    key = f"upload:{digest}"
    if key not in state.uploads:
        household = household_from_csvs([(data, kind, None) for data, kind in files])
        profiles, adj = load_market_inputs(household.portfolio(), prefer_live=not settings.demo_mode,
                                           extra=["VOO", "VTI", "QQQ"])
        state.uploads[key] = {"household": household, "profiles": profiles, "adj": adj, "files": len(files)}
    return key


def make_inputs(base_key: str, label: str, household, profiles, adj, sample: str | None,
                default: tuple[UserProfile | None, list[int] | None]) -> Inputs:
    user, answers = state.about.get(base_key, default)
    about_key = hashlib.sha256(f"{user!r}{answers!r}".encode()).hexdigest()[:10]
    key = f"{base_key}:{about_key}"
    if key not in state.inputs:
        is_default = sample is not None and (user, answers) == default
        if is_default:  # identical to the recorded demo facts
            facts = default_persona_facts(sample)
        else:
            facts = build_facts(None, profiles, adj, household=household, user=user, risk_answers=answers)
        state.inputs[key] = Inputs(key=key, label=label, values=household.portfolio().values_by_symbol(),
                                   profiles=profiles, adj=adj, facts=facts, sample=sample,
                                   extra={"household": household, "user": user, "answers": answers,
                                          "base_key": base_key, "is_default": is_default, "default": default})
    return state.inputs[key]


def context(demo: bool = False) -> AgentContext:
    s = settings.model_copy(update={"demo_mode": True}) if demo else settings
    return AgentContext(session_id=state.sid, settings=s)


def explain(inputs: Inputs, fluency: str):
    cache_key = (inputs.key, fluency)
    if cache_key not in state.analyses:
        result = orchestrator.run_analysis(inputs.facts, fluency, context())
        if result.source == "template" and not settings.demo_mode and inputs.sample:
            saved = orchestrator.run_analysis(inputs.facts, fluency, context(demo=True))
            if saved.source != "template":
                result = saved.model_copy(update={"note": "The AI spending limit was reached, so this is a saved explanation."})
        state.analyses[cache_key] = result
    return state.analyses[cache_key]


def pause_facts(inputs: Inputs, action: Action) -> dict:
    if inputs.extra["is_default"]:
        return demo_runs.persona_facts(inputs.sample, action)
    household = inputs.extra["household"]
    profiles, adj = load_market_inputs(household.portfolio(), prefer_live=not settings.demo_mode and not inputs.sample,
                                       extra=[action.ticker] if action.ticker else [])
    return build_facts(None, profiles, adj, action, household=household, user=inputs.extra["user"],
                       risk_answers=inputs.extra["answers"])


def pause_check(inputs: Inputs, action: Action):
    cache_key = (inputs.key, action.model_dump_json())
    if cache_key not in state.pauses:
        facts = pause_facts(inputs, action)
        result = orchestrator.run_pause(facts, context())
        if result.source == "template" and not settings.demo_mode and inputs.sample:
            saved = orchestrator.run_pause(facts, context(demo=True))
            if saved.source != "template":
                result = saved.model_copy(update={"note": "The AI spending limit was reached, so this is a saved explanation."})
        state.pauses[cache_key] = (facts["pause_check"], result)
    return state.pauses[cache_key]


# --- pages -------------------------------------------------------------------------

def admin_page() -> None:
    st.title("Admin")
    st.markdown('<p class="sl-muted">Spending recorded in the local ledger (.runtime/ledger.db). No keys or personal data are stored there.</p>',
                unsafe_allow_html=True)
    s = budget.summary()
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Spent today", f"${s['today_usd']:.4f}", help=f"Daily limit ${settings.daily_budget_usd:.2f}")
    c2.metric("Spent all time", f"${s['total_usd']:.4f}")
    c3.metric("Per-session limit", f"${settings.session_budget_usd:.2f}")
    c4.metric("AI mode", "Demo (no charges)" if settings.demo_mode else "Live")
    st.caption(f"Per-call limits: {settings.max_tokens_per_call:,} tokens and ${settings.max_cost_per_call_usd:.2f}. "
               f"Models: fast = {settings.model_fast or 'not set'}, smart = {settings.model_smart or 'not set'}.")
    st.subheader("Cost by agent")
    st.caption("cached_input_tokens: prompt tokens the provider served from its prompt cache, billed at the "
               "lower cached-input rate.")
    st.dataframe(pd.DataFrame(s["by_agent"]), hide_index=True)
    st.subheader("Cost by session")
    st.dataframe(pd.DataFrame(s["by_session"]).head(50), hide_index=True)
    st.subheader("Verifier rewrites")
    st.caption("Every time the checker sent an AI draft back. 'rewrite' means a second draft was requested; "
               "'template' means the second draft failed too, so the standard summary was shown.")
    rows = budget.rewrites(limit=100)
    if rows:
        st.dataframe(pd.DataFrame([{"Time (UTC)": r["timestamp"][:19].replace("T", " "), "Session": r["session_id"],
                                    "Stage": r["stage"], "Fluency": r["fluency"], "Reasons": " | ".join(r["reasons"])}
                                   for r in rows]), hide_index=True)
    else:
        st.info("No drafts have been sent back yet.")


def hero() -> None:
    st.markdown("""
<div class="sl-hero">
  <p class="sl-kicker">An AI second opinion on your portfolio</p>
  <h1>Take a <em>second look</em> at what you own.</h1>
  <p class="sl-promise">See what you really own, what it costs you, and how it would have held up in past crashes,
  explained in plain English.</p>
  <div class="sl-trust"><span>&#10003; Every number computed in Python</span>
  <span>&#10003; AI explanations checked before you see them</span>
  <span>&#10003; Never tells you what to buy or sell</span></div>
</div>""", unsafe_allow_html=True)
    steps = [("Choose a portfolio", "Try one of three samples or upload your brokerage CSV."),
             ("Python does the math", "Fund look-through, fees, crash replays and a retirement plan."),
             ("AI explains it", "Plain English at your level, checked number by number.")]
    for col, (n, (title, text)) in zip(st.columns(3), enumerate(steps, 1)):
        col.markdown(f'<div class="sl-step"><div class="n">{n}</div><div><b>{title}</b><span>{text}</span></div></div>',
                     unsafe_allow_html=True)


def landing(compact: bool) -> None:
    if not compact:
        hero()
        st.markdown('<p class="sl-section">Try a sample portfolio</p>'
                    '<p class="sl-section-sub">Made-up people with real funds and real market history. Free to explore.</p>',
                    unsafe_allow_html=True)
    else:
        st.markdown('<p class="sl-section" style="margin-top:0.4rem">Portfolio</p>', unsafe_allow_html=True)
    cols = st.columns(3)
    for col, (name, (title, blurb)) in zip(cols, SAMPLES.items()):
        with col, st.container(border=True):
            chosen = params.get("sample") == name
            if not compact:
                st.markdown(f'<p class="sl-sample-title">{safe(title)}</p><p class="sl-sample-blurb">{safe(blurb)}</p>',
                            unsafe_allow_html=True)
            label = (f"{title} (showing)" if chosen else title) if compact else ("Showing" if chosen else "Open this sample")
            if st.button(label, key=f"sample-{name}", width="stretch", type="primary" if chosen else "secondary"):
                params["sample"] = name
                params.pop("pause", None)
                st.rerun()

    if not compact:
        st.markdown('<p class="sl-section">Or analyze your own portfolio</p>'
                    '<p class="sl-section-sub">Export your positions from your brokerage as a CSV file, one file per account.</p>',
                    unsafe_allow_html=True)
    box = st.expander("Upload your own brokerage CSV") if compact else st.container(border=True)
    with box:
        files = st.file_uploader("Brokerage export (.csv)", type=["csv"], accept_multiple_files=True)
        tagged = []
        for i, f in enumerate(files or []):
            account_type = st.selectbox(f"Account type for {f.name}", list(ACCOUNT_TYPE_LABELS),
                                        format_func=ACCOUNT_TYPE_LABELS.get, key=f"type-{i}")
            tagged.append((f.getvalue(), account_type))
        if files:
            st.caption("Add one file per account (for example your 401(k) and your IRA) and pick each one's type.")
        if tagged and st.button("Analyze my files", type="primary"):
            try:
                state["upload_key"] = save_upload(tagged)
                for k in params_to_reset(list(params)):
                    del params[k]
                params["sample"] = "upload"
                st.rerun()
            except ValueError as exc:
                st.error(f"That file could not be read: {exc}")
        st.markdown('<div class="sl-privacy">Your file is processed in memory and never saved. Account numbers and '
                    'descriptions are removed before anything reaches the AI, and the AI only sees computed numbers.</div>',
                    unsafe_allow_html=True)


def project_card() -> None:
    st.markdown('<p class="sl-section">About this project</p>', unsafe_allow_html=True)
    with st.container(border=True):
        text, link = st.columns([3, 1], vertical_alignment="center")
        text.markdown("**Second Look** is an AI Foundations final project. The report covers the motivation, the agents, "
                      "measured costs and the safety tests; the slide deck is the 9-slide presentation.")
        link.page_link(PAGES[1], label="Report & slide deck", icon=":material/arrow_forward:", width="stretch")


def current_inputs() -> Inputs | None:
    choice = params.get("sample")
    if choice in SAMPLES:
        with st.spinner("Reading the portfolio..."):
            household, profiles, adj, user, answers = sample_base(choice)
            title, blurb = SAMPLES[choice]
            return make_inputs(f"sample:{choice}", f"{title}: {blurb}", household, profiles, adj, choice, (user, answers))
    key = state.get("upload_key")
    if choice == "upload" and key in state.uploads:
        u = state.uploads[key]
        label = f"Your upload: {u['files']} file(s), {len(u['household'].accounts)} account(s)"
        return make_inputs(key, label, u["household"], u["profiles"], u["adj"], None, (None, None))
    return None


def about_you(inputs: Inputs) -> None:
    """The About-you step: situation and the 5-question risk quiz. Samples come pre-filled."""
    user, answers = inputs.extra["user"], inputs.extra["answers"]
    base_key, default_user, default_answers = inputs.extra["base_key"], *inputs.extra["default"]
    filled = user is not None
    with st.expander("About you" + ("" if filled else ": add your situation to see your plan and risk profile"),
                     expanded=not filled or params.get("about") == "open"):
        if inputs.sample:
            st.markdown('<p class="sl-muted">Pre-filled for this sample persona. Change anything and press Update.</p>',
                        unsafe_allow_html=True)
        base = user or UserProfile(age=40, retirement_age=65, desired_income_today_usd=60_000,
                                   social_security_or_pension_usd=None, annual_contribution_usd=10_000)
        with st.form(f"about-{base_key}"):
            a, b, c = st.columns(3)
            age = a.number_input("Age", 18, 100, base.age)
            retire = b.number_input("Target retirement age", 40, 100, base.retirement_age)
            contribution = c.number_input("Current yearly contribution ($)", 0, 1_000_000,
                                          int(base.annual_contribution_usd), step=500)
            d, e = st.columns(2)
            income = d.number_input("Desired yearly income in retirement, in today's dollars ($)", 0, 2_000_000,
                                    int(base.desired_income_today_usd), step=1_000)
            ss = e.number_input("Social Security or pension estimate per year ($, optional)", 0, 500_000,
                                int(base.social_security_or_pension_usd or 0), step=500)
            e.markdown("[Get your Social Security estimate at ssa.gov/myaccount](https://www.ssa.gov/myaccount/)")
            employer = st.text_input("Your employer's stock ticker, if you hold any (optional)",
                                     value=base.employer_ticker or "", max_chars=6,
                                     help="Used only to show how much of your portfolio depends on your employer.")
            st.markdown("**Risk quiz** (one answer per question)")
            picks = []
            for i, q in enumerate(QUIZ):
                default_index = (answers[i] - 1) if answers else 2
                choice = st.radio(q["question"], q["options"], index=default_index, horizontal=True, key=f"q-{base_key}-{i}")
                picks.append(q["options"].index(choice) + 1)
            if st.form_submit_button("Update" if filled else "Save and show my plan", type="primary"):
                try:
                    new_user = UserProfile(age=age, retirement_age=retire, desired_income_today_usd=income,
                                           social_security_or_pension_usd=ss or None, annual_contribution_usd=contribution,
                                           employer_ticker=employer or None)
                except ValueError:
                    st.error("Please check your answers: the employer ticker should be 1 to 6 letters, like MSFT.")
                    return
                state.about[base_key] = (new_user, picks)
                st.rerun()


# --- tabs ---------------------------------------------------------------------------

def plot_layout(fig: go.Figure, height: int = 420) -> go.Figure:
    fig.update_layout(template="plotly_dark", paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", height=height, margin=dict(l=10, r=10, t=30, b=10),
                      font=dict(family="Source Sans Pro, Helvetica, Arial, sans-serif", color=INK, size=13),
                      legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0))
    return fig


@st.cache_resource(show_spinner=False)
def sp500_benchmark():
    return load_benchmark() if BENCHMARK_FILE.exists() else None


# Gold shades plus greys for the donut; every slice is also labelled, so colour is never the only cue.
SLICE_COLORS = ["#D4AF37", "#B8902A", "#F1D67A", "#8A6D1C", "#E6C76B", "#6B5A33", "#A08A55", "#5A5548", "#C9B98A",
                "#3A362C", "#8C877A", "#2A2720"]


def what_you_own(inputs: Inputs, xr) -> None:
    """Top 10 companies and sector mix next to the S&P 500 (all numbers from Python; no AI)."""
    bench = sp500_benchmark()
    st.markdown("#### What you own")
    if bench is None:
        st.info("The S&P 500 reference (data/reference/sp500_benchmark.json) is missing; run scripts/build_snapshot.py.")
        return

    look_through = st.toggle("Count shares inside funds", value=True, key=f"lt-{inputs.key}",
                             help="On: companies you own through funds count too. Off: direct holdings only.")
    top = top_ten(xr, bench, look_through=look_through)
    mode = "counting shares inside funds" if look_through else "direct holdings only"
    st.markdown(f'<div class="sl-headline">Top 10 = {100 * top.weight:.1f}% of your money vs. '
                f'{100 * top.sp500_weight:.1f}% of the S&P 500</div>'
                f'<p class="sl-muted">Your 10 largest companies ({mode}) as a share of your whole portfolio, next to '
                f'each company\'s weight in the S&P 500.</p>', unsafe_allow_html=True)
    if not top.rows:
        st.markdown('<p class="sl-muted">You hold no individual companies directly.</p>', unsafe_allow_html=True)
    else:
        rows = list(reversed(top.rows))  # largest at the top of a horizontal chart
        names = [f"{r.name} ({r.symbol})" for r in rows]
        sp = [100 * r.sp500_weight if r.sp500_weight is not None else 0 for r in rows]
        sp_text = [f"S&P 500 {100 * r.sp500_weight:.1f}%" if r.sp500_weight is not None else "S&P 500: not in fund's top 10"
                   for r in rows]
        fig = go.Figure([
            go.Bar(name="S&P 500", y=names, x=sp, orientation="h", marker=dict(color="#5A5548", pattern_shape="/",
                   pattern_fgcolor="#8C877A"), text=sp_text, textposition="outside", cliponaxis=False,
                   hovertemplate="%{y}: %{text}<extra></extra>"),
            go.Bar(name="You", y=names, x=[100 * r.weight for r in rows], orientation="h", marker_color=ACCENT,
                   text=[f"You {100 * r.weight:.1f}%" for r in rows], textposition="outside", cliponaxis=False,
                   hovertemplate="%{y}: %{text}<extra></extra>"),
        ])
        fig.update_layout(barmode="group", bargap=0.25, legend=dict(traceorder="reversed"))
        fig.update_xaxes(ticksuffix="%", range=[0, max([100 * r.weight for r in rows] + sp) * 1.45])
        st.plotly_chart(plot_layout(fig, 90 + 58 * len(rows)), key=f"top10-{inputs.key}-{look_through}")

    st.markdown("##### Industry mix")
    mine = stock_sector_weights(xr)
    if not mine:
        st.markdown('<p class="sl-muted">No stock holdings to sort into industries.</p>', unsafe_allow_html=True)
    else:
        slices = donut_slices(mine)
        left, right = st.columns([3, 2], vertical_alignment="center")
        with left:
            pie = go.Figure(go.Pie(labels=list(slices), values=[100 * v for v in slices.values()], hole=0.55, sort=False,
                                   marker=dict(colors=SLICE_COLORS[:len(slices)], line=dict(color=BG, width=2)),
                                   texttemplate="%{label}<br>%{value:.1f}%", textposition="outside",
                                   textfont=dict(size=14), hovertemplate="%{label}: %{value:.1f}%<extra></extra>"))
            pie.update_layout(showlegend=False, annotations=[dict(text="Your<br>stocks", showarrow=False,
                                                                  font=dict(size=14, color=INK))])
            pie = plot_layout(pie, 470)
            pie.update_layout(margin=dict(l=90, r=90, t=50, b=50))
            st.plotly_chart(pie, key=f"donut-{inputs.key}")
        with right:
            for row in sector_rows(xr, bench)[:3]:
                st.markdown(f"- {safe(comparison_sentence(row))}")
            other = [k for k in mine if k not in slices]
            if other:
                st.caption(f"Other = sectors under 3% each: {', '.join(other)}.")
            labels = {"cash": "cash (including cash inside funds)"}
            bonds_cash = [f"{labels.get(k, SECTOR_LABELS.get(k, k).lower())} {100 * xr.sectors[k]:.1f}%"
                          for k in ("bonds", "cash", "other", "unclassified") if xr.sectors.get(k, 0) >= 0.0005]
            st.caption("Shares of your stock holdings only" + (f"; not included: {', '.join(bonds_cash)} of the portfolio."
                                                                 if bonds_cash else "."))

        st.markdown("##### Each sector: you vs. the S&P 500 (biggest differences first)")
        rows = list(reversed(sector_rows(xr, bench)))
        names = [r.sector for r in rows]
        fig = go.Figure([
            go.Bar(name="S&P 500", y=names, x=[100 * r.sp500_weight for r in rows], orientation="h",
                   marker=dict(color="#5A5548", pattern_shape="/", pattern_fgcolor="#8C877A"),
                   text=[f"S&P 500 {100 * r.sp500_weight:.1f}%" for r in rows], textposition="outside", cliponaxis=False),
            go.Bar(name="You", y=names, x=[100 * r.weight for r in rows], orientation="h", marker_color=ACCENT,
                   text=[f"You {100 * r.weight:.1f}%" for r in rows], textposition="outside", cliponaxis=False),
        ])
        fig.update_layout(barmode="group", bargap=0.25, legend=dict(traceorder="reversed"))
        fig.update_xaxes(ticksuffix="%", range=[0, max(max(100 * r.weight, 100 * r.sp500_weight) for r in rows) * 1.4])
        st.plotly_chart(plot_layout(fig, 90 + 52 * len(rows)), key=f"sectors-sp-{inputs.key}")
    st.caption(f"{bench.label} ({bench.source}). The fund reports only its top 10 holdings. "
               "These are comparisons, not recommendations.")
    st.divider()


def xray_tab(inputs: Inputs) -> None:
    xr = xray(inputs.values, inputs.profiles, top_n=1_000_000)
    lt = inputs.facts["look_through"]
    a, b, c = st.columns(3)
    top = xr.top_companies[0] if xr.top_companies else None
    a.metric("Largest company, counting funds", f"{top.symbol}: {100 * top.weight:.1f}%" if top else "None")
    b.metric("Traced to named companies", f"{lt['coverage_pct_of_invested']}%",
             help="Share of the invested money (not cash) that could be traced to individual companies.")
    c.metric("Cash", f"{100 * xr.cash_weight:.1f}%")

    what_you_own(inputs, xr)

    frame = treemap_frame(xr)
    colors = {"root": BG, "company": "#D4AF37", "part": "#B8902A", "other": "#8A6D1C",
              "untraced": "#3A362C", "cash": "#2A2720"}
    dark_text = {"company", "part"}  # black labels on the brighter gold tiles, light labels elsewhere
    fig = go.Figure(go.Treemap(
        ids=frame["id"], labels=frame["label"], parents=frame["parent"], values=frame["value"], branchvalues="total",
        marker=dict(colors=[colors[k] for k in frame["kind"]], line=dict(color=BG, width=2)),
        textfont=dict(color=["#0A0A0A" if k in dark_text else INK for k in frame["kind"]], size=13),
        customdata=frame["pct"], texttemplate="%{label}<br>%{customdata:.1f}%",
        hovertemplate="%{label}<br>$%{value:,.0f} (%{customdata:.1f}% of portfolio)<extra></extra>",
        maxdepth=2,
    ))
    st.markdown("##### What you really own, counting the shares inside your funds")
    st.plotly_chart(plot_layout(fig, 480), key=f"tree-{inputs.key}")
    st.caption("Funds report only their largest holdings, so company shares here are minimums. "
               "Click a company to see where it comes from.")

    left, right = st.columns(2)
    with left:
        st.markdown("##### Largest companies")
        st.dataframe(company_table(xr), hide_index=True,
                     column_config={"Dollars": st.column_config.NumberColumn(format="$%,.0f", width="small"),
                                    "Company": st.column_config.TextColumn(width="medium"),
                                    "Ticker": st.column_config.TextColumn(width="small"),
                                    "Share (%)": st.column_config.NumberColumn(width="small"),
                                    "Direct (%)": st.column_config.NumberColumn(width="small")})
    with right:
        st.markdown("##### How much your funds overlap")
        table = overlap_table(xr)
        if table.empty:
            st.markdown('<p class="sl-muted">No overlap between your funds\' reported holdings.</p>', unsafe_allow_html=True)
        else:
            st.dataframe(table, hide_index=True)

    st.markdown("#### Concentration")
    employer = inputs.extra["user"].employer_ticker if inputs.extra.get("user") is not None else None
    conc = concentration(inputs.values, inputs.profiles, threshold=CONCENTRATION_THRESHOLD, employer_ticker=employer)
    a, b, c = st.columns(3)
    lc = conc.largest_company
    a.metric("Largest single company", f"{lc.symbol}: {100 * lc.weight:.1f}%" if lc else "None",
             help="Counting shares held directly and inside funds.")
    b.metric("Effective number of holdings", f"{conc.effective_holdings:.1f}" if conc.effective_holdings else "None",
             help="1 / HHI of position weights: 10 equal positions count as 10.")
    c.metric("Effective number of companies", f"{conc.effective_companies_upper_bound:.0f}"
             if conc.effective_companies_upper_bound else "None",
             help="From the look-through. Fund holdings beyond the reported top 10 count as fully spread out, so this is an upper bound.")
    if conc.flags:
        st.markdown(f"**Companies above {100 * conc.threshold:.0f}% of the portfolio**")
        st.dataframe(pd.DataFrame([{"Company": f.name, "Ticker": f.ticker, "Share (%)": round(100 * f.weight, 1),
                                    "Held directly (%)": round(100 * f.direct_weight, 1)} for f in conc.flags]),
                     hide_index=True)
    else:
        st.markdown(f'<p class="sl-muted">No single company is above {100 * conc.threshold:.0f}% of the portfolio.</p>',
                    unsafe_allow_html=True)
    if conc.employer_stock is not None:
        e = conc.employer_stock
        text = (f"Your employer's stock ({e.ticker}) is {100 * e.weight:.1f}% of the portfolio, counting funds "
                f"({100 * e.direct_weight:.1f}% held directly).")
        if e.over_threshold:
            text += f" That is above the {100 * conc.threshold:.0f}% concentration threshold."
        st.markdown(f'<p class="sl-muted">{safe(text)}</p>', unsafe_allow_html=True)
    if conc.sectors_vs_market:
        st.markdown("**Stock sectors compared with the total US market**")
        frame = pd.DataFrame([{"Sector": SECTOR_LABELS.get(x.sector, x.sector), "You": 100 * x.portfolio,
                               "Total market": 100 * x.reference} for x in conc.sectors_vs_market])
        fig = go.Figure([go.Bar(name="Your stocks", x=frame["Sector"], y=frame["You"], marker_color=ACCENT),
                         go.Bar(name="Total US market (VTI)", x=frame["Sector"], y=frame["Total market"], marker_color="#8C877A")])
        fig.update_layout(barmode="group")
        fig.update_yaxes(ticksuffix="%", title="Share of stocks")
        st.plotly_chart(plot_layout(fig, 320), key=f"sectors-{inputs.key}")


def fee_tab(inputs: Inputs) -> None:
    fees = fee_report(inputs.values, inputs.profiles)
    curves = fee_curves(fees)
    p30 = fees.projections[-1]
    a, b = st.columns([1, 2])
    with a:
        st.markdown(f"""<div class="sl-callout"><div class="sl-muted">Fees over 30 years</div>
            <div class="big">{money(p30.cost_vs_no_fees_usd)}</div>
            <div class="sl-muted">at a weighted expense ratio of {100 * fees.weighted_expense_ratio:.3f}% a year
            ({money(fees.annual_cost_usd)} this year), assuming {100 * fees.gross_return:.0f}% growth a year before fees
            and no new deposits.</div></div>""", unsafe_allow_html=True)
        st.write("")
        extra = p30.cost_vs_baseline_usd
        if extra > 0:
            st.markdown(f'<p class="sl-muted">That is {money(extra)} more than the same money in index funds charging '
                        f'{100 * fees.baseline_expense_ratio:.2f}%.</p>', unsafe_allow_html=True)
        else:
            st.markdown(f'<p class="sl-muted">That is {money(-extra)} less than index funds charging '
                        f'{100 * fees.baseline_expense_ratio:.2f}%, mostly because individual stocks have no fund fee.</p>',
                        unsafe_allow_html=True)
        if fees.funds:
            st.markdown("##### Costliest funds")
            st.dataframe(pd.DataFrame([{"Fund": f.symbol, "Expense ratio (%)": round(100 * f.expense_ratio, 3),
                                        "Per year ($)": round(f.annual_cost_usd)} for f in fees.funds[:5]]), hide_index=True)
    with b:
        fig = go.Figure()
        styles = [(MIST, "dot"), ("#8C877A", "dash"), (ACCENT, "solid")]
        for (color, dash), column in zip(styles, curves.columns[1:]):
            fig.add_trace(go.Scatter(x=curves["year"], y=curves[column], name=column, mode="lines",
                                     line=dict(color=color if color != MIST else "#8C877A", width=3 if dash == "solid" else 2, dash=dash),
                                     hovertemplate="Year %{x}: $%{y:,.0f}<extra>" + column + "</extra>"))
        fig.update_yaxes(tickprefix="$", tickformat=",.0f", title=None)
        fig.update_xaxes(title="Years from today")
        st.markdown("##### Today's balance growing with and without fees")
        st.plotly_chart(plot_layout(fig), key=f"fees-{inputs.key}")


def crash_tab(inputs: Inputs) -> None:
    results = {r.key: r for r in replay(inputs.values, inputs.adj)}
    st.markdown('<p class="sl-muted">Today\'s mix, held through past crises, at today\'s dollar size. '
                'This describes history, not a forecast.</p>', unsafe_allow_html=True)
    for scenario in SCENARIOS:
        r = results.get(scenario.key)
        if r is None:
            continue
        st.markdown(f"##### {r.label} ({r.start[:7]} to {r.end[:7]})")
        a, b, c = st.columns(3)
        a.metric("Worst fall", f"{100 * r.max_drawdown:.1f}%")
        b.metric("In dollars", money(r.max_drawdown_usd))
        c.metric("Months from bottom to recovery",
                 f"{r.months_to_recover:.1f}" if r.months_to_recover is not None else "Not recovered in data")
        frame = crash_frame(inputs.values, inputs.adj, scenario, r.recovery_date)
        fig = go.Figure(go.Scatter(x=frame["date"], y=frame["drawdown_pct"], fill="tozeroy", mode="lines",
                                   line=dict(color=INK, width=2), fillcolor="rgba(212,175,55,0.18)",
                                   customdata=frame["value_usd"],
                                   hovertemplate="%{x|%b %Y}: %{y:.1f}% below peak ($%{customdata:,.0f})<extra></extra>"))
        fig.update_yaxes(ticksuffix="%", title="Below previous peak")
        st.plotly_chart(plot_layout(fig, 280), key=f"crash-{inputs.key}-{scenario.key}")
        if r.proxies_used:
            st.caption("Stand-ins for holdings that did not exist yet: "
                       + ", ".join(f"{k} used {v}" for k, v in r.proxies_used.items()) + ".")


def plan_tab(inputs: Inputs) -> None:
    user, answers, household = inputs.extra["user"], inputs.extra["answers"], inputs.extra["household"]
    if user is None:
        st.info("Fill in About you above to see your retirement plan.")
        return
    key = inputs.extra["base_key"]
    with st.expander("Assumptions", expanded=params.get("assumptions") == "open"):
        st.markdown('<p class="sl-muted">Move a slider and the plan recalculates instantly in Python. The AI explanation '
                    'in Top 3 uses the default assumptions.</p>', unsafe_allow_html=True)
        a, b = st.columns(2)
        wr = a.slider("Withdrawal rate (% of the nest egg per year)", 3.5, 4.5, 100 * ASSUMPTIONS["withdrawal_rate"],
                      step=0.1, key=f"wr-{key}") / 100
        tax = b.slider("Retirement tax rate on 401(k)/403(b) and traditional IRA (%)", 0, 40,
                       int(100 * RETIREMENT_TAX_RATE), step=1, key=f"tax-{key}") / 100
        defaults = ASSUMPTIONS["real_returns"]
        c, d, e = st.columns(3)
        returns = {
            "cautious": c.slider("Cautious real return (%)", 0.0, 8.0, 100 * defaults["cautious"], step=0.5, key=f"rc-{key}") / 100,
            "middle": d.slider("Middle real return (%)", 0.0, 8.0, 100 * defaults["middle"], step=0.5, key=f"rm-{key}") / 100,
            "strong": e.slider("Strong real return (%)", 0.0, 10.0, 100 * defaults["strong"], step=0.5, key=f"rs-{key}") / 100,
        }
        st.caption("Real returns are after inflation and before fund fees; the portfolio's weighted expense ratio "
                   "is subtracted. All amounts are in today's dollars.")

    fees = fee_report(inputs.values, inputs.profiles)
    balances = household.balances_by_type()
    counted = counted_balance(balances, tax)
    plan = make_plan(user, counted, fees.weighted_expense_ratio, wr, returns, tax)
    headline, _, _ = gap_headline(plan, user.desired_income_today_usd)

    st.markdown(f'<div class="sl-headline">{safe(headline)}</div>', unsafe_allow_html=True)
    st.markdown(f'<div class="sl-estimate">{PLAN_LABEL}</div>', unsafe_allow_html=True)
    a, b, c = st.columns(3)
    a.metric("Target nest egg", money(plan.target_nest_egg_usd), help=PLAN_LABEL)
    b.metric("Counted balance today", money(plan.counted_balance_usd), help=PLAN_LABEL)
    c.metric("Years to retirement", f"{plan.years_to_retirement}" if plan.status == "saving" else "Retired")
    st.markdown(f'<p class="sl-muted">Target = (desired income {money(user.desired_income_today_usd)} minus Social Security '
                f'or pension {money(user.guaranteed_income_usd)}) divided by a {100 * wr:.1f}% withdrawal rate.</p>',
                unsafe_allow_html=True)

    if plan.status == "saving":
        band = plan_band_frame(plan)
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=band["age"], y=band["strong"], line=dict(width=0), showlegend=False, hoverinfo="skip"))
        fig.add_trace(go.Scatter(x=band["age"], y=band["cautious"], fill="tonexty", fillcolor="rgba(212,175,55,0.16)",
                                 line=dict(width=0), name="Cautious to strong range",
                                 hovertemplate="Age %{x}: cautious $%{y:,.0f}<extra></extra>"))
        fig.add_trace(go.Scatter(x=band["age"], y=band["middle"], line=dict(color=ACCENT, width=3), name="Middle scenario",
                                 hovertemplate="Age %{x}: $%{y:,.0f}<extra>Middle</extra>"))
        fig.add_trace(go.Scatter(x=band["age"], y=band["target"], line=dict(color=INK, width=2, dash="dash"),
                                 name="Target nest egg", hovertemplate="Target $%{y:,.0f}<extra></extra>"))
        fig.update_yaxes(tickprefix="$", tickformat=",.0f", title=None)
        fig.update_xaxes(title="Age")
        st.markdown("##### Projected balance in three scenarios (planning estimate, not a guarantee)")
        st.plotly_chart(plot_layout(fig, 380), key=f"plan-{inputs.key}")

    st.dataframe(pd.DataFrame([{
        "Scenario": s.name.capitalize(), "Return after fees (%)": round(100 * s.net_return, 2),
        "Projected at retirement": round(s.projected_at_retirement_usd),
        "Gap to target": round(max(s.gap_usd, 0)),
        "Yearly saving needed": round(s.required_annual_contribution_usd) if s.required_annual_contribution_usd is not None else None,
        "Supported yearly income": round(s.supported_income_usd)} for s in plan.scenarios]), hide_index=True,
        column_config={k: st.column_config.NumberColumn(format="$%,.0f") for k in
                       ("Projected at retirement", "Gap to target", "Yearly saving needed", "Supported yearly income")})
    st.caption(f"Every amount in this table is a {PLAN_LABEL.lower()[:-1]}.")

    left, right = st.columns(2)
    with left:
        st.markdown("##### Balances by account type")
        frame = balances_frame(balances, tax)
        fig = go.Figure([go.Bar(name="Balance", y=frame["Account type"], x=frame["Balance"], orientation="h", marker_color="#8C877A"),
                         go.Bar(name="Counted toward retirement", y=frame["Account type"], x=frame["Counted toward retirement"],
                                orientation="h", marker_color=ACCENT)])
        fig.update_layout(barmode="group")
        fig.update_xaxes(tickprefix="$", tickformat=",.0f")
        st.plotly_chart(plot_layout(fig, 260), key=f"accounts-{inputs.key}")
        st.caption(f"Pre-tax accounts are counted after a flat {100 * tax:.0f}% tax; Roth, HSA and taxable count in full.")
    with right:
        st.markdown("##### Your stock and bond mix")
        mix = asset_mix(xray(inputs.values, inputs.profiles))
        st.dataframe(pd.DataFrame([{"Asset": k, "Share (%)": round(v, 1)} for k, v in mix.items() if v > 0.05]),
                     hide_index=True)
        if answers:
            risk = risk_profile(user, answers)
            ranges = load_risk_ranges()
            band, position = mix_vs_range(mix["Stocks"], risk.label, ranges)
            if band:
                label = ranges.get("label", RANGE_LABEL)
                st.markdown(f"Stocks are **{mix['Stocks']:.1f}%** of your portfolio. The rule-of-thumb range for a "
                            f"**{risk.label.lower()}** profile is **{band[0]:.0f}% to {band[1]:.0f}%** stocks, "
                            f"so your mix is **{position}** that range.")
                st.markdown(f'<span class="sl-badge">{safe(label)}</span>', unsafe_allow_html=True)
                if ranges.get("is_illustrative"):
                    st.caption("These ranges are not from a cited source yet (data/reference/risk_profile_ranges.json).")
        else:
            st.markdown('<p class="sl-muted">Answer the risk quiz in About you to compare your mix with a planning rule of thumb (not a recommendation).</p>',
                        unsafe_allow_html=True)


def top3_tab(inputs: Inputs, fluency: str) -> None:
    with st.spinner("Explaining the numbers..."):
        result = explain(inputs, fluency)
    labels = {"ai": "AI explanation, checked against the computed numbers",
              "ai_rewrite": "AI explanation, revised once and checked against the computed numbers",
              "template": "Standard summary built directly from the computed numbers"}
    st.markdown(f'<div class="sl-source">{labels[result.source]}</div>', unsafe_allow_html=True)
    message = friendly_note(result.note, settings.demo_mode)
    if message:
        st.info(message)
    for card in result.cards[:3]:
        st.markdown(f'<div class="sl-card"><h4>{safe(card.title)}</h4><p>{safe(card.body)}</p></div>',
                    unsafe_allow_html=True)
    st.markdown("##### Questions to ask a fiduciary advisor")
    for q in result.advisor_questions:
        st.markdown(f"- {safe(q)}")


def possible_shifts(inputs: Inputs, fluency: str):
    """The Python-computed shifts for this portfolio, worded by the shifts agent (cached per portfolio and fluency)."""
    state.setdefault("shifts", {})
    cache_key = (inputs.key, fluency)
    if cache_key not in state.shifts:
        user, answers = inputs.extra["user"], inputs.extra["answers"]
        risk_label = risk_profile(user, answers).label if user is not None and answers else None
        facts, details, banned = shift_rules.prepare(inputs.values, inputs.profiles, sp500_benchmark(),
                                                     household=inputs.extra.get("household"), risk_label=risk_label)
        result = orchestrator.run_shifts(facts, details, fluency, context(), banned)
        if result.source == "template" and not settings.demo_mode and inputs.sample:
            saved = orchestrator.run_shifts(facts, details, fluency, context(demo=True), banned)
            if saved.source == "ai":
                result = saved.model_copy(update={"note": "The AI spending limit was reached, so this is a saved explanation."})
        state.shifts[cache_key] = (facts, result)
    return state.shifts[cache_key]


def shifts_tab(inputs: Inputs, fluency: str) -> None:
    st.markdown(f'<div class="sl-ai-warning"><b>Generative AI, not a financial advisor.</b> {safe(AI_DISCLAIMER)}</div>',
                unsafe_allow_html=True)
    with st.spinner("Working out possible shifts..."):
        facts, result = possible_shifts(inputs, fluency)
    kinds = {x["kind"] for x in facts["shifts"]}
    if result.source == "none":
        st.markdown('<p class="sl-muted">No shifts stand out: your sector mix, stock share, single-company exposure, fees '
                    'and account placement are all within the reference points this app uses.</p>', unsafe_allow_html=True)
        return
    labels = {"ai": "Suggestions computed in Python from your numbers; wording by AI, checked against those numbers",
              "template": "Suggestions computed in Python from your numbers (standard wording)"}
    st.markdown(f'<div class="sl-source">{labels[result.source]}</div>', unsafe_allow_html=True)
    message = friendly_note(result.note, settings.demo_mode)
    if message:
        st.info(message)
    st.markdown(f"**{safe(result.summary)}**")
    for item in result.items:
        st.markdown(f'<div class="sl-card"><h4>{safe(item.title)}</h4><p>{safe(item.body)}</p></div>',
                    unsafe_allow_html=True)
    notes = []
    if "asset_mix" in kinds:
        notes.append("Stock ranges per risk profile are a planning rule of thumb, not a recommendation.")
    if "sector" in kinds:
        notes.append(sp500_benchmark().label + ".")
    if "fees" in kinds:
        notes.append("Fee dollars over 30 years are an estimate.")
    if notes:
        st.caption(" ".join(notes))
    st.markdown('<p class="sl-muted">Want to see how a shift toward more or fewer stocks would have played out? Open the '
                '<b>Try a Shift</b> tab to replay a pretend $1,000 through real market history.</p>', unsafe_allow_html=True)


def sandbox_run(inputs: Inputs, fluency: str):
    """The pretend-$1,000 replay (Python) and its AI explanation, cached per portfolio and fluency."""
    state.setdefault("sandbox", {})
    cache_key = (inputs.key, fluency)
    if cache_key not in state.sandbox:
        result, facts, banned = sandbox_engine.prepare(inputs.values, inputs.profiles, inputs.adj, sp500_benchmark())
        explanation = orchestrator.run_sandbox(facts, fluency, context(), banned)
        if explanation.source == "template" and not settings.demo_mode and inputs.sample:
            saved = orchestrator.run_sandbox(facts, fluency, context(demo=True), banned)
            if saved.source == "ai":
                explanation = saved.model_copy(update={"note": "The AI spending limit was reached, so this is a saved explanation."})
        state.sandbox[cache_key] = (result, explanation)
    return state.sandbox[cache_key]


LINE_STYLES = {"Conservative": ("#8C877A", "dot"), "Moderate": ("#F1D67A", "dash"), "Risky": ("#D4AF37", "solid"),
               sandbox_engine.YOURS: ("#4CB85A", "longdash")}


def sandbox_tab(inputs: Inputs, fluency: str) -> None:
    st.markdown(f'<div class="sl-ai-warning"><b>Pretend money only.</b> See how ${sandbox_engine.AMOUNT_USD:,} would '
                'have done in three mixes of asset types, replayed through real market history, next to your own '
                'portfolio. The mixes use index stand-ins for each asset type and are illustrations, not '
                'recommendations. Generative AI, not a professional financial advisor.</div>', unsafe_allow_html=True)
    with st.spinner("Replaying market history..."):
        result, explanation = sandbox_run(inputs, fluency)

    cols = st.columns(3)
    for col, s in zip(cols, result.strategies[:3]):
        with col, st.container(border=True):
            mix = "<br>".join(f"{v:.0f}% {safe(k)}" for k, v in s.mix_pct.items())
            st.markdown(f'<p class="sl-sample-title">{safe(s.name)}</p><p class="sl-muted">{mix}</p>',
                        unsafe_allow_html=True)
            st.metric("Value today", money(s.end_usd), f"{s.annual_return_pct}% a year", delta_color="off")
            st.markdown(f'<p class="sl-muted">Worst drop: <b>{s.worst_drop_pct}%</b></p>', unsafe_allow_html=True)

    fig = go.Figure()
    end_values = {x.name: x.end_usd for x in result.strategies}  # labels use the same rounded value as the cards
    for name, values in result.paths.items():
        color, dash = LINE_STYLES[name]
        fig.add_trace(go.Scatter(x=result.dates, y=values, name=name, mode="lines", line=dict(color=color, dash=dash, width=3),
                                 hovertemplate=name + ": $%{y:,.0f} on %{x}<extra></extra>"))
        # on a log axis, annotation positions are given as log10 of the value
        fig.add_annotation(x=result.dates[-1], y=math.log10(values[-1]), text=f"{name} {money(end_values[name])}",
                           showarrow=False, xanchor="left", xshift=6, font=dict(color=color, size=12))
    top = max(max(v) for v in result.paths.values())
    ticks = [t for t in (250, 500, 1000, 2000, 5000, 10000, 20000, 50000, 100000) if t <= top * 1.5]
    fig = plot_layout(fig, 460)  # shared styling first, then this chart's own margin and axes
    fig.update_yaxes(type="log", tickvals=ticks, ticktext=[f"${t:,}" for t in ticks])
    fig.update_xaxes(range=[result.dates[0], result.dates[-1]])
    fig.update_layout(margin=dict(l=10, r=190, t=30, b=10))
    st.markdown(f"##### ${sandbox_engine.AMOUNT_USD:,} from {result.start[:7]} to {result.end[:7]}")
    st.plotly_chart(fig, key=f"sandbox-{inputs.key}")
    st.caption("The value axis grows by multiples so every line fits; each line also has its own dash pattern.")

    st.markdown("##### How far each one fell in past crashes")
    crashes = list(result.strategies[0].crash_drop_pct)
    st.dataframe(pd.DataFrame([{"Strategy": s.name, "Worst drop (%)": s.worst_drop_pct,
                                **{f"{c} (%)": s.crash_drop_pct.get(c) for c in crashes}} for s in result.strategies]),
                 hide_index=True)
    st.caption(f"{sandbox_engine.HINDSIGHT_NOTE} Cash is assumed not to grow. Past results do not predict future results.")

    labels = {"ai": "AI explanation, checked against the replay numbers",
              "template": "Standard summary built from the replay numbers"}
    st.markdown("##### What this shows")
    st.markdown(f'<div class="sl-source">{labels[explanation.source]}</div>', unsafe_allow_html=True)
    message = friendly_note(explanation.note, settings.demo_mode)
    if message:
        st.info(message)
    st.markdown(f'<div class="sl-card"><p>{safe(explanation.summary)}</p></div>', unsafe_allow_html=True)
    for point in explanation.points:
        st.markdown(f"- {safe(point)}")


def pause_panel(inputs: Inputs) -> None:
    with st.container(border=True):
        st.markdown("### Pause button")
        st.markdown('<p class="sl-muted">Thinking about a big move? See what it would change, and what happened to '
                    'people who made similar moves in past crashes, before you decide.</p>', unsafe_allow_html=True)
        holdings = [t for t in inputs.values if t != CASH]
        text = st.text_input("What are you thinking of doing?", value=params.get("pause", ""),
                             placeholder="For example: sell everything, move half to cash, buy $5,000 of QQQ")
        pick = st.segmented_control("Or pick one", ["Sell everything", "Move to cash", "Buy more of a stock"],
                                    key=f"pick-{inputs.key}")
        action = None
        if pick == "Sell everything":
            action = Action(kind="sell_all")
        elif pick == "Move to cash":
            pct = st.slider("Share of the portfolio to move to cash (%)", 10, 100, 50, step=10)
            action = Action(kind="sell_pct", pct=pct)
        elif pick == "Buy more of a stock":
            a, b = st.columns(2)
            options = holdings_for_buy(inputs.values)
            ticker = a.selectbox("Which one", options, index=options.index("QQQ") if inputs.sample == "beginner" else 0)
            amount = b.number_input("New money to invest ($)", 100, 1_000_000, 5_000, step=500)
            action = Action(kind="buy", ticker=ticker, amount_usd=amount)
        elif text:
            action = parse_action(text, holdings)
            if action is None:
                st.warning("I could not read that as a trade. Try one of the quick picks, or a phrase like "
                           "\"sell 30%\", \"sell all of VOO\" or \"buy $5,000 of QQQ\".")

        run = st.button("Check it", type="primary", disabled=action is None) or (bool(params.get("pause")) and action is not None)
        if not (run and action):
            return
        try:
            with st.spinner("Comparing before and after..."):
                facts, result = pause_check(inputs, action)
        except ValueError as exc:
            st.error(f"That change could not be checked: {exc}")
            return

        st.markdown(f"#### {safe(facts['proposed_action'])}")
        ctx = facts.get("investor_context")
        if ctx:
            horizon = ("You are retired, so some of this money may be needed during a downturn."
                       if ctx["status"] == "retired" else f"You are {ctx['years_to_retirement']} years from retirement.")
            if ctx.get("slowest_recovery_shown_months") is not None:
                horizon += (f" In the crises below, the market took between {ctx['fastest_recovery_shown_months']} and "
                            f"{ctx['slowest_recovery_shown_months']} months to climb back from its bottom.")
            if ctx.get("risk_profile"):
                horizon += f" Your risk profile is {ctx['risk_profile'].lower()}."
            st.markdown(f'<div class="sl-callout">{safe(horizon)}</div>', unsafe_allow_html=True)
        message = friendly_note(result.note, settings.demo_mode)
        if message:
            st.info(message)
        st.markdown(f'<div class="sl-card"><p>{safe(result.summary)}</p></div>', unsafe_allow_html=True)
        for point in result.points:
            st.markdown(f"- {safe(point)}")

        before, after = facts["before"], facts["after"]

        def top_item(d: dict) -> str:
            return f"{next(iter(d))} {next(iter(d.values()))}%" if d else "None"

        rows = [("Total value", money(before["total_usd"]), money(after["total_usd"])),
                ("Cash", f"{before['cash_pct']}%", f"{after['cash_pct']}%"),
                ("Largest company", top_item(before["top_companies_pct"]), top_item(after["top_companies_pct"])),
                ("Largest sector", top_item(before["top_sectors_pct"]), top_item(after["top_sectors_pct"]))]
        names = {s.key: s.label for s in SCENARIOS}
        for key, dd in before["max_drawdown_pct"].items():
            rows.append((f"Worst fall, {names.get(key, key)}",
                         f"{dd}% ({money(before['max_drawdown_usd'][key])})",
                         f"{after['max_drawdown_pct'][key]}% ({money(after['max_drawdown_usd'][key])})"))
        left, right = st.columns(2)
        with left:
            st.markdown("##### Before and after")
            st.dataframe(pd.DataFrame(rows, columns=["", "Before", "After"]), hide_index=True)
        with right:
            st.markdown("##### What the market did after past bottoms")
            hist = [{"Crisis": e["event"], "Drop (%)": e["peak_to_bottom_drop_pct"],
                     "Bottom": e["bottom_date"], "1 yr later (%)": e["gain_1y_after_bottom_pct"],
                     "3 yrs later (%)": e["gain_3y_after_bottom_pct"],
                     "Months back": e["months_bottom_to_recovery"]} for e in facts["market_history"]]
            st.dataframe(pd.DataFrame(hist), hide_index=True)
            st.caption("Someone who sold at the bottom missed the gains in the following columns.")


def docs_page() -> None:
    st.markdown('<p class="sl-kicker" style="color:#D4AF37">Project documents</p>', unsafe_allow_html=True)
    st.title("Report and slide deck")
    st.markdown('<p class="sl-muted">The final project report and presentation. Every number in them is generated from '
                'the project\'s measured results (costs, red-team tests and the prompt log).</p>', unsafe_allow_html=True)
    cost = json.loads((DOCS / "cost_results.json").read_text(encoding="utf-8")) if (DOCS / "cost_results.json").exists() else None
    red = json.loads((DOCS / "redteam_results.json").read_text(encoding="utf-8")) if (DOCS / "redteam_results.json").exists() else None
    if cost and red:
        c1, c2, c3 = st.columns(3)
        c1.metric("Cost per analysis", f"${cost['per_analysis']['mean_usd']:.4f}", border=True)
        c2.metric("10,000 users per month", f"${cost['monthly']['without_cache']['users'][-1]['total_usd']:,.2f}", border=True)
        o = red["summary"]["overall"]
        c3.metric("Red-team cases passed", f"{o['passed']}/{o['total']}", border=True)
    report_t, slides_t = st.tabs(["Final report", "Slide deck"])
    for tab, name, height, hint in ((report_t, "report.html", 1000, "Scroll inside the report, or download it to read or print."),
                                    (slides_t, "slides.html", 640, "Click a slide or use the arrow keys to move through the deck.")):
        with tab:
            path = DOCS / name
            if not path.exists():
                st.info(f"docs/{name} has not been generated yet.")
                continue
            content = path.read_text(encoding="utf-8")
            left, right = st.columns([3, 1])
            left.caption(hint)
            right.download_button(f"Download {name}", content, file_name=name, mime="text/html", width="stretch",
                                  key=f"dl-{name}")
            st.iframe(content, height=height)


def analysis_page() -> None:
    inputs = current_inputs()

    landing(compact=inputs is not None)
    if inputs is None:
        project_card()
        return

    # Two separate sidebar controls: how things are explained, and what the portfolio is compared to.
    st.sidebar.markdown("#### Fluency")
    st.sidebar.caption("How the findings are explained.")
    fluency_label = st.sidebar.radio("Fluency", list(FLUENCY), index=1, key="fluency", bind="query-params",
                                     label_visibility="collapsed")
    fluency = FLUENCY[fluency_label or "Comfortable"]
    st.sidebar.markdown("#### Risk profile")
    st.sidebar.caption("What your mix is compared to. Set by the risk quiz and your years to retirement.")
    if inputs is not None and inputs.extra["user"] is not None and inputs.extra["answers"]:
        risk = risk_profile(inputs.extra["user"], inputs.extra["answers"])
        st.sidebar.markdown(f'<div class="sl-risk"><div class="label">{safe(risk.label)}</div>'
                            f'<div class="sub">Capacity {risk.capacity} of 5 (time) and tolerance {risk.tolerance} of 5 (quiz); '
                            f'the lower one sets the profile.</div></div>', unsafe_allow_html=True)
    else:
        st.sidebar.markdown('<div class="sl-risk"><div class="sub">Not set yet. Complete About you.</div></div>',
                            unsafe_allow_html=True)
    st.sidebar.markdown(f"**AI mode:** {'Demo, no charges' if settings.demo_mode else 'Live'}")

    p = inputs.facts["portfolio"]
    note = " (demo replay, no charge)" if settings.demo_mode else ""
    head, badge = st.columns([3, 1], vertical_alignment="center")
    head.markdown(f'<div class="sl-portfolio"><h3>{safe(inputs.label)}</h3><p class="sl-muted">{money(p["total_usd"])} in '
                  f'{p["holdings_count"]} holdings across {p["accounts_count"]} account(s).</p></div>', unsafe_allow_html=True)
    badge_slot = badge.empty()
    about_you(inputs)

    xray_t, fee_t, crash_t, plan_t, top3_t, shifts_t, sandbox_t = st.tabs(TABS, key="tab", bind="query-params",
                                                                          on_change="rerun")
    with xray_t:
        if xray_t.open:
            xray_tab(inputs)
    with fee_t:
        if fee_t.open:
            fee_tab(inputs)
    with crash_t:
        if crash_t.open:
            crash_tab(inputs)
    with plan_t:
        if plan_t.open:
            plan_tab(inputs)
    with top3_t:
        if top3_t.open:
            top3_tab(inputs, fluency)
    with shifts_t:
        if shifts_t.open:
            shifts_tab(inputs, fluency)
    with sandbox_t:
        if sandbox_t.open:
            sandbox_tab(inputs, fluency)

    st.write("")
    pause_panel(inputs)

    cost = session_cost(state.sid)
    badge_slot.markdown(f'<div style="text-align:right"><span class="sl-badge">Cost of this analysis: '
                        f'{cost.tokens:,} tokens, ${cost.usd:.4f}{note}</span></div>', unsafe_allow_html=True)


def admin_with_mode() -> None:
    st.sidebar.markdown(f"**AI mode:** {'Demo, no charges' if settings.demo_mode else 'Live'}")
    admin_page()


# --- main ---------------------------------------------------------------------------

PAGES = [
    st.Page(analysis_page, title="Analyze", icon=":material/insights:", default=True),
    st.Page(docs_page, title="Report & slides", icon=":material/description:", url_path="report"),
    st.Page(admin_with_mode, title="Admin", icon=":material/admin_panel_settings:", url_path="admin"),
]
st.navigation(PAGES, position="top").run()
footer()
