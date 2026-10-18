"""Build docs/report.html: the course report as one self-contained, print-friendly HTML file.

Every number is read at build time from the project's own outputs:
  docs/cost_results.json, docs/quality_vs_cost.json, docs/redteam_results.json,
  docs/cost_results_default_effort.json, earlier results in git history,
  the demo recordings (one real run, replayed for $0), the sample personas' Facts JSON,
  and AI_Prompts.md. Anything that does not exist yet is shown as a visible TODO instead of a number.
  No user study was run, so no user numbers appear anywhere.

Usage:
    python scripts/build_report.py
"""

from __future__ import annotations

import base64
import html
import io
import json
import re
import subprocess
import sys
import tempfile
from collections import OrderedDict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from secondlook import budget, orchestrator  # noqa: E402
from secondlook.agents import analyst, explainer, intake, pause, verifier  # noqa: E402
from secondlook.agents import sandbox as sandbox_agent  # noqa: E402
from secondlook.agents import shifts as shifts_agent  # noqa: E402
from secondlook.agents.base import AgentContext  # noqa: E402
from secondlook.config import load_settings  # noqa: E402
from secondlook.demo_runs import persona_facts  # noqa: E402
from secondlook.quant.facts import estimate_tokens, facts_json  # noqa: E402

DOCS = ROOT / "docs"
OUT = DOCS / "report.html"
SHOTS = DOCS / "screenshots"
AUTHOR = "Michael MacGovern"
REPO_URL = None   # TODO: set once the repository is pushed to GitHub
APP_URL = None    # TODO: set if the app is deployed online
VIDEO_URL = None  # TODO: set to the demo video link
LOW_EFFORT_COMMIT = "095e211"
LIVE_REDTEAM_COMMIT = "559d9e6"


def esc(text) -> str:
    return html.escape(str(text))


def todo(text: str) -> str:
    return f'<span class="todo">TODO: {esc(text)}</span>'


def usd(x: float, digits: int = 2) -> str:
    return f"${x:,.{digits}f}"


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def git_json(commit: str, path: str):
    try:
        out = subprocess.run(["git", "show", f"{commit}:{path}"], cwd=ROOT, capture_output=True, text=True, check=True).stdout
        return json.loads(out)
    except Exception:
        return None


def image(name: str, alt: str, width: int = 900) -> str:
    """A screenshot embedded as base64 (scaled down so the file stays small)."""
    path = SHOTS / name
    if not path.exists():
        return todo(f"screenshot {name} is missing")
    try:
        from PIL import Image

        img = Image.open(path).convert("RGB")
        if img.width > width:
            img = img.resize((width, round(img.height * width / img.width)))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=82, optimize=True)
        data, mime = buf.getvalue(), "image/jpeg"
    except Exception:
        data, mime = path.read_bytes(), "image/png"
    return f'<img src="data:{mime};base64,{base64.b64encode(data).decode()}" alt="{esc(alt)}">'


APP_CONTENT = (0.26, 0.95)  # left and right edges of the app's main column in a screenshot (drops the sidebar)


def crop_image(name: str, alt: str, top_frac: float, bottom_frac: float, width: int = 900,
               left_frac: float = 0.0, right_frac: float = 1.0) -> str:
    """Embed only a slice of a tall screenshot (fractions of its height, and optionally of its width)."""
    path = SHOTS / name
    if not path.exists():
        return todo(f"screenshot {name} is missing")
    from PIL import Image

    img = Image.open(path).convert("RGB")
    img = img.crop((int(img.width * left_frac), int(img.height * top_frac),
                    int(img.width * right_frac), int(img.height * bottom_frac)))
    if img.width > width:
        img = img.resize((width, round(img.height * width / img.width)))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=82, optimize=True)
    return f'<img src="data:image/jpeg;base64,{base64.b64encode(buf.getvalue()).decode()}" alt="{esc(alt)}">'


def table(headers: list[str], rows: list[list], cls: str = "") -> str:
    head = "".join(f"<th>{h}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in row) + "</tr>" for row in rows)
    return f'<table class="{cls}"><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>'


# --- data ----------------------------------------------------------------------

def traced_analysis(persona: str, fluency: str) -> dict:
    """Replay one recorded analysis for $0 and capture what each agent returned, step by step."""
    from secondlook import llm

    settings = load_settings(env_file=None, environ={"DEMO_MODE": "true"})
    facts = persona_facts(persona)
    trace: list[dict] = []
    original = llm.call_agent

    def recording_call(agent_name, *args, **kwargs):
        response = original(agent_name, *args, **kwargs)
        trace.append({"agent": agent_name, "parsed": response.parsed.model_dump() if response.parsed else None,
                      "input_tokens": response.input_tokens, "output_tokens": response.output_tokens})
        return response

    llm.call_agent = recording_call
    try:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "ledger.db"
            result = orchestrator.run_analysis(facts, fluency, AgentContext(session_id="report", settings=settings, db_path=db))
            calls = budget.calls(db)
    finally:
        llm.call_agent = original
    return {"facts": facts, "result": result, "calls": calls, "trace": trace,
            "facts_tokens": estimate_tokens(facts_json(facts))}


def real_run() -> dict:
    """The recorded mid-career / Comfortable analysis, replayed from demo/recorded for $0."""
    return traced_analysis("midcareer", "comfortable")


def rewrite_example() -> dict | None:
    """A recorded analysis where the verifier sent the first draft back, if the recordings have one."""
    for persona in ("midcareer", "beginner", "retiree"):
        for fluency in ("new", "pro", "comfortable"):
            run = traced_analysis(persona, fluency)
            if run["result"].source == "ai_rewrite":
                return {**run, "label": f"{persona} / {fluency}"}
    return None


def step_output(step: dict) -> str:
    """A short, readable excerpt of one agent's JSON output."""
    p = step["parsed"] or {}
    if step["agent"] == "analyst":
        shown = [{"title": f["title"], "dollar_fact_key": f.get("dollar_fact_key")} for f in p.get("findings", [])]
        return json.dumps({"findings": shown}, indent=1)
    if step["agent"] in ("explainer", "explainer_rewrite"):
        return json.dumps({"card_titles": [c["title"] for c in p.get("cards", [])],
                           "advisor_questions": len(p.get("advisor_questions", []))}, indent=1)
    return json.dumps(p, indent=1)[:600]


def prompt_log() -> list[dict]:
    """Entries of AI_Prompts.md with their phase and any Result lines, in order."""
    text = (ROOT / "AI_Prompts.md").read_text(encoding="utf-8")
    entries, current = [], None
    for line in text.splitlines():
        m = re.match(r"^### #(\d+) — [^—]+ — (.+)$", line)
        if m:
            current = {"n": int(m.group(1)), "phase": m.group(2).strip(), "results": []}
            entries.append(current)
        elif line.startswith("**Result") and current is not None:
            current["results"].append(re.sub(r"^\*\*Result[^*]*:\*\*\s*", "", line).strip())
    return entries


def persona_numbers() -> dict:
    mid, ret = persona_facts("midcareer"), persona_facts("retiree")
    apple = next(c for c in mid["look_through"]["top_companies"] if c["ticker"] == "AAPL")
    overlap = mid["look_through"]["fund_overlap_pct"][0]
    gfac = next(f for f in ret["fees"]["costliest_funds"] if f["ticker"] == "GFACX")
    p30 = ret["fees"]["projection"][-1]
    return {"apple": apple, "overlap": overlap, "mid_tech": mid["look_through"]["sectors_pct"].get("Technology"),
            "gfacx": gfac, "ret_fees30": p30, "ret_er": ret["fees"]["weighted_expense_ratio_pct"],
            "mid_gfc": mid["crash_replays"][0]}


# --- sections --------------------------------------------------------------------

ARCH_SVG = """
<svg viewBox="0 0 900 470" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="Second Look agent architecture">
  <defs><marker id="a" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
    <path d="M0,0 L10,5 L0,10 z" fill="#0B1F3A"/></marker></defs>
  <style>.b{fill:#F3F6FA;stroke:#0B1F3A;stroke-width:1.4}.ai{fill:#E6F3F4;stroke:#0E7C86;stroke-width:1.6}
  .py{fill:#FFFFFF;stroke:#64748B;stroke-width:1.2;stroke-dasharray:4 3}.t{font:13px sans-serif;fill:#0B1F3A}
  .s{font:11px sans-serif;fill:#475569}.l{stroke:#0B1F3A;stroke-width:1.3;fill:none;marker-end:url(#a)}</style>
  <rect class="b" x="10" y="20" width="150" height="58" rx="8"/><text class="t" x="85" y="44" text-anchor="middle">User</text>
  <text class="s" x="85" y="62" text-anchor="middle">CSV upload / About you</text>
  <rect class="py" x="200" y="10" width="200" height="78" rx="8"/><text class="t" x="300" y="34" text-anchor="middle">parse.py (Python)</text>
  <text class="s" x="300" y="52" text-anchor="middle">account numbers removed,</text><text class="s" x="300" y="66" text-anchor="middle">free text dropped</text>
  <rect class="ai" x="200" y="104" width="200" height="50" rx="8"/><text class="t" x="300" y="126" text-anchor="middle">intake agent (fast)</text>
  <text class="s" x="300" y="143" text-anchor="middle">pasted text only, grounded</text>
  <rect class="py" x="440" y="10" width="210" height="144" rx="8"/><text class="t" x="545" y="34" text-anchor="middle">quant engine (Python)</text>
  <text class="s" x="545" y="54" text-anchor="middle">look-through, fees, crash replay,</text>
  <text class="s" x="545" y="69" text-anchor="middle">plan, concentration, pause check</text>
  <text class="s" x="545" y="96" text-anchor="middle">market data: live yfinance,</text><text class="s" x="545" y="111" text-anchor="middle">offline snapshot fallback</text>
  <text class="t" x="545" y="140" text-anchor="middle">Facts JSON (&lt;2,000 tokens)</text>
  <rect class="ai" x="40" y="215" width="170" height="60" rx="8"/><text class="t" x="125" y="240" text-anchor="middle">1 analyst (smart)</text>
  <text class="s" x="125" y="258" text-anchor="middle">top 3 findings + fact keys</text>
  <rect class="ai" x="255" y="215" width="170" height="60" rx="8"/><text class="t" x="340" y="240" text-anchor="middle">2 explainer (smart)</text>
  <text class="s" x="340" y="258" text-anchor="middle">cards for one fluency level</text>
  <rect class="ai" x="470" y="215" width="190" height="60" rx="8"/><text class="t" x="565" y="240" text-anchor="middle">3 verifier (Python + fast)</text>
  <text class="s" x="565" y="258" text-anchor="middle">numbers, advice, plan ranges</text>
  <rect class="b" x="705" y="200" width="180" height="40" rx="8"/><text class="t" x="795" y="225" text-anchor="middle">shown to the user</text>
  <rect class="b" x="705" y="252" width="180" height="40" rx="8"/><text class="t" x="795" y="270" text-anchor="middle">safe template</text>
  <text class="s" x="795" y="285" text-anchor="middle">after a second failure</text>
  <rect class="ai" x="470" y="320" width="190" height="50" rx="8"/><text class="t" x="565" y="342" text-anchor="middle">pause, shifts, sandbox (smart)</text>
  <text class="s" x="565" y="358" text-anchor="middle">neutral pause; rule-based shifts</text>
  <rect class="b" x="40" y="395" width="560" height="62" rx="8"/><text class="t" x="320" y="418" text-anchor="middle">llm.py: the only Portkey caller (budget check, cache, 1 retry, demo replay)</text>
  <text class="s" x="320" y="440" text-anchor="middle">budget.py: per-call, per-session and daily limits; SQLite ledger of every call</text>
  <rect class="b" x="660" y="405" width="225" height="44" rx="8"/><text class="t" x="772" y="432" text-anchor="middle">Portkey → gpt-6-luna</text>
  <path class="l" d="M160,49 L200,49"/><path class="l" d="M400,49 L440,49"/><path class="l" d="M160,62 C180,62 180,129 200,129"/>
  <path class="l" d="M400,129 L440,129"/><path class="l" d="M545,154 C545,185 125,180 125,215"/>
  <path class="l" d="M210,245 L255,245"/><path class="l" d="M425,245 L470,245"/><path class="l" d="M660,232 L705,220"/>
  <path class="l" d="M660,258 L705,270"/><path class="l" d="M565,275 C540,300 360,300 345,277"/>
  <text class="s" x="452" y="304" text-anchor="middle">fail: 1 rewrite with reasons</text>
  <path class="l" d="M650,140 C700,150 690,330 660,345"/><path class="l" d="M600,426 L660,426"/>
  <text class="s" x="320" y="386" text-anchor="middle">every agent call (max 4 per analysis) goes through llm.py</text>
</svg>"""


def build() -> str:
    cost = load_json(DOCS / "cost_results.json")
    quality = load_json(DOCS / "quality_vs_cost.json")
    redteam = load_json(DOCS / "redteam_results.json")
    default_effort = load_json(DOCS / "cost_results_default_effort.json")
    low_effort = git_json(LOW_EFFORT_COMMIT, "docs/cost_results.json")
    live_redteam = load_json(DOCS / "redteam_results_live.json")
    live_source = "docs/redteam_results_live.json"
    if not live_redteam:  # fall back to the earlier live run kept in git history
        live_redteam = git_json(LIVE_REDTEAM_COMMIT, "docs/redteam_results.json")
        live_source = f"docs/redteam_results.json at commit {LIVE_REDTEAM_COMMIT}"
    run = real_run()
    rw = rewrite_example()
    wrong = redteam["summary"]["by_suite"].get("wrong-number drafts / verifier")
    smart_q = quality["routings"].get("MODEL_SMART for every agent")
    evidence = []
    if wrong:
        evidence.append(f"the verifier caught {wrong['passed']} of {wrong['total']} red-team drafts that contained a wrong number")
    if smart_q:
        evidence.append(f"even the larger model's first drafts passed the verifier only "
                        f"{100 * smart_q['first_draft_pass_rate']:.0f}% of the time")
    no_tools_html = f"""<p><strong>Why the agents have no tools (a deliberate choice).</strong> The tools are real, but they run
  <em>before</em> the agents, in Python: live and snapshot market data (yfinance), fund look-through, fee projections,
  crash replays, the retirement plan, the S&P 500 comparison and the Possible-Shifts rules. Their results go to the
  agents as the Facts JSON, and the verifier's checks (number matcher, advice guard) are tools the orchestrator runs on
  every draft. Letting a model call calculators itself would make the money math depend on the model's choices, add
  calls and cost, and give the model actions it does not need. Keeping the agents tool-free means every number can be
  traced to Python, and nothing an agent writes can trigger an action.{(" In this project, " + " and ".join(evidence) + ", which is why numbers are computed, not generated.") if evidence else ""}</p>"""
    pn = persona_numbers()
    log = prompt_log()

    links = [f'<a href="{esc(REPO_URL)}">GitHub repository</a>' if REPO_URL else todo("GitHub repository link (not pushed yet)"),
             f'<a href="{esc(APP_URL)}">Live app</a>' if APP_URL else todo("app link (not deployed online; runs locally with streamlit run app.py)")]

    # --- 1 -----------------------------------------------------------------------
    s1 = f"""
<section class="cover">
  <p class="kicker">AI Foundations final project report</p>
  <h1>Second Look</h1>
  <p class="lede">An AI second opinion on an investment portfolio that shows what you really own, what it costs, how it
  would have held up in past crashes and whether you are on track for retirement, with every number computed in Python
  and only explained by AI.</p>
  <p><strong>Author:</strong> {esc(AUTHOR)}</p>
  <p>{' &nbsp;·&nbsp; '.join(links)}</p>
  <div class="video">
    <video controls width="640" {'src="' + esc(VIDEO_URL) + '"' if VIDEO_URL else ''}>Demo video</video>
    <p>{f'<a href="{esc(VIDEO_URL)}">Watch the demo video</a>' if VIDEO_URL else todo("demo video link")}</p>
  </div>
  {image("00-landing.png", "Second Look landing page", 820)}
  <p class="note">Second Look uses generative AI and is not a professional financial advisor. Its suggestions are general
  shifts between industries, asset types and account types, never specific stocks or securities. Sample portfolios and
  personas are made up.</p>
</section>"""

    # --- 2 -----------------------------------------------------------------------
    s2 = f"""
<section>
  <h2>2. Motivation</h2>
  <p><strong>Who has the problem.</strong> Ordinary investors with a 401(k), an IRA and maybe a brokerage account, who own
  several funds and a few popular stocks but have never looked inside them. They are not professionals, and many do not
  pay for an advisor.</p>
  <p><strong>How they handle it today.</strong> Mostly they don't. Brokerage statements list holdings one account at a
  time and do not show that three funds own the same companies. Free tools exist but each answers one question (fund
  overlap, fees or retirement), usually after manual data entry, and general chatbots will happily do arithmetic they
  can get wrong.</p>
  <p><strong>What it costs them.</strong> Our sample personas (made up, but built from real tickers and real closing
  prices) show the kind of thing people miss:</p>
  <ul>
    <li>Hidden concentration: in the mid-career sample, Apple is <strong>{pn['apple']['pct']}%</strong> of the portfolio
    (${pn['apple']['usd']:,}) once fund holdings are counted, while only {pn['apple']['via_pct'].get('direct')}% is held
    directly. Technology is {pn['mid_tech']}% of the whole portfolio. ({esc('Source: Facts JSON for the mid-career sample')})</li>
    <li>Fees: the retiree sample pays a weighted {pn['ret_er']}% a year; over {pn['ret_fees30']['years']} years at the
    assumed 6% return, fees cost about <strong>${pn['ret_fees30']['fees_cost_usd']:,}</strong>, of which
    ${pn['ret_fees30']['extra_vs_baseline_usd']:,} is more than index funds would charge. One active fund (GFACX,
    {pn['gfacx']['expense_ratio_pct']}%) costs ${pn['gfacx']['annual_usd']:,} a year. (Source: Facts JSON for the retiree sample)</li>
    <li>Fear: a repeat of the {esc(pn['mid_gfc']['scenario'])} would have cut the mid-career portfolio by
    {pn['mid_gfc']['max_drawdown_pct']}% (${pn['mid_gfc']['max_drawdown_usd']:,}) at the worst point. People who sold near
    the bottom locked that in.</li>
    <li>How many people this affects: {todo("public data on how many households hold several funds without an advisor, with source")}.</li>
  </ul>
  <p><strong>Why anyone should care.</strong> These are expensive, slow-to-notice mistakes made by people who are trying
  to do the right thing. A tool that explains them plainly, never sells anything and never picks specific stocks or funds
  could help without adding a new risk.</p>
</section>"""

    # --- 3 -----------------------------------------------------------------------
    comp_rows = [
        ["Morningstar Portfolio X-Ray", "Looks inside funds: asset mix, sectors, regions, stock overlap",
         "Manual ticker entry; some views tied to the paid Premium plan; no plain-English explanation layer"],
        ["Empower Personal Dashboard (formerly Personal Capital)", "Free fee analyzer and retirement planner from linked accounts",
         "Needs account linking; reviewers note wealth-management sales follow-up"],
        ["Free ETF overlap tools (e.g. ETF Research Center, others)", "Overlap between two funds",
         "Two funds at a time; no fees, crash or retirement view"],
        ["Robo-advisors", "Manage the money for you", "They take over the portfolio; not a second opinion on one you already have"],
        ["ChatGPT and similar chatbots", "Answers any question in plain English",
         "Does the math itself and can get numbers wrong; no look-through data; may answer 'should I sell?'"],
        ["<strong>Second Look</strong>", "Look-through, fees, crash replay, retirement plan and a pause check in one place, explained at three levels; then try a suggested shift with pretend money against real history before acting",
         "Every number from Python; AI output checked against the numbers; general sector, asset-type and account-type shifts, never stock picks; demo mode free"],
    ]
    s3 = f"""
<section class="cont">
  <h2>3. What is new here</h2>
  <p>None of the individual analyses is new. Look-through tools, fee calculators, crash backtests and retirement planners
  all exist. What is different is the combination and the guardrails:</p>
  {table(["Option", "What it does well", "Gap that Second Look addresses (or does not)"], comp_rows, "compare")}
  <p class="note">Competitor descriptions are summarized from public pages and reviews, which may change:
  <a href="https://www.morningstar.com/help-center/portfolio/xray">Morningstar Portfolio X-Ray help</a>,
  <a href="https://www.roboadvisorpros.com/a-review-of-empowers-free-tools/">review of Empower's free tools</a>,
  <a href="https://theglobetrottinginvestor.substack.com/p/2-free-tools-i-use-before-investing">free ETF overlap tools</a>.</p>
  <p><strong>Said honestly:</strong> professionals and paid tools go deeper than this (full holdings instead of top 10,
  tax-lot detail, live account links). Second Look's contribution is a free, explainable, guard-railed second opinion:
  the AI is used only to explain, a verifier checks every number it writes, and a safe template replaces anything that
  fails. The newest piece, <strong>Try a Shift</strong>, closes the loop from analysis to action without risk: after
  the app suggests shifts, the user can see how a pretend $1,000 would have done in conservative, moderate and risky
  mixes of asset types, replayed through real market history (2008, Covid, 2022) next to their own portfolio, with
  the AI explaining the trade-off between growth and drops. Whether that is enough for real users has not been measured: no user study was run, and running one
  is the first next step (Section 8).</p>
  <figure>{crop_image("22-midcareer-try-a-shift.png", "Try a Shift tab for the mid-career sample", 0.20, 0.66, 900, *APP_CONTENT)}<figcaption>Try a Shift: a pretend $1,000 in three mixes of asset types (index stand-ins, no stock picks), replayed through real market history in Python next to the user's portfolio, whose replay is labeled as having hindsight built in.</figcaption></figure>
</section>"""

    # --- 4 -----------------------------------------------------------------------
    res = run["result"]
    agents = [
        ["intake", "Turns messy pasted text into holdings (uploads skip AI entirely)", "None; text arrives inside &lt;user_data&gt; tags. Python keeps only rows whose ticker and numbers appear in the text", f"MODEL_FAST, {intake.SPEC.max_tokens} tokens"],
        ["analyst", "Reads the Facts JSON and picks the 3 findings most worth knowing, each tied to fact keys; Python ranks them by the dollar amount at the cited key", "None (data only)", f"MODEL_SMART, {analyst.SPEC.max_tokens} tokens"],
        ["explainer", "Writes the cards for the chosen fluency level using only numbers in the facts, plus 2-3 questions for a fiduciary advisor", "None (data only)", f"MODEL_SMART, {explainer.SPEC.max_tokens} tokens"],
        ["sandbox", "Explains the Try a Shift replay (pretend $1,000 in three asset mixes vs. the user's portfolio); must mention hindsight, may not name a security or predict", "None (data only)", f"MODEL_SMART, {sandbox_agent.SPEC.max_tokens} tokens"],
        ["shifts", "Words the Possible Shifts that Python computed (sector, asset type, account type) for the chosen fluency level; any named stock, fund or ticker is rejected", "None (data only)", f"MODEL_SMART, {shifts_agent.SPEC.max_tokens} tokens"],
        ["verifier", "Python checks every number, advice wording and plan ranges first; then a model review", "None; its Python half uses the advice guard and number matcher", f"MODEL_FAST, {verifier.SPEC.max_tokens} tokens"],
        ["pause", "Explains a proposed trade neutrally: before/after and market history next to the user's time horizon", "None (data only)", f"MODEL_SMART, {pause.SPEC.max_tokens} tokens"],
    ]
    roles = {"analyst": "Picks 3 findings and the fact keys behind them", "explainer": "Writes the cards for the fluency level",
             "verifier": "Model review after the Python checks pass", "explainer_rewrite": "Rewrites using the verifier's reasons"}
    trace_rows = [[f"{i}. <strong>{esc(t['agent'])}</strong><br><span class=\"note\">{esc(roles.get(t['agent'], ''))}</span>",
                   f"<pre class=\"trace\">{esc(step_output(t))}</pre>", f"{t['input_tokens']:,} / {t['output_tokens']:,}"]
                  for i, t in enumerate(run["trace"], 1)]
    if rw:
        rw_reasons = "".join(f"<li>{esc(x)}</li>" for x in rw["result"].verifier_reasons[:3])
        rewrite_html = (f"<h3>When the verifier says no</h3><p>In the recorded <strong>{esc(rw['label'])}</strong> run the "
                        f"steps were <strong>{' → '.join(esc(x) for x in rw['result'].steps)}</strong>. The first draft was "
                        f"sent back with these reasons; the explainer rewrote it once, and the rewrite passed the Python checks (the 4-call limit leaves no second model review):</p><ul>{rw_reasons}</ul>"
                        f"<p class=\"note\">Every rejection and its reasons are also recorded in the ledger and listed on the "
                        f"Admin page.</p>")
    else:
        rewrite_html = ""
    findings_rows = [[esc(f.title), f"<code>{esc(f.dollar_fact_key)}</code>", usd(f.dollar_impact_usd or 0, 0)] for f in res.findings]
    cards_html = "".join(f'<div class="card"><h4>{esc(c.title)}</h4><p>{esc(c.body)}</p></div>' for c in res.cards)
    s4 = f"""
<section>
  <h2>4. The agents</h2>
  <p>Python does all the finance. The agents never calculate and have no tools to call: they receive the computed
  Facts JSON and return structured JSON that Python validates. The orchestrator runs <em>analyst → explainer →
  verifier</em>; on a failure it allows one rewrite with the verifier's reasons, and after a second failure it shows a
  safe template built straight from the facts. An analysis makes at most 4 AI calls, all through <code>llm.py</code>,
  which checks the budget before each one.</p>
  <figure>{ARCH_SVG}<figcaption>Agent architecture and hand-offs. Teal boxes are AI agents; dashed boxes are plain Python.</figcaption></figure>
  {table(["Agent", "Job", "Tools", "Model and cap"], agents)}
  {no_tools_html}
  <p><strong>Which models and why.</strong> Both MODEL_FAST and MODEL_SMART are <code>gpt-6-luna</code> through Portkey,
  the course default at $0.10 / $0.50 per million input / output tokens, with <code>reasoning_effort=low</code>. The
  larger models are priced in the code (gpt-6-sol $2 / $10, gpt-6-astra $10 / $50) but were not needed: on luna,
  {quality['routings']['Normal routing']['first_draft_pass_rate'] * 100:.0f}% of first drafts passed the verifier in the
  quality run (Section 5), and astra's worst case per call exceeds the $0.05 per-call limit.</p>
</section>
<section class="cont">
  <h3>One real run, step by step</h3>
  <p>This is the recorded mid-career / Comfortable analysis, replayed from <code>demo/recorded/</code> (identical to the
  live answers, at $0). Steps: <strong>{' → '.join(esc(s) for s in res.steps)}</strong>; result:
  <strong>{esc(res.source)}</strong>.</p>
  <ol>
    <li><strong>Python builds the facts.</strong> The CSV's three accounts are merged, account numbers removed, and the
    quant engine produces a Facts JSON of about {run['facts_tokens']:,} tokens.</li>
    <li><strong>Analyst</strong> picks three findings and cites fact keys; Python ranks them by the dollars at each key:
    {table(["Finding", "Dollar fact key", "Dollar impact"], findings_rows)}</li>
    <li><strong>Explainer</strong> writes cards using only those facts (plan figures as a range with assumptions):</li>
  </ol>
  <div class="cards">{cards_html}</div>
  <ol start="4">
    <li><strong>Verifier</strong> runs the Python checks (every number matched, no advice wording, plan rule met) and then
    the model review; the draft passed, so it is shown.</li>
  </ol>
  <h3>What each agent returned in this run</h3>
  <p>Captured while replaying the recorded run; each agent answers with JSON that Python validates before the next step
  (excerpts; the full cards are shown above).</p>
  {table(["Step", "Output (JSON)", "Tokens in / out"], trace_rows, "small")}
  {rewrite_html}
  <figure>{crop_image("10-midcareer-top3.png", "Top 3 tab for the mid-career sample", 0.33, 0.66, 900, *APP_CONTENT)}<figcaption>The same run in the app (Top 3 Things Worth Knowing tab).</figcaption></figure>
  <figure>{crop_image("06-midcareer-xray.png", "What you own: top 10 companies and industry mix vs. the S&P 500", 0.14, 0.46, 900, *APP_CONTENT)}<figcaption>The X-Ray tab's "What you own" view for the same sample: top 10 companies and industry mix next to the S&P 500. Pure Python, no AI calls; the comparison numbers also go into the Facts JSON so the explainer can mention them and the verifier checks them.</figcaption></figure>
  <figure>{crop_image("21-midcareer-shifts.png", "Possible Shifts tab for the mid-career sample", 0.35, 0.81, 900, *APP_CONTENT)}<figcaption>The Possible Shifts tab: Python rules pick the shifts (sector, asset type, account type) from the numbers; the AI words them and is rejected if it names any stock, fund or ticker. The generative-AI disclaimer sits on top.</figcaption></figure>
</section>"""

    # --- 5 -----------------------------------------------------------------------
    pa, rc = cost["per_analysis"], cost["response_cache"]
    agent_rows = [[esc(a), s["n"], f"{s['mean_input_tokens']:,.0f}", f"{s['mean_cached_input_tokens']:,.0f}",
                   f"{s['mean_output_tokens']:,.0f}", usd(s["mean_usd"], 6), f"{s['p95_latency_s']:.1f}"]
                  for a, s in cost["per_agent"].items()]
    agent_rows.append(["<strong>Full analysis</strong>", pa["n"], f"{pa['mean_input_tokens']:,.0f}",
                       f"{pa['mean_cached_input_tokens']:,.0f}", f"{pa['mean_output_tokens']:,.0f}", usd(pa["mean_usd"], 6),
                       f"{pa['p95_latency_s']:.1f}"])
    m_without, m_with = cost["monthly"]["without_cache"], cost["monthly"]["with_cache"]
    monthly_rows = [[label, usd(m["per_user_usd"], 4)] + [usd(u["total_usd"]) for u in m["users"]]
                    for label, m in (("Without response cache", m_without), ("With response cache", m_with))]
    q_rows = [[esc(name), esc(r["models"]), f"{100 * r['first_draft_pass_rate']:.0f}%", r["rewrites"],
               r["template_fallbacks"], usd(r["mean_usd_per_analysis"], 6)] for name, r in quality["routings"].items()]
    effort_rows = []
    for label, r, src in (("Default effort", default_effort, "docs/cost_results_default_effort.json"),
                          ("reasoning_effort=low", low_effort, f"docs/cost_results.json at commit {LOW_EFFORT_COMMIT}")):
        if r:
            a = r["per_analysis"]
            effort_rows.append([label, usd(a["mean_usd"], 6), f"{a['p95_latency_s']:.1f}",
                                f"{r['sources'].get('template', 0)} of {r['analyses'] + r['pauses']}", f"<code>{esc(src)}</code>"])
        else:
            effort_rows.append([label, todo("result not found"), "", "", ""])
    cache_bar_max = max(pa["mean_usd"], 1e-9)
    bars = f"""
<svg viewBox="0 0 640 120" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="Cost per analysis with and without the response cache">
  <style>.t{{font:12px sans-serif;fill:#0B1F3A}}</style>
  <text class="t" x="0" y="30">Without cache</text><rect x="120" y="16" width="{460 * pa['mean_usd'] / cache_bar_max:.1f}" height="22" fill="#0B1F3A"/>
  <text class="t" x="{125 + 460 * pa['mean_usd'] / cache_bar_max:.1f}" y="32">{usd(pa['mean_usd'], 6)}</text>
  <text class="t" x="0" y="80">With cache</text><rect x="120" y="66" width="{460 * rc['mean_usd_per_analysis'] / cache_bar_max:.1f}" height="22" fill="#0E7C86"/>
  <text class="t" x="{125 + 460 * rc['mean_usd_per_analysis'] / cache_bar_max:.1f}" y="82">{usd(rc['mean_usd_per_analysis'], 6)}</text>
</svg>"""
    routings = quality["routings"]
    smart = routings.get("MODEL_SMART for every agent")
    if smart:
        normal = routings["Normal routing"]
        smart_note = (
            f"<p>Running every agent on the larger model ({esc(smart['models'])}) cost "
            f"{smart['mean_usd_per_analysis'] / normal['mean_usd_per_analysis']:.0f}x as much per analysis "
            f"({usd(smart['mean_usd_per_analysis'], 4)} vs. {usd(normal['mean_usd_per_analysis'], 4)}) and did not "
            f"improve quality on this measure: {100 * smart['first_draft_pass_rate']:.0f}% of its first drafts passed the "
            f"verifier, against {100 * normal['first_draft_pass_rate']:.0f}% for normal routing, with "
            f"{smart['template_fallbacks']} fallback(s) to the safe template. The verifier rewards drafts that use only "
            f"numbers from the facts, so this measures accuracy and rule-following, not writing style. "
            f"{normal['analyses']} analyses per routing (run <code>{esc(quality['run_id'])}</code>).</p>")
    else:
        smart_note = f'<p>{todo("MODEL_SMART-for-every-agent comparison (run scripts/measure_costs.py --smart-model gpt-6-sol)")}</p>'
    s5 = f"""
<section class="cont">
  <h2>5. Tokenomics</h2>
  <p>Measured live on {esc(cost['run_at'][:10])} (run <code>{esc(cost['run_id'])}</code>): {cost['analyses']} full analyses
  (3 personas × 3 fluency levels × 3 repeats) and {cost['pauses']} pause checks, with the app's response cache off and the
  Portkey gateway cache bypassed, so every call was a real model call. Main pass: {usd(cost['total_usd'], 4)} for
  {cost['calls']} calls. Source: <code>docs/cost_results.json</code>.</p>
  {table(["Unit", "n", "Mean input tokens", "Mean cached input", "Mean output tokens", "Mean $", "p95 latency (s)"], agent_rows)}
  <p>A typical analysis uses about {pa['mean_input_tokens']:,.0f} input tokens (of which {pa['mean_cached_input_tokens']:,.0f}
  were served from the provider's prompt cache) and {pa['mean_output_tokens']:,.0f} output tokens, and costs
  <strong>{usd(pa['mean_usd'], 4)}</strong>. A pause check costs {usd(cost['per_pause']['mean_usd'], 4)}.</p>
  <h3>Per user and at scale (2 analyses per user per month)</h3>
  {table(["", "Per user per month", "100 users", "10,000 users"], monthly_rows)}
  {bars}
  <p class="note">"With response cache" is measured: the same 27 analyses re-run with the app's cache on cost
  {usd(rc['total_usd'], 4)}, because repeats and steps shared across fluency levels come from the cache. It applies when
  people re-run a portfolio or switch fluency; a brand-new portfolio always pays the full price.</p>
  <h3>What was done to cut cost</h3>
  <ul>
    <li>All arithmetic in Python; the AI sees a compact Facts JSON (kept under 2,000 tokens) instead of raw data.</li>
    <li>The cheapest course model (gpt-6-luna) for every agent; larger models priced but unused.</li>
    <li><code>reasoning_effort=low</code>: at the default effort, hidden reasoning sometimes used the whole token allowance
    and returned nothing, forcing retries and fallbacks.</li>
    <li>A response cache, a hard cap of 4 AI calls per analysis, and budget limits per call ($0.05), per session ($0.10)
    and per day ($2.00), all enforced in <code>budget.py</code> before each call.</li>
    <li>Demo mode replays recorded answers for $0.</li>
  </ul>
  <h3>Did cost cutting hurt quality?</h3>
  {table(["Setting", "Mean $ per analysis", "p95 latency (s)", "Template fallbacks", "Source"], effort_rows)}
  <p>Lowering the reasoning effort made analyses cheaper and faster <em>and</em> more reliable (fewer safe-template
  fallbacks), so in this case cost cutting did not hurt quality. From <code>docs/quality_vs_cost.json</code>:</p>
  {table(["Routing", "Models", "First-draft verifier pass rate", "Rewrites", "Template fallbacks", "Mean $ per analysis"], q_rows)}
  {smart_note}
  <p class="note">The latest run cost more per analysis than the {LOW_EFFORT_COMMIT} run because a stricter verifier rule
  (plan figures must be a labeled range) now triggers more rewrites: {cost['sources']}.</p>
</section>"""

    # --- 6 -----------------------------------------------------------------------
    def by_suite(summary: dict) -> dict[str, dict]:
        """Red-team results summed per suite, with the surfaces each covers."""
        out: dict[str, dict] = {}
        for key, v in summary["by_suite"].items():
            suite, _, surface = key.partition(" / ")
            row = out.setdefault(suite, {"passed": 0, "total": 0, "surfaces": []})
            row["passed"] += v["passed"]
            row["total"] += v["total"]
            row["surfaces"].append(surface)
        return out

    live_suites = by_suite(live_redteam["summary"]) if live_redteam else {}
    suite_rows = [[f"{esc(k)}<br><span class=\"note\">{esc(', '.join(v['surfaces']))}</span>", f"{v['passed']}/{v['total']}",
                   f"{live_suites[k]['passed']}/{live_suites[k]['total']}" if k in live_suites else "not run live yet"]
                  for k, v in by_suite(redteam["summary"]).items()]
    o = redteam["summary"]["overall"]
    picks = []
    for cid, surface in (("reveal-02", "Pasted-text intake"), ("injection", "CSV upload"),
                         ("transposed-dollars", "verifier"), ("names-company", "Possible Shifts")):
        c = next((x for x in redteam["cases"] if x["id"] == cid and x["surface"] == surface), None)
        if c:
            picks.append([f"<code>{esc(c['id'])}</code>", esc(c["surface"]), esc(str(c.get("input", ""))[:90]), esc(c["outcome"][:120]),
                          "pass" if c["passed"] else "FAIL"])
    live_line = (f"Live run against the real model ({esc(live_redteam['run_at'][:10])}): "
                 f"<strong>{live_redteam['summary']['overall']['passed']}/{live_redteam['summary']['overall']['total']}</strong>."
                 if live_redteam else todo("live red-team result"))
    s6 = f"""
<section class="cont">
  <h2>6. Safety</h2>
  <div class="policy"><strong>Safety policy.</strong> Second Look explains numbers and, in its Possible Shifts tab, suggests
  general shifts between industries, asset types (stocks, bonds, cash, low-cost index funds) and account types. Those
  suggestions come from fixed Python rules applied to the user's numbers, and the AI only words them. It never
  recommends a specific stock, fund or other security, never sets price targets, never predicts markets and never
  promises returns; the Top 3 explanations and the Pause Button stay neutral. Every page carries a disclaimer that this
  is generative AI, not a professional financial advisor. Every number shown is computed in Python; the AI may only explain numbers it
  is given, and a verifier rejects any draft with a number that is not in the facts, advice-like wording, or a
  retirement figure that is not a labeled range. Uploaded files are processed in memory, account numbers and free text
  are removed before anything reaches the AI, and the API key lives only in <code>.env</code>, which git never tracks.
  When anything fails, users see a safe summary built straight from the numbers.</div>
  <h3>Threats and defenses</h3>
  {table(["Threat", "Defense"], [
      ["Prompt injection in pasted text or CSV fields", "Pause Button text is parsed by Python and never sent to the AI; pasted text goes inside &lt;user_data&gt; tags with closing tags neutralised; CSV descriptions and notes are dropped; intake rows are kept only if their ticker and numbers appear in the text"],
      ["Data leaks (key, account numbers, system prompts)", "Only config.py reads settings; logs and errors are redacted; account numbers become 'Account 1'; a secret scanner checks every tracked file and the rendered app (tests/test_secrets.py); gitleaks runs on every commit"],
      ["Invented facts", "The verifier matches every number to the Facts JSON within the writer's rounding; the analyst's ranking uses Python-looked-up dollars, not numbers the model wrote"],
      ["Wrong actions", "Agents have no tools and cannot trade or send anything; advice_guard flags buy/sell/hold, account moves, price targets and promises in the neutral sections, and in Possible Shifts rejects any named stock, fund or ticker"],
      ["Loops", "At most 4 AI calls per analysis and one retry per call; then a safe template"],
      ["Overspending", "budget.py checks per-call ($0.05), per-session ($0.10) and daily ($2.00) limits before every call; scripts add --max-usd; every call is recorded in the ledger and shown on the Admin page"],
  ])}
  <h3>Defenses working: red-team results</h3>
  <p>Demo mode (saved answers and a simulated attacker that obeys every attack, to test the Python defenses on
  their own; run {esc(redteam['run_at'][:10])}): <strong>{o['passed']}/{o['total']}</strong>. {live_line}
  Sources: <code>docs/redteam_results.json</code> and <code>{esc(live_source)}</code>.</p>
  {table(["Suite / surface", "Demo mode", "Live model"], suite_rows)}
  {table(["Case", "Surface", "Input", "What happened", "Result"], picks, "small")}
  <figure>{crop_image("18-midcareer-pause-sell-all.png", "Pause Button result for sell everything", 0.54, 0.89, 900, *APP_CONTENT)}<figcaption>A defense working: asked to "sell everything", the Pause Button shows consequences and history with no verdict on the trade.</figcaption></figure>
</section>"""

    # --- 7 -----------------------------------------------------------------------
    m_wo, m_w = cost["monthly"]["without_cache"], cost["monthly"]["with_cache"]
    est = '<span class="est">estimate</span>'
    cost_rows = [
        ["Cost of one full analysis", usd(pa["mean_usd"], 4), "Measured", "docs/cost_results.json"],
        ["Cost of one analysis with the response cache", usd(cost["response_cache"]["mean_usd_per_analysis"], 4), "Measured",
         "docs/cost_results.json"],
    ] + [[f"Monthly cost, {u['users']:,} users", f"{usd(u['total_usd'])} ({usd(uc['total_usd'])} with cache)",
          f"{est}: assumes 2 analyses per user per month", "docs/cost_results.json"]
         for u, uc in zip(m_wo["users"], m_w["users"])]
    persona_rows = []
    for name, label in (("beginner", "Starting out"), ("midcareer", "Mid-career"), ("retiree", "Retired")):
        f = persona_facts(name)
        fees, top = f["fees"], f["look_through"]["top_companies"][0]
        p30 = fees["projection"][-1]
        direct = top.get("via_pct", {}).get("direct")
        persona_rows.append([
            label, usd(f["portfolio"]["total_usd"], 0), f"{fees['weighted_expense_ratio_pct']}%", usd(fees["annual_fees_usd"], 0),
            f"{usd(p30['fees_cost_usd'], 0)} ({est})",
            f"{esc(top['name'])}: {top['pct']}%" + (f" ({direct}% held directly)" if direct is not None else " (not broken down)"),
        ])
    s7 = f"""
<section class="cont">
  <h2>7. Impact</h2>
  <p>Every number below is either measured or computed by the app's Python engine from the sample portfolios. Anything
  that depends on an assumption is marked <span class="est">estimate</span>. The sample people are made up; their
  holdings use real tickers and real closing prices.</p>
  <h3>Cost to run</h3>
  {table(["", "Number", "Type", "Source"], cost_rows)}
  <h3>What the app surfaces in the sample portfolios</h3>
  {table(["Sample", "Portfolio", "Weighted fee", "Fees this year", "Fees over 30 years", "Largest company, counting funds"], persona_rows)}
  <p class="note">Fees over 30 years assume 6% growth a year before fees and no new deposits. Source: the Facts JSON the
  app computes for each sample (<code>secondlook/demo_runs.py</code>).</p>
  <h3>The fee figures from Section 2</h3>
  <ul>
    <li>The retiree sample pays {pn['ret_er']}% a year in fund fees; over {pn['ret_fees30']['years']} years that is about
    ${pn['ret_fees30']['fees_cost_usd']:,} ({est}), of which ${pn['ret_fees30']['extra_vs_baseline_usd']:,} is more than
    index funds would charge ({est}).</li>
    <li>Its costliest fund, GFACX ({pn['gfacx']['expense_ratio_pct']}%), costs ${pn['gfacx']['annual_usd']:,} this year.</li>
    <li>In the mid-career sample, Apple is {pn['apple']['pct']}% of the portfolio counting funds, against
    {pn['apple']['via_pct'].get('direct')}% held directly.</li>
  </ul>
  <p><strong>A user study was not run</strong>, so there are no measured numbers on time saved, understanding or trust.
  Running one is the first item in the next steps (Section 8).</p>
</section>"""

    # --- 8 -----------------------------------------------------------------------
    s8 = f"""
<section class="cont">
  <h2>8. Limits and next steps</h2>
  <h3>What does not work yet</h3>
  <ul>
    <li>Fund look-through uses only each fund's top ~10 holdings, so company exposures are minimums.</li>
    <li>Prices come from Yahoo Finance (unofficial) with an offline snapshot through 2026-10-16; sample portfolios always use the snapshot.</li>
    <li>The retirement plan is simple: constant real returns, a flat 15% tax on pre-tax accounts and no other taxes.</li>
    <li>The glide-path reference and the risk-profile ranges are placeholders until a cited source is added.</li>
    <li>The stricter plan-range rule makes the model verifier send more first drafts back for a rewrite. Its reasons are now
    recorded on the Admin page, but the rule has not been tuned yet.</li>
    <li>Demo mode has saved answers only for the three samples with their default answers; uploads get the standard summary.</li>
    <li>No user study has been run; the app is not deployed online; the macOS setup is untested.</li>
  </ul>
  <h3>With another month</h3>
  <ul>
    <li><strong>Run a user study (5-6 people, timed against a spreadsheet, with a comprehension check).</strong></li>
    <li>Full fund holdings from fund filings instead of top 10.</li>
    <li>Tune the verifier using its recorded reasons to cut rewrites.</li>
    <li>Deploy online with proper secret handling.</li>
    <li>Monte Carlo ranges instead of three fixed scenarios; basic tax awareness.</li>
    <li>Parking-lot ideas: {todo("add the parking-lot list (none is recorded in the repository yet)")}</li>
  </ul>
</section>"""

    # --- appendix --------------------------------------------------------------------
    phases: "OrderedDict[str, dict]" = OrderedDict()
    for e in log:
        p = phases.setdefault(e["phase"], {"count": 0, "results": []})
        p["count"] += 1
        p["results"] += e["results"]
    rows = [["Prompts 0-1 (setup)", "2", "Workspace, .gitignore and guardrails (before the log existed; see the hand-entry section)"]]
    for phase, p in phases.items():
        # first clause of each Result line, without bracketed details (the full text stays in AI_Prompts.md)
        clauses = [re.sub(r"\s*\([^)]*\)", "", r.split(";")[0]).rstrip(".") + "." for r in p["results"][:3]]
        produced = " ".join(clauses) or "No Result line under this entry; see AI_Prompts.md."
        rows.append([esc(phase), p["count"], esc(produced[:400])])
    log_link = f'<a href="{esc(REPO_URL)}/blob/main/AI_Prompts.md">AI_Prompts.md</a>' if REPO_URL else '<code>AI_Prompts.md</code> in the repository root'
    appendix = f"""
<section class="cont">
  <h2>Appendix: AI Prompt Log</h2>
  <p>Every prompt used to build Second Look is logged word for word in {log_link}, with a Result line after each task.
  {len(log)} entries are logged there (prompts 0-1 predate the log). Summary by phase:</p>
  {table(["Phase", "Prompts", "What it produced"], rows, "small")}
</section>"""

    css = """
:root{--navy:#0B1F3A;--teal:#0E7C86;--slate:#475569;--mist:#F3F6FA}
*{box-sizing:border-box}body{font:15px/1.55 "Segoe UI",Helvetica,Arial,sans-serif;color:#1E293B;margin:0;background:#fff}
main{max-width:900px;margin:0 auto;padding:32px 24px}
h1{font-size:44px;color:var(--navy);margin:8px 0}h2{color:var(--navy);font-size:26px;border-bottom:3px solid var(--teal);padding-bottom:6px;margin-top:0}
h3{color:var(--navy);font-size:18px;margin:22px 0 8px}h4{margin:0 0 6px;color:var(--navy)}
.kicker{color:var(--teal);font-weight:600;letter-spacing:.04em;text-transform:uppercase;font-size:13px;margin:0}
.lede{font-size:19px;color:#334155}.note{font-size:13px;color:var(--slate)}
pre.trace{white-space:pre-wrap;font-size:11px;margin:0;background:var(--mist);padding:6px;border-radius:4px;max-height:none}
.est{background:#E6F3F4;border:1px solid #9CCFD3;border-radius:4px;padding:0 5px;font-size:12px;color:#0E5F68}
.todo{background:#FFF4D6;border:1px solid #E5B94B;border-radius:4px;padding:1px 6px;font-size:13px;color:#7A5300}
table{width:100%;border-collapse:collapse;margin:10px 0 14px;font-size:13.5px}th,td{border:1px solid #DCE3EC;padding:6px 8px;text-align:left;vertical-align:top}
th{background:var(--mist);color:var(--navy)}table.small{font-size:12px}table.compare td:first-child{font-weight:600;width:22%}
img{max-width:100%;border:1px solid #DCE3EC;border-radius:6px}figure{margin:14px 0}figcaption{font-size:12.5px;color:var(--slate);margin-top:4px}
svg{max-width:100%;height:auto}.cards{display:grid;gap:10px;margin:10px 0}.card{border:1px solid #DCE3EC;border-radius:8px;padding:10px 14px}
.card p{margin:0;font-size:14px}.policy{background:var(--mist);border-left:4px solid var(--teal);padding:12px 16px;border-radius:6px}
code{background:var(--mist);padding:1px 4px;border-radius:3px;font-size:12.5px}section{margin-bottom:40px}
.video video{background:#0B1F3A;max-width:100%;height:200px;border-radius:6px}
@media print{@page{size:Letter;margin:0.6in}body{font-size:10pt}table{font-size:9pt}main{padding:0;max-width:none}
section{page-break-before:always;margin:0}section.cover{page-break-before:avoid}section.cont{page-break-before:auto;margin-top:18pt}
img{max-height:2.3in;width:auto;display:block;margin:0 auto}h2,h3{page-break-after:avoid}
figure,.card,.policy,tr{page-break-inside:avoid}a{color:inherit}.video video{display:none}}
"""
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Second Look: Final Project Report</title><style>{css}</style></head>
<body><main>
{s1}{s2}{s3}{s4}{s5}{s6}{s7}{s8}{appendix}
</main></body></html>
"""


def main() -> int:
    OUT.write_text(build(), encoding="utf-8")
    print(f"Wrote {OUT.relative_to(ROOT)} ({OUT.stat().st_size / 1e6:.2f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
