"""Build docs/slides.html: a 9-slide, 16:9, self-contained deck in the app's black and gold theme.

Numbers are read at build time from docs/cost_results.json, docs/redteam_results.json,
and the sample personas' Facts JSON. No user study was run, so no user numbers appear. Arrow keys, space, Page Up/Down, Home/End and clicks move between slides.

Usage:
    python scripts/build_slides.py
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("build_report", ROOT / "scripts" / "build_report.py")
br = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(br)

OUT = br.DOCS / "slides.html"
# Red-team suites (summed across surfaces) and their short slide labels.
SUITE_LABELS = {
    "adversarial prompts": "Attack prompts",
    "malicious CSVs": "Malicious CSVs",
    "wrong-number drafts": "Wrong numbers",
    "profile cases": "Bad profiles",
    "bad drafts": "Bad drafts",
    "real model output": "Real AI output",
    "data isolation": "Data isolation",
}


def suite_totals(by_suite: dict) -> dict[str, dict]:
    """Red-team results summed per suite ("suite / surface" keys -> "suite")."""
    totals: dict[str, dict] = {}
    for key, v in by_suite.items():
        row = totals.setdefault(key.split(" / ")[0], {"passed": 0, "total": 0})
        row["passed"] += v["passed"]
        row["total"] += v["total"]
    return totals


def slide(n: int, title: str, body: str, cls: str = "") -> str:
    head = f"<h2>{br.esc(title)}</h2>" if title else ""
    return f'<section class="slide {cls}" id="s{n}" data-n="{n}">{head}{body}</section>'


def stat(value: str, label: str) -> str:
    return f'<div class="stat"><div class="v">{value}</div><div class="l">{label}</div></div>'


def todo(text: str) -> str:
    return f'<span class="todo">TODO: {br.esc(text)}</span>'


MASCOT_SVG = (ROOT / "assets" / "labubu.svg").read_text(encoding="utf-8")  # the app's green Labubu

# How the agents work, drawn big enough to read on a projector: gold boxes are AI, dashed boxes are Python.
AGENTS_SVG = """
<svg viewBox="0 0 1168 470" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="How Second Look's agents work">
 <defs><marker id="ga" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" orient="auto">
  <path d="M0,0 L10,5 L0,10 z" fill="#D4AF37"/></marker></defs>
 <style>.py{fill:#161616;stroke:#B8B0A0;stroke-width:2;stroke-dasharray:7 5}.ai{fill:#2A2314;stroke:#D4AF37;stroke-width:2.5}
 .you{fill:#D4AF37}.h{font:700 25px 'Segoe UI',sans-serif;fill:#F5F1E6}.d{font:19px 'Segoe UI',sans-serif;fill:#B8B0A0}
 .hk{font:700 25px 'Segoe UI',sans-serif;fill:#0A0A0A}.dk{font:19px 'Segoe UI',sans-serif;fill:#2A2314}
 .tag{font:700 15px 'Segoe UI',sans-serif;letter-spacing:.12em}.ar{stroke:#D4AF37;stroke-width:3;fill:none;marker-end:url(#ga)}</style>
 <text class="tag" x="0" y="22" fill="#B8B0A0">PYTHON DOES THE MATH</text>
 <rect class="py" x="0" y="38" width="350" height="110" rx="14"/>
 <text class="h" x="175" y="84" text-anchor="middle">Your portfolio</text><text class="d" x="175" y="114" text-anchor="middle">CSV upload or a sample</text>
 <path class="ar" d="M352,93 H404"/>
 <rect class="py" x="408" y="38" width="350" height="110" rx="14"/>
 <text class="h" x="583" y="84" text-anchor="middle">Quant engine</text><text class="d" x="583" y="114" text-anchor="middle">overlap, fees, crash replay, plan</text>
 <path class="ar" d="M760,93 H812"/>
 <rect class="py" x="816" y="38" width="350" height="110" rx="14"/>
 <text class="h" x="991" y="84" text-anchor="middle">Facts JSON</text><text class="d" x="991" y="114" text-anchor="middle">every number, under 2,000 tokens</text>
 <path class="ar" d="M991,150 V172 H131 V204"/>
 <text class="tag" x="150" y="196" fill="#D4AF37">AI EXPLAINS (NO TOOLS)</text>
 <rect class="ai" x="0" y="208" width="262" height="120" rx="14"/>
 <text class="h" x="131" y="256" text-anchor="middle">1 Analyst</text><text class="d" x="131" y="288" text-anchor="middle">picks the top 3 facts</text>
 <path class="ar" d="M264,268 H296"/>
 <rect class="ai" x="300" y="208" width="262" height="120" rx="14"/>
 <text class="h" x="431" y="256" text-anchor="middle">2 Explainer</text><text class="d" x="431" y="288" text-anchor="middle">plain English, your level</text>
 <path class="ar" d="M564,268 H596"/>
 <rect class="ai" x="600" y="208" width="262" height="120" rx="14"/>
 <text class="h" x="731" y="256" text-anchor="middle">3 Verifier</text><text class="d" x="731" y="288" text-anchor="middle">every number, no stock picks</text>
 <path class="ar" d="M864,268 H900"/>
 <rect class="you" x="904" y="208" width="262" height="120" rx="14"/>
 <text class="hk" x="1035" y="256" text-anchor="middle">You see it</text><text class="dk" x="1035" y="288" text-anchor="middle">only checked text</text>
 <path class="ar" d="M731,330 V362 H431 V334"/>
 <text class="d" x="750" y="370">fails: one rewrite, then a safe template</text>
 <rect x="0" y="406" width="1166" height="56" rx="12" fill="#161616" stroke="#2E2A1E" stroke-width="2"/>
 <text class="d" x="583" y="441" text-anchor="middle">Every call goes through budget.py: per-call, $0.10 per session and $2 per day limits</text>
</svg>"""


def slides() -> list[str]:
    cost = br.load_json(br.DOCS / "cost_results.json")
    red = br.load_json(br.DOCS / "redteam_results.json")
    live_red = br.git_json(br.LIVE_REDTEAM_COMMIT, "docs/redteam_results.json")
    pn = br.persona_numbers()
    pa = cost["per_analysis"]
    m_without, m_with = cost["monthly"]["without_cache"], cost["monthly"]["with_cache"]
    big = m_without["users"][-1]

    s = []
    s.append(slide(1, "", f"""
<div class="mascot">{MASCOT_SVG}</div>
<p class="kicker">AI Foundations final project</p><h1>Second Look</h1>
<p class="pitch">An AI second opinion on your portfolio: numbers computed in Python, explained plainly, never stock picks.</p>
<p class="by">{br.esc(br.AUTHOR)} &middot; <a href="Demo%20Video%20recording.mp4">Demo Video recording</a></p>""", "title"))

    s.append(slide(2, "The problem", """
<p class="lead">Most households own funds. Few get help looking inside them.</p>
<div class="stats">{0}
{1}</div>
<p class="src">Sources: ICI Fact Book 2024; Northwestern Mutual.</p>""".format(
        stat("56%", "of US households own funds"), stat("37%", "work with a financial advisor")), "hero"))

    s.append(slide(3, "What's new", """
<div class="cards"><div><b>One place</b>Overlap, fees, crashes, retirement</div>
<div><b>Checked AI</b>A verifier checks every number</div>
<div><b>Shifts, not picks</b>Sector and asset ideas, never stocks</div>
<div><b>Try first</b>Test a shift with pretend $1,000</div></div>"""))

    s.append(slide(4, "How the agents work", f"""
<div class="diagram">{AGENTS_SVG}</div>
<p class="cap">Python computes. Agents explain. The verifier checks. At most 4 AI calls.</p>""", "wide"))

    # (file, caption, top, bottom): the slice of each screenshot that shows the feature
    shots = [("06-midcareer-xray.png", "What you own vs. S&P 500", 0.145, 0.34), ("10-midcareer-top3.png", "Top 3 findings", 0.33, 0.66),
             ("22-midcareer-try-a-shift.png", "Try a Shift (pretend $1,000)", 0.20, 0.47)]
    figs = "".join(f"<figure>{br.crop_image(f, c, top, bottom, 700, *br.APP_CONTENT)}<figcaption>{c}</figcaption></figure>"
                   for f, c, top, bottom in shots)
    s.append(slide(5, "Demo", f'<div class="shots">{figs}</div>', "wide"))

    s.append(slide(6, "Cost to run", f"""
<div class="stats">{stat(br.usd(cost.get('per_visit_usd', pa['mean_usd']), 4), "per full visit, every AI tab")}
{stat(br.usd(big['total_usd']), f"per month, {big['users']:,} users")}
{stat(br.usd(m_with['users'][-1]['total_usd']), "same, with response cache")}</div>
<p class="src">Measured live, {cost['analyses']} analyses. Source: cost_results.json</p>"""))

    o = red["summary"]["overall"]
    bars = "".join(
        f'<div class="bar"><span>{br.esc(SUITE_LABELS.get(k, k))}</span><div class="track"><div class="fill" style="width:{100 * v["passed"] / v["total"]:.0f}%"></div></div>'
        f'<b>{v["passed"]}/{v["total"]}</b></div>' for k, v in suite_totals(red["summary"]["by_suite"]).items())
    live = (f'{live_red["summary"]["overall"]["passed"]}/{live_red["summary"]["overall"]["total"]} on the live model'
            if live_red else todo("live red-team"))
    s.append(slide(7, "Safety", f"""
<div class="split"><div>{stat(f"{o['passed']}/{o['total']}", "red-team cases passed")}<p class="src">{live}</p></div>
<div class="bars">{bars}</div></div>"""))

    est = '<span class="est">estimate</span>'
    gfc = pn["mid_gfc"]
    impact = f"""
<div class="stats">{stat(f"{pn['apple']['pct']}%", f"Apple via funds ({pn['apple']['via_pct'].get('direct')}% direct)")}
{stat(f"-{gfc['max_drawdown_pct']:.0f}%", "Mid-career: 2008 crash replay")}
{stat(f"${pn['ret_fees30']['fees_cost_usd']:,}", f"Retiree fees, 30 years {est}")}</div>
<p class="sum">{br.usd(pa['mean_usd'], 4)} per analysis, measured &middot; {br.usd(big['total_usd'])} a month, {big['users']:,} users {est}</p>
<p class="src">Sample personas. No user study yet.</p>"""
    s.append(slide(8, "Impact", impact))

    s.append(slide(9, "What's next", f"""
<ul class="big"><li>Run a user study</li><li>Full fund holdings, not top 10</li>
<li>Tune the verifier to cut rewrites</li><li>Deploy online</li></ul>
<p class="close">Take a second look before you trust a portfolio. <a href="{br.REPO_URL}">{br.REPO_URL.removeprefix("https://")}</a></p>"""))
    return s


CSS = """
:root{--bg:#0A0A0A;--surface:#161616;--line:#2E2A1E;--gold:#D4AF37;--gold2:#F1D67A;--ink:#F5F1E6;--slate:#B8B0A0}
*{box-sizing:border-box}html,body{margin:0;height:100%;background:#000;font-family:"Segoe UI",Helvetica,Arial,sans-serif;color:var(--ink)}
#stage{position:absolute;left:50%;top:50%;width:1280px;height:720px;transform-origin:center;transform:translate(-50%,-50%)}
.slide{position:absolute;inset:0;background:var(--bg);border:1px solid var(--line);border-radius:14px;padding:60px 80px;display:none;flex-direction:column;overflow:hidden}
.slide.on{display:flex}.slide::after{content:attr(data-n) " / 9";position:absolute;right:28px;bottom:20px;font-size:16px;color:var(--slate)}
.slide::before{content:"";position:absolute;left:0;top:0;right:0;height:6px;background:linear-gradient(90deg,var(--gold),var(--gold2),var(--gold))}
a{color:var(--gold2)}
h1{font-size:120px;margin:8px 0 18px;letter-spacing:-.02em}h1 em{font-style:normal;color:var(--gold)}
h2{font-size:52px;margin:0 0 34px;border-bottom:4px solid var(--gold);padding-bottom:10px;align-self:flex-start}
.title{justify-content:center;background:radial-gradient(circle at 80% 35%,#2A2314 0,var(--bg) 55%)}
.mascot{position:absolute;right:90px;top:150px;width:280px}.mascot svg{width:100%;height:auto}
.kicker{color:var(--gold);font-size:24px;text-transform:uppercase;letter-spacing:.12em;margin:0}.pitch{font-size:34px;max-width:780px;line-height:1.3;margin:0}
.by{font-size:26px;margin-top:44px;color:var(--slate)}.lead{font-size:38px;line-height:1.3;max-width:1050px;margin:0 0 34px}
.stats{display:flex;gap:28px;margin:10px 0}.stat{flex:1;background:var(--surface);border:1px solid var(--line);border-top:3px solid var(--gold);border-radius:14px;padding:26px 30px}
.stat .v{font-size:68px;font-weight:700;color:var(--gold)}.stat .l{font-size:24px;margin-top:6px;color:var(--ink)}
.hero .lead{font-size:44px}.hero .stat{padding:34px 40px}.hero .stat .v{font-size:130px;line-height:1.05}.hero .stat .l{font-size:32px}
.src{font-size:18px;color:var(--slate);margin-top:auto}.sum{font-size:28px;margin:26px 0 0}
ul.big{font-size:40px;line-height:1.55;padding-left:40px;margin:0}ul.big li::marker{color:var(--gold)}
.close{margin-top:auto;font-size:30px;color:var(--gold2);border-top:1px solid var(--line);padding-top:20px}.close a{margin-left:12px;font-size:22px;color:var(--slate)}
.cards{display:grid;grid-template-columns:1fr 1fr;grid-auto-rows:1fr;gap:28px;flex:1;margin-bottom:24px}.cards div{background:var(--surface);border:1px solid var(--line);border-left:5px solid var(--gold);border-radius:14px;padding:30px 36px;font-size:32px;line-height:1.35;color:var(--slate);display:flex;flex-direction:column;justify-content:center}
.cards b{display:block;font-size:44px;color:var(--ink);margin-bottom:8px}
.wide{padding:44px 56px}.wide h2{margin-bottom:18px}.diagram svg{width:100%;height:470px}.cap{font-size:26px;margin:10px 0 0;text-align:center;color:var(--gold2)}
.shots{display:flex;gap:22px;flex:1;min-height:0}.shots figure{flex:1;margin:0;display:flex;flex-direction:column;min-height:0}
.shots img{width:100%;flex:1;min-height:0;object-fit:cover;object-position:left top;background:#000;border:1px solid var(--gold);border-radius:10px}
.shots figcaption{font-size:24px;text-align:center;margin-top:12px}
.split{display:flex;gap:44px;align-items:flex-start}.split>div:first-child{flex:0 0 330px}.bars{flex:1;display:flex;flex-direction:column;gap:18px}
.bar{display:grid;grid-template-columns:300px 1fr 70px;align-items:center;gap:14px;font-size:24px}.track{height:22px;background:var(--surface);border:1px solid var(--line);border-radius:11px;overflow:hidden}
.fill{height:100%;background:linear-gradient(90deg,var(--gold),var(--gold2))}.est{font-size:15px;background:#2A2314;color:var(--gold2);border-radius:5px;padding:1px 7px;vertical-align:middle}
.todo{background:#FFF4D6;border:1px solid #E5B94B;border-radius:6px;padding:2px 10px;color:#7A5300}
#nav{position:fixed;bottom:10px;left:50%;transform:translateX(-50%);display:flex;gap:8px;z-index:2}
#nav button{background:#161616;color:var(--gold);border:1px solid var(--line);border-radius:8px;padding:6px 14px;font-size:16px;cursor:pointer}
@media print{@page{size:1280px 720px;margin:0}html,body{background:var(--bg);-webkit-print-color-adjust:exact;print-color-adjust:exact}#stage{position:static;transform:none!important}
.slide{position:relative;display:flex!important;width:1280px;height:720px;border-radius:0;page-break-after:always}#nav{display:none}}
"""

JS = """
const slides=[...document.querySelectorAll('.slide')];let i=0;
function show(n){i=Math.max(0,Math.min(slides.length-1,n));slides.forEach((s,k)=>s.classList.toggle('on',k===i));try{history.replaceState(null,'','#'+(i+1));}catch(e){}}
function fit(){const s=Math.min(innerWidth/1280,(innerHeight-50)/720)*0.98;document.getElementById('stage').style.transform='translate(-50%,-50%) scale('+s+')';}
addEventListener('keydown',e=>{if(['ArrowRight','ArrowDown','PageDown',' ','Enter'].includes(e.key)){show(i+1);e.preventDefault();}
 else if(['ArrowLeft','ArrowUp','PageUp','Backspace'].includes(e.key)){show(i-1);e.preventDefault();}
 else if(e.key==='Home')show(0);else if(e.key==='End')show(slides.length-1);});
document.getElementById('stage').addEventListener('click',e=>{const r=e.currentTarget.getBoundingClientRect();show(e.clientX<r.left+r.width/3?i-1:i+1);});
document.getElementById('prev').onclick=()=>show(i-1);document.getElementById('next').onclick=()=>show(i+1);
addEventListener('resize',fit);fit();show((parseInt(location.hash.slice(1))||1)-1);
"""


def build() -> str:
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Second Look: Slides</title><style>{CSS}</style></head>
<body><div id="stage">{''.join(slides())}</div>
<div id="nav"><button id="prev" aria-label="Previous slide">&larr;</button><button id="next" aria-label="Next slide">&rarr;</button></div>
<script>{JS}</script></body></html>
"""


def main() -> int:
    OUT.write_text(build(), encoding="utf-8")
    print(f"Wrote {OUT.relative_to(br.ROOT)} ({OUT.stat().st_size / 1e6:.2f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
