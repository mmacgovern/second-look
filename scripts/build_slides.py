"""Build docs/slides.html: a 9-slide, 16:9, self-contained deck in the app's theme.

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
<p class="kicker">AI Foundations final project</p><h1>Second Look</h1>
<p class="pitch">An AI second opinion on your portfolio: numbers computed in Python, explained plainly, never stock picks.</p>
<p class="by">{br.esc(br.AUTHOR)}</p>""", "title"))

    s.append(slide(2, "The problem", f"""
<p class="lead">Investors with several funds can't see the overlap, fees or crash risk inside them.</p>
<div class="stats">{stat(f"{pn['apple']['pct']}%", f"Apple, counting funds ({pn['apple']['via_pct'].get('direct')}% direct)")}
{stat(f"${pn['ret_fees30']['fees_cost_usd']:,}", f"retiree fees over {pn['ret_fees30']['years']} years")}</div>
<p class="src">Made-up sample personas.</p>"""))

    s.append(slide(3, "What's new", """
<ul class="big"><li>Overlap, fees, crash replay and retirement plan in one place</li>
<li>AI only explains; a verifier checks every number</li>
<li>Suggests sector and asset shifts, never specific stocks</li>
<li>Try a shift with pretend $1,000 first</li></ul>"""))

    s.append(slide(4, "How the agents work", f"""
<div class="diagram">{br.ARCH_SVG}</div>
<p class="cap">Python computes. Agents explain. Verifier checks. At most 4 AI calls.</p>""", "wide"))

    # (file, caption, top, bottom): the slice of each screenshot that shows the feature
    shots = [("06-midcareer-xray.png", "What you own vs. S&P 500", 0.145, 0.34), ("10-midcareer-top3.png", "Top 3 findings", 0.33, 0.66),
             ("22-midcareer-try-a-shift.png", "Try a Shift (pretend $1,000)", 0.20, 0.47)]
    figs = "".join(f"<figure>{br.crop_image(f, c, top, bottom, 700, *br.APP_CONTENT)}<figcaption>{c}</figcaption></figure>"
                   for f, c, top, bottom in shots)
    s.append(slide(5, "Demo", f'<div class="shots">{figs}</div>', "wide"))

    s.append(slide(6, "Cost to run", f"""
<div class="stats">{stat(br.usd(pa['mean_usd'], 4), "per analysis")}
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
    impact = f"""
<div class="stats">{stat(br.usd(pa['mean_usd'], 4), "per analysis, measured")}
{stat(br.usd(big['total_usd']), f"a month, {big['users']:,} users {est}")}</div>
<div class="stats">{stat(f"${pn['ret_fees30']['fees_cost_usd']:,}", f"retiree fees, 30 years {est}")}
{stat(f"{pn['apple']['pct']}%", f"Apple via funds ({pn['apple']['via_pct'].get('direct')}% direct)")}</div>
<p class="src">Sample personas. No user study yet: first next step.</p>"""
    s.append(slide(8, "Impact: the numbers", impact))

    s.append(slide(9, "What's next", """
<ul class="big"><li>Run a user study</li><li>Full fund holdings, not top 10</li>
<li>Tune the verifier to cut rewrites</li><li>Deploy online</li></ul>"""))
    return s


CSS = """
:root{--navy:#0B1F3A;--teal:#0E7C86;--mist:#F3F6FA;--line:#DCE3EC;--slate:#475569}
*{box-sizing:border-box}html,body{margin:0;height:100%;background:#0B1F3A;font-family:"Segoe UI",Helvetica,Arial,sans-serif;color:var(--navy)}
#stage{position:absolute;left:50%;top:50%;width:1280px;height:720px;transform-origin:center;transform:translate(-50%,-50%)}
.slide{position:absolute;inset:0;background:#fff;border-radius:14px;padding:64px 80px;display:none;flex-direction:column;overflow:hidden}
.slide.on{display:flex}.slide::after{content:attr(data-n) " / 9";position:absolute;right:28px;bottom:20px;font-size:16px;color:var(--slate)}
.slide::before{content:"";position:absolute;left:0;top:0;bottom:0;width:10px;background:var(--teal)}
h1{font-size:110px;margin:8px 0 18px;letter-spacing:-.02em}h2{font-size:52px;margin:0 0 34px;border-bottom:4px solid var(--teal);padding-bottom:10px;align-self:flex-start}
.title{justify-content:center;background:var(--navy);color:#F3F6FA}.title::before{width:18px}.title::after{color:#94A3B8}
.kicker{color:#5EC4CC;font-size:24px;text-transform:uppercase;letter-spacing:.08em;margin:0}.pitch{font-size:34px;max-width:960px;line-height:1.3;margin:0}
.by{font-size:28px;margin-top:40px;color:#CBD5E1}.lead{font-size:34px;line-height:1.35;max-width:1050px;margin:0 0 30px}
.stats{display:flex;gap:28px;margin:10px 0}.stat{flex:1;background:var(--mist);border:1px solid var(--line);border-radius:14px;padding:26px 30px}
.stat .v{font-size:64px;font-weight:700;color:var(--teal)}.stat .l{font-size:24px;margin-top:6px}
.src{font-size:18px;color:var(--slate);margin-top:auto}ul.big{font-size:40px;line-height:1.5;padding-left:40px;margin:0}ul.big li::marker{color:var(--teal)}
.wide{padding:44px 56px}.wide h2{margin-bottom:18px}.diagram svg{width:100%;height:480px}.cap{font-size:26px;margin:6px 0 0;text-align:center}
.shots{display:flex;gap:22px;flex:1;min-height:0}.shots figure{flex:1;margin:0;display:flex;flex-direction:column;min-height:0}
.shots img{width:100%;flex:1;min-height:0;object-fit:cover;object-position:left top;background:#000;border:1px solid var(--line);border-radius:10px}
.shots figcaption{font-size:24px;text-align:center;margin-top:10px}
.split{display:flex;gap:44px;align-items:flex-start}.split>div:first-child{flex:0 0 330px}.bars{flex:1;display:flex;flex-direction:column;gap:18px}
.bar{display:grid;grid-template-columns:340px 1fr 70px;align-items:center;gap:14px;font-size:22px}.track{height:22px;background:var(--mist);border-radius:11px;overflow:hidden}
.fill{height:100%;background:var(--teal)}.est{font-size:15px;background:#E6F3F4;color:#0E5F68;border-radius:5px;padding:1px 7px;vertical-align:middle}
.todo{background:#FFF4D6;border:1px solid #E5B94B;border-radius:6px;padding:2px 10px;color:#7A5300}
.ut{font-size:18px;white-space:pre-wrap}
#nav{position:fixed;bottom:10px;left:50%;transform:translateX(-50%);display:flex;gap:8px;z-index:2}
#nav button{background:#13294B;color:#F3F6FA;border:1px solid #2B4269;border-radius:8px;padding:6px 14px;font-size:16px;cursor:pointer}
@media print{@page{size:1280px 720px;margin:0}html,body{background:#fff}#stage{position:static;transform:none!important}
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
