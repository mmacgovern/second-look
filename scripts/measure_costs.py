"""Measure real AI costs and project monthly spend.

Runs live (DEMO_MODE forced off) with the app's response cache off AND the Portkey
gateway cache bypassed (cache_force_refresh), so every step is a real, billed
model call:
    3 sample portfolios x 3 fluency levels x 3 repeats (27 full analyses)
    + 5 Pause Button scenarios
Each run is its own ledger session. Before every run the script checks the
worst-case cost of that run and stops if the total would pass --cap ($1.00).

Writes docs/cost_results.json and docs/cost_results.md with, per agent and per
full analysis: mean input tokens, mean output tokens, mean $ and p95 latency,
plus monthly projections for 1,000 / 10,000 / 50,000 users at 2 analyses each,
with and without prompt caching.

"With caching" assumes each agent's fixed system prompt is served from the
provider's prompt cache, billed at 10% of the input price (the course price
table lists $0.01 vs $0.10 per 1M tokens for gpt-6-luna). The rest of the input
(the Facts JSON, which differs for every portfolio) and all output are billed in
full. Prompt-token counts for the system prompts are estimated at 4 characters
per token.

Every request carries a Portkey trace id ("<session>-<agent>") so a run can be
checked against Portkey's own logs.

Usage:
    python scripts/measure_costs.py
    python scripts/measure_costs.py --repeats 1 --cap 0.25
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from secondlook import budget, demo_runs, llm, orchestrator  # noqa: E402
from secondlook.agents import analyst, explainer, pause, verifier  # noqa: E402
from secondlook.agents.base import AgentContext, model_for, system_prompt  # noqa: E402
from secondlook.config import Settings, load_settings  # noqa: E402
from secondlook.quant.models import Action  # noqa: E402

DOCS = ROOT / "docs"
CAP_USD = 1.00
USERS = (1_000, 10_000, 50_000)
ANALYSES_PER_USER = 2
CACHED_INPUT_RATIO = 0.10
PAUSES = demo_runs.PAUSE_SCENARIOS + [
    ("midcareer", Action(kind="sell_pct", ticker="QQQ", pct=30)),
    ("retiree", Action(kind="sell_all")),
]
SPECS = {s.name: s for s in (analyst.SPEC, explainer.SPEC, verifier.SPEC, pause.SPEC)}


def p95(values: list[float]) -> float:
    """Nearest-rank 95th percentile."""
    ordered = sorted(values)
    return ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)] if ordered else 0.0


def worst_case(settings: Settings, steps: list[str], input_tokens: int = 4000) -> float:
    return sum(budget.cost_usd(model_for(SPECS[a], settings), input_tokens, SPECS[a].max_tokens) for a in steps)


def system_prompt_tokens(agent: str) -> int:
    return math.ceil(len(system_prompt(agent)) / 4)


def cached_cost(row: dict) -> float:
    """What this call would cost if its system prompt came from the provider's prompt cache."""
    price = budget.price_for(row["model"])
    cached = min(system_prompt_tokens(row["agent"]), row["input_tokens"])
    saving = cached * price.input_per_1m * (1 - CACHED_INPUT_RATIO) / 1_000_000
    return max(0.0, row["cost_usd"] - saving)


def stats(rows: list[dict], latency: list[float] | None = None) -> dict:
    return {
        "n": len(rows),
        "mean_input_tokens": round(statistics.fmean(r["input_tokens"] for r in rows), 1) if rows else 0,
        "mean_output_tokens": round(statistics.fmean(r["output_tokens"] for r in rows), 1) if rows else 0,
        "mean_usd": round(statistics.fmean(r["cost_usd"] for r in rows), 7) if rows else 0,
        "mean_usd_with_caching": round(statistics.fmean(r["cached_usd"] for r in rows), 7) if rows else 0,
        "p95_latency_s": round(p95(latency if latency is not None else [r["latency_s"] for r in rows]), 2),
    }


def group(rows: list[dict], sessions: list[str]) -> list[dict]:
    out = []
    for s in sessions:
        mine = [r for r in rows if r["session_id"] == s]
        if mine:
            out.append({"input_tokens": sum(r["input_tokens"] for r in mine), "output_tokens": sum(r["output_tokens"] for r in mine),
                        "cost_usd": sum(r["cost_usd"] for r in mine), "cached_usd": sum(r["cached_usd"] for r in mine),
                        "latency_s": 0.0, "calls": len(mine)})
    return out


def markdown(report: dict) -> str:
    lines = [f"# Second Look: measured AI costs", "",
             f"Run {report['run_id']} ({report['run_at']}), models: fast = {report['models']['fast']}, "
             f"smart = {report['models']['smart']}. App response cache off and Portkey gateway cache bypassed; "
             f"gateway cache hits during the run: {report['gateway_cache_hits']}.", "",
             f"Total spent: ${report['total_usd']:.4f} on {report['calls']} calls "
             f"({report['analyses']} analyses, {report['pauses']} pause checks).", "",
             "| Unit | n | Mean input tokens | Mean output tokens | Mean $ | Mean $ with caching | p95 latency (s) |",
             "|---|---:|---:|---:|---:|---:|---:|"]
    for name, s in list(report["per_agent"].items()) + [("**Full analysis**", report["per_analysis"]),
                                                         ("**Pause check**", report["per_pause"])]:
        lines.append(f"| {name} | {s['n']} | {s['mean_input_tokens']:,.0f} | {s['mean_output_tokens']:,.0f} | "
                     f"${s['mean_usd']:.6f} | ${s['mean_usd_with_caching']:.6f} | {s['p95_latency_s']:.2f} |")
    lines += ["", f"## Monthly projection ({ANALYSES_PER_USER} analyses per user per month)", "",
              "| Users | Analyses / month | Without caching | With caching |", "|---:|---:|---:|---:|"]
    for p in report["projection"]:
        lines.append(f"| {p['users']:,} | {p['analyses']:,} | ${p['without_caching_usd']:,.2f} | ${p['with_caching_usd']:,.2f} |")
    lines += ["", report["caching_note"], "",
              f"Analysis sources: {report['sources']}. Latency for a full analysis is end-to-end wall time."]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None, client=None, settings: Settings | None = None,
         db_path: Path | None = None, out_dir: Path = DOCS) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--cap", type=float, default=CAP_USD, help="stop before the total could pass this many dollars")
    parser.add_argument("--personas", nargs="*", default=list(demo_runs.PERSONAS))
    parser.add_argument("--fluencies", nargs="*", default=list(demo_runs.FLUENCIES))
    parser.add_argument("--pauses", type=int, default=len(PAUSES))
    args = parser.parse_args(argv)

    base = settings or load_settings()
    if not base.has_key:
        print("Measuring real costs needs PORTKEY_API_KEY in .env or the environment.")
        return 2
    live = base.model_copy(update={"demo_mode": False, "model_fast": base.model_fast or "gpt-6-luna",
                                   "model_smart": base.model_smart or "gpt-6-luna"})
    run_id = f"costs-{datetime.now():%Y%m%d-%H%M%S}"
    runs, stopped = [], None

    def spent() -> float:
        return sum(r["cost_usd"] for r in budget.calls(db_path, run_id))

    def go(kind: str, label: str, steps: list[str], work) -> bool:
        nonlocal stopped
        worst = worst_case(live, steps)
        if spent() + worst > args.cap:
            stopped = f"stopped before {label}: ${spent():.4f} spent + ${worst:.4f} worst case > ${args.cap:.2f} cap"
            return False
        session = f"{run_id}-{len(runs):02d}"
        llm.TRACE_PREFIX = session
        start = time.perf_counter()
        source, used = work(AgentContext(session_id=session, settings=live, client=client, db_path=db_path))
        runs.append({"kind": kind, "label": label, "session": session, "source": source, "steps": used,
                     "wall_s": round(time.perf_counter() - start, 2)})
        print(f"  {kind:8} {label:42} {source:10} {time.perf_counter() - start:5.1f}s  total ${spent():.4f}")
        return True

    llm.CACHE.clear()
    llm.CACHE_ENABLED = False
    llm.FORCE_REFRESH = True
    try:
        for persona in args.personas:
            facts = demo_runs.persona_facts(persona)
            for fluency in args.fluencies:
                for r in range(args.repeats):
                    def analysis(ctx, facts=facts, fluency=fluency):
                        res = orchestrator.run_analysis(facts, fluency, ctx)
                        return res.source, res.steps
                    if not go("analysis", f"{persona} / {fluency} / run {r + 1}",
                              ["analyst", "explainer", "verifier", "explainer"], analysis):
                        break
                if stopped:
                    break
            if stopped:
                break
        for name, action in ([] if stopped else PAUSES[: args.pauses]):
            def pause_run(ctx, name=name, action=action):
                return orchestrator.run_pause(demo_runs.persona_facts(name, action), ctx).source, ["pause"]
            if not go("pause", f"{name} / {action.describe()}", ["pause"], pause_run):
                break
    finally:
        llm.CACHE_ENABLED = True
        llm.TRACE_PREFIX = None
        llm.FORCE_REFRESH = False

    rows = budget.calls(db_path, run_id)
    for row in rows:
        row["cached_usd"] = cached_cost(row)
    analyses = [r for r in runs if r["kind"] == "analysis"]
    pauses = [r for r in runs if r["kind"] == "pause"]
    per_analysis = group(rows, [r["session"] for r in analyses])
    per_pause = group(rows, [r["session"] for r in pauses])
    analysis_stats = stats(per_analysis, [r["wall_s"] for r in analyses])
    projection = [{"users": u, "analyses": u * ANALYSES_PER_USER,
                   "without_caching_usd": round(u * ANALYSES_PER_USER * analysis_stats["mean_usd"], 2),
                   "with_caching_usd": round(u * ANALYSES_PER_USER * analysis_stats["mean_usd_with_caching"], 2)}
                  for u in USERS]
    first = rows[0] if rows else None
    report = {
        "run_id": run_id,
        "run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "models": {"fast": live.model_fast, "smart": live.model_smart},
        "cap_usd": args.cap,
        "stopped": stopped,
        "total_usd": round(sum(r["cost_usd"] for r in rows), 6),
        "calls": len(rows),
        "gateway_cache_hits": sum(int(r["cache_hit"]) for r in rows),
        "analyses": len(analyses),
        "pauses": len(pauses),
        "sources": dict(sorted({r["source"]: sum(x["source"] == r["source"] for x in runs) for r in runs}.items())),
        "per_agent": {a: stats([r for r in rows if r["agent"] == a]) for a in sorted({r["agent"] for r in rows})},
        "per_analysis": analysis_stats,
        "per_pause": stats(per_pause, [r["wall_s"] for r in pauses]),
        "projection": projection,
        "caching_note": ("With caching: each agent's system prompt (about "
                         + ", ".join(f"{a} {system_prompt_tokens(a)}" for a in sorted(SPECS))
                         + " tokens) billed at 10% of the input price; Facts JSON and output billed in full. "
                         "OpenAI only caches prompt prefixes of 1,024 tokens or more, so this saving needs the "
                         "system prompts to grow past that size or be combined with a longer shared prefix."),
        "portkey_check": None if first is None else {
            "trace_id": f"{first['session_id']}-{first['agent']}", "timestamp_utc": first["timestamp"],
            "model": first["model"], "input_tokens": first["input_tokens"], "output_tokens": first["output_tokens"],
            "our_cost_usd": round(first["cost_usd"], 8)},
        "runs": runs,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "cost_results.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    (out_dir / "cost_results.md").write_text(markdown(report), encoding="utf-8")
    print()
    print(markdown(report))
    if stopped:
        print(stopped)
    if first:
        c = report["portkey_check"]
        print(f"Portkey check: filter logs by trace id {c['trace_id']} ({c['timestamp_utc']}): "
              f"{c['input_tokens']} in / {c['output_tokens']} out tokens, our cost ${c['our_cost_usd']:.8f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
