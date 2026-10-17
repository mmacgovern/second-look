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
from secondlook.quant.accounts import ACCOUNT_TYPE_LABELS, household_from_csvs
from secondlook.quant.facts import build_facts, load_market_inputs
from secondlook.quant.fees import fee_report
from secondlook.quant.models import CASH, Action
from secondlook.quant.profile import UserProfile, load_persona
from secondlook.quant.replay import SCENARIOS, replay
from secondlook.quant.xray import SECTOR_LABELS, xray
from secondlook.ui_data import (
    FLUENCY, SAMPLES, Inputs, company_table, crash_frame, fee_curves, friendly_note, holdings_for_buy,
    overlap_table, parse_action, session_cost, treemap_frame,
)

NAVY, ACCENT, SLATE, MIST = "#0B1F3A", "#0E7C86", "#64748B", "#E8EEF5"
TABS = ["X-Ray", "Fee Clock", "Crash Replay", "Top 3 Things Worth Knowing"]
DISCLAIMER = ("Second Look provides educational information only. It is not investment, tax or legal advice, "
              "and it does not recommend buying, selling or holding any security. Past results do not predict future results.")

st.set_page_config(page_title="Second Look", layout="wide", initial_sidebar_state="collapsed")

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
                padding: 0.55rem 1.5rem; text-align: center; z-index: 999990; }}
  div[data-testid="stMetricValue"] {{ color: {NAVY}; }}
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
params = st.query_params


# --- data loading (cached) ------------------------------------------------------

@st.cache_resource(show_spinner=False)
def sample_inputs(name: str) -> Inputs:
    user, household, _ = load_persona(name)
    values = household.portfolio().values_by_symbol()
    profiles, adj = load_market_inputs(household.portfolio(), prefer_live=False, extra=["VOO", "VTI", "QQQ"])
    title, blurb = SAMPLES[name]
    return Inputs(key=f"sample:{name}", label=f"{title}: {blurb}", values=values, profiles=profiles, adj=adj,
                  facts=demo_runs.persona_facts(name), sample=name, extra={"user": user})


def upload_inputs(files: list[tuple[bytes, str]], user: UserProfile | None) -> Inputs:
    digest = hashlib.sha256(b"".join(data for data, _ in files) + repr(user).encode()).hexdigest()[:16]
    key = f"upload:{digest}"
    if key in state.get("uploads", {}):
        return state["uploads"][key]
    household = household_from_csvs([(data, account_type, None) for data, account_type in files])
    portfolio = household.portfolio()
    profiles, adj = load_market_inputs(portfolio, prefer_live=not settings.demo_mode, extra=["VOO", "VTI", "QQQ"])
    facts = build_facts(None, profiles, adj, household=household, user=user)
    inputs = Inputs(key=key, label=f"Your upload: {len(files)} file(s), {len(household.accounts)} account(s)",
                    values=portfolio.values_by_symbol(), profiles=profiles, adj=adj, facts=facts,
                    extra={"household": household, "user": user})
    state.setdefault("uploads", {})[key] = inputs
    return inputs


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
    if inputs.sample:
        return demo_runs.persona_facts(inputs.sample, action)
    household, user = inputs.extra["household"], inputs.extra["user"]
    profiles, adj = load_market_inputs(household.portfolio(), prefer_live=not settings.demo_mode,
                                       extra=[action.ticker] if action.ticker else [])
    return build_facts(None, profiles, adj, action, household=household, user=user)


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
        user = None
        if st.checkbox("Add my retirement picture (optional)"):
            a, b, c = st.columns(3)
            age = a.number_input("Age", 18, 100, 40)
            retire = b.number_input("Retirement age", 40, 100, 65)
            contribution = c.number_input("Yearly savings ($)", 0, 1_000_000, 10_000, step=1_000)
            d, e = st.columns(2)
            income = d.number_input("Yearly income wanted in retirement, today's dollars ($)", 0, 2_000_000, 60_000, step=5_000)
            ss = e.number_input("Expected Social Security or pension per year ($)", 0, 500_000, 24_000, step=1_000)
            user = UserProfile(age=age, retirement_age=retire, desired_income_today_usd=income,
                               social_security_or_pension_usd=ss, annual_contribution_usd=contribution)
        if tagged and st.button("Analyze my files", type="primary"):
            try:
                state["upload_key"] = upload_inputs(tagged, user).key
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
            return sample_inputs(choice)
    if choice == "upload" and state.get("upload_key") in state.get("uploads", {}):
        return state["uploads"][state["upload_key"]]
    return None


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
                     column_config={"Dollars": st.column_config.NumberColumn(format="$%,.0f"),
                                    "Company": st.column_config.TextColumn(width="medium")})
    with right:
        st.markdown("##### How much your funds overlap")
        table = overlap_table(xr)
        if table.empty:
            st.markdown('<p class="sl-muted">No overlap between your funds\' reported holdings.</p>', unsafe_allow_html=True)
        else:
            st.dataframe(table, hide_index=True)


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
st.sidebar.markdown(f"**AI mode:** {'Demo, no charges' if settings.demo_mode else 'Live'}")
if page == "Admin":
    admin_page()
    footer()
    st.stop()

inputs = current_inputs()
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
fluency_label = st.segmented_control("Explain it for", list(FLUENCY), default="Comfortable", key="fluency",
                                     bind="query-params", required=True)
fluency = FLUENCY[fluency_label or "Comfortable"]

xray_t, fee_t, crash_t, top3_t = st.tabs(TABS, key="tab", bind="query-params", on_change="rerun")
with xray_t:
    if xray_t.open:
        xray_tab(inputs)
with fee_t:
    if fee_t.open:
        fee_tab(inputs)
with crash_t:
    if crash_t.open:
        crash_tab(inputs)
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
