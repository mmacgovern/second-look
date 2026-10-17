"""Second Look: an AI second opinion on an investment portfolio.

Run:  streamlit run app.py

All numbers come from secondlook/quant (Python). The AI only explains them,
through secondlook/orchestrator.py, and every AI answer is checked before it
is shown. Uploaded files are processed in memory and never written to disk.
"""

from __future__ import annotations

import hashlib
import html
import uuid

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from secondlook import budget, demo_runs, orchestrator
from secondlook.agents.base import AgentContext
from secondlook.config import get_settings
from secondlook.quant.accounts import ACCOUNT_TYPE_LABELS, RETIREMENT_TAX_RATE, counted_balance, household_from_csvs
from secondlook.quant.concentration import CONCENTRATION_THRESHOLD, concentration
from secondlook.quant.facts import build_facts, load_market_inputs
from secondlook.quant.fees import fee_report
from secondlook.quant.models import CASH, Action
from secondlook.quant.plan import ASSUMPTIONS, make_plan
from secondlook.quant.profile import QUIZ, UserProfile, load_persona, risk_profile
from secondlook.quant.replay import SCENARIOS, replay
from secondlook.quant.xray import SECTOR_LABELS, xray
from secondlook.ui_data import (
    FLUENCY, PLAN_LABEL, SAMPLES, Inputs, asset_mix, balances_frame, company_table, crash_frame, fee_curves,
    friendly_note, gap_headline, holdings_for_buy, load_risk_ranges, mix_vs_range, overlap_table, parse_action,
    plan_band_frame, session_cost, treemap_frame,
)

NAVY, ACCENT, SLATE, MIST = "#0B1F3A", "#0E7C86", "#64748B", "#E8EEF5"
TABS = ["X-Ray", "Fee Clock", "Crash Replay", "Retirement Plan", "Top 3 Things Worth Knowing"]
DISCLAIMER = ("Second Look provides educational information only. It is not investment, tax or legal advice, "
              "and it does not recommend buying, selling or holding any security. Past results do not predict future results.")

st.set_page_config(page_title="Second Look", layout="wide", initial_sidebar_state="expanded")

st.markdown(f"""
<style>
  .block-container {{ max-width: 1180px; padding-top: 2.4rem; padding-bottom: 6rem; }}
  h1, h2, h3, h4 {{ color: {NAVY}; letter-spacing: -0.01em; }}
  h1 {{ font-weight: 700; }}
  .sl-promise {{ font-size: 1.3rem; color: #334155; margin: 0.2rem 0 1.6rem 0; line-height: 1.5; }}
  .sl-muted {{ color: {SLATE}; font-size: 0.92rem; }}
  .sl-privacy {{ color: #475569; font-size: 0.9rem; border-left: 3px solid {ACCENT}; padding: 0.3rem 0 0.3rem 0.8rem; margin-top: 0.6rem; }}
  .sl-badge {{ display: inline-block; border: 1px solid #D5DCE6; border-radius: 999px; padding: 0.3rem 0.9rem;
               font-size: 0.85rem; color: #334155; background: #F7F9FC; }}
  .sl-card {{ border: 1px solid #E2E8F0; border-radius: 12px; padding: 1.1rem 1.3rem; margin-bottom: 0.9rem; background: #FFFFFF; }}
  .sl-card h4 {{ margin: 0 0 0.4rem 0; font-size: 1.08rem; }}
  .sl-card p {{ margin: 0; color: #1E293B; line-height: 1.6; }}
  .sl-callout {{ background: #F3F6FA; border-radius: 12px; padding: 1.1rem 1.3rem; }}
  .sl-callout .big {{ font-size: 1.9rem; font-weight: 700; color: {NAVY}; }}
  .sl-source {{ font-size: 0.8rem; color: {SLATE}; margin-bottom: 0.8rem; }}
  .sl-footer {{ position: fixed; left: 0; right: 0; bottom: 0; background: {NAVY}; color: #E2E8F0; font-size: 0.78rem;
                padding: 0.55rem 1.5rem; text-align: center; z-index: 1000100; }}
  div[data-testid="stMetricValue"] {{ color: {NAVY}; }}
  .sl-risk {{ border: 1px solid rgba(243,246,250,0.35); border-radius: 10px; padding: 0.6rem 0.8rem; margin-top: 0.3rem; }}
  .sl-risk .label {{ font-size: 1.05rem; font-weight: 700; }}
  .sl-risk .sub {{ font-size: 0.8rem; opacity: 0.8; }}
  .sl-estimate {{ font-size: 0.8rem; color: {SLATE}; font-style: italic; }}
  .sl-headline {{ font-size: 1.6rem; font-weight: 700; color: {NAVY}; margin: 0.2rem 0 0.1rem 0; }}
</style>
""", unsafe_allow_html=True)


def footer() -> None:
    st.markdown(f'<div class="sl-footer">{html.escape(DISCLAIMER)}</div>', unsafe_allow_html=True)


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
    st.dataframe(pd.DataFrame(s["by_agent"]), hide_index=True)
    st.subheader("Cost by session")
    st.dataframe(pd.DataFrame(s["by_session"]).head(50), hide_index=True)


def landing(compact: bool) -> None:
    st.title("Second Look")
    if not compact:
        st.markdown('<div class="sl-promise">See what you really own, what it costs you, and how it would have held up '
                    'in past crashes, explained in plain English.</div>', unsafe_allow_html=True)
        st.markdown("#### Try a sample portfolio")
    cols = st.columns(3)
    for col, (name, (title, blurb)) in zip(cols, SAMPLES.items()):
        with col:
            if st.button(title, key=f"sample-{name}", width="stretch",
                         type="primary" if params.get("sample") == name else "secondary"):
                params["sample"] = name
                params.pop("pause", None)
                st.rerun()
            if not compact:
                st.markdown(f'<p class="sl-muted">{safe(blurb)}</p>', unsafe_allow_html=True)

    with st.expander("Or upload your own brokerage CSV", expanded=not compact and params.get("sample") is None):
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
                params.clear()
                params["sample"] = "upload"
                st.rerun()
            except ValueError as exc:
                st.error(f"That file could not be read: {exc}")
        st.markdown('<div class="sl-privacy">Your file is processed in memory and never saved. Account numbers and '
                    'descriptions are removed before anything reaches the AI, and the AI only sees computed numbers.</div>',
                    unsafe_allow_html=True)


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
            st.markdown("**Risk quiz** (one answer per question)")
            picks = []
            for i, q in enumerate(QUIZ):
                default_index = (answers[i] - 1) if answers else 2
                choice = st.radio(q["question"], q["options"], index=default_index, horizontal=True, key=f"q-{base_key}-{i}")
                picks.append(q["options"].index(choice) + 1)
            if st.form_submit_button("Update" if filled else "Save and show my plan", type="primary"):
                new_user = UserProfile(age=age, retirement_age=retire, desired_income_today_usd=income,
                                       social_security_or_pension_usd=ss or None, annual_contribution_usd=contribution)
                state.about[base_key] = (new_user, picks)
                st.rerun()


# --- tabs ---------------------------------------------------------------------------

def plot_layout(fig: go.Figure, height: int = 420) -> go.Figure:
    fig.update_layout(template="simple_white", height=height, margin=dict(l=10, r=10, t=30, b=10),
                      font=dict(family="Source Sans Pro, Helvetica, Arial, sans-serif", color=NAVY, size=13),
                      legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0))
    return fig


def xray_tab(inputs: Inputs) -> None:
    xr = xray(inputs.values, inputs.profiles, top_n=1_000_000)
    lt = inputs.facts["look_through"]
    a, b, c = st.columns(3)
    top = xr.top_companies[0] if xr.top_companies else None
    a.metric("Largest company, counting funds", f"{top.symbol}: {100 * top.weight:.1f}%" if top else "None")
    b.metric("Traced to named companies", f"{lt['coverage_pct_of_invested']}%",
             help="Share of the invested money (not cash) that could be traced to individual companies.")
    c.metric("Cash", f"{100 * xr.cash_weight:.1f}%")

    frame = treemap_frame(xr)
    colors = {"root": "#FFFFFF", "company": "#2B6C8F", "part": "#7FB3C8", "other": "#9FB4C9",
              "untraced": "#CBD5E1", "cash": "#E2E8F0"}
    fig = go.Figure(go.Treemap(
        ids=frame["id"], labels=frame["label"], parents=frame["parent"], values=frame["value"], branchvalues="total",
        marker=dict(colors=[colors[k] for k in frame["kind"]], line=dict(color="#FFFFFF", width=2)),
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
    conc = concentration(inputs.values, inputs.profiles, threshold=CONCENTRATION_THRESHOLD)
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
    if conc.sectors_vs_market:
        st.markdown("**Stock sectors compared with the total US market**")
        frame = pd.DataFrame([{"Sector": SECTOR_LABELS.get(x.sector, x.sector), "You": 100 * x.portfolio,
                               "Total market": 100 * x.reference} for x in conc.sectors_vs_market])
        fig = go.Figure([go.Bar(name="Your stocks", x=frame["Sector"], y=frame["You"], marker_color=ACCENT),
                         go.Bar(name="Total US market (VTI)", x=frame["Sector"], y=frame["Total market"], marker_color="#94A3B8")])
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
        styles = [(MIST, "dot"), ("#94A3B8", "dash"), (ACCENT, "solid")]
        for (color, dash), column in zip(styles, curves.columns[1:]):
            fig.add_trace(go.Scatter(x=curves["year"], y=curves[column], name=column, mode="lines",
                                     line=dict(color=color if color != MIST else "#94A3B8", width=3 if dash == "solid" else 2, dash=dash),
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
                                   line=dict(color=NAVY, width=2), fillcolor="rgba(14,124,134,0.18)",
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
        fig.add_trace(go.Scatter(x=band["age"], y=band["cautious"], fill="tonexty", fillcolor="rgba(14,124,134,0.16)",
                                 line=dict(width=0), name="Cautious to strong range",
                                 hovertemplate="Age %{x}: cautious $%{y:,.0f}<extra></extra>"))
        fig.add_trace(go.Scatter(x=band["age"], y=band["middle"], line=dict(color=ACCENT, width=3), name="Middle scenario",
                                 hovertemplate="Age %{x}: $%{y:,.0f}<extra>Middle</extra>"))
        fig.add_trace(go.Scatter(x=band["age"], y=band["target"], line=dict(color=NAVY, width=2, dash="dash"),
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
        fig = go.Figure([go.Bar(name="Balance", y=frame["Account type"], x=frame["Balance"], orientation="h", marker_color="#94A3B8"),
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
                st.markdown(f"Stocks are **{mix['Stocks']:.1f}%** of your portfolio. The reference range for a "
                            f"**{risk.label.lower()}** profile is **{band[0]:.0f}% to {band[1]:.0f}%** stocks, "
                            f"so your mix is **{position}** that range.")
                if ranges.get("is_illustrative"):
                    st.caption("Reference ranges are illustrative rules of thumb, not personal advice "
                               "(data/reference/risk_profile_ranges.json).")
        else:
            st.markdown('<p class="sl-muted">Answer the risk quiz in About you to compare your mix with a reference range.</p>',
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


# --- main ---------------------------------------------------------------------------

page = st.sidebar.radio("Page", ["Analysis", "Admin"], key="page", label_visibility="collapsed", bind="query-params")
if page == "Admin":
    st.sidebar.markdown(f"**AI mode:** {'Demo, no charges' if settings.demo_mode else 'Live'}")
    admin_page()
    footer()
    st.stop()

inputs = current_inputs()

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

landing(compact=inputs is not None)
if inputs is None:
    footer()
    st.stop()

st.divider()
head, badge = st.columns([3, 1])
with head:
    st.markdown(f"### {safe(inputs.label)}")
    p = inputs.facts["portfolio"]
    st.markdown(f'<p class="sl-muted">{money(p["total_usd"])} in {p["holdings_count"]} holdings across '
                f'{p["accounts_count"]} account(s).</p>', unsafe_allow_html=True)
about_you(inputs)

xray_t, fee_t, crash_t, plan_t, top3_t = st.tabs(TABS, key="tab", bind="query-params", on_change="rerun")
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

st.write("")
pause_panel(inputs)

cost = session_cost(state.sid)
with badge:
    note = " (demo replay, no charge)" if settings.demo_mode else ""
    st.markdown(f'<div style="text-align:right"><span class="sl-badge">Cost of this analysis: '
                f'{cost.tokens:,} tokens, ${cost.usd:.4f}{note}</span></div>', unsafe_allow_html=True)
footer()
