"""Measure real AI costs, project monthly spend, and compare quality against cost.

1. Main pass, live (DEMO_MODE forced off) with the app's response cache off AND the
   Portkey gateway cache bypassed (cache_force_refresh), so every step is a real,
   billed model call:
       3 sample portfolios x 3 fluency levels x 3 repeats (27 full analyses)
       + 5 Pause Button scenarios
       + 3 x 3 Possible Shifts wordings and 3 x 3 Try a Shift explanations (one per sample and level)
   A "full visit" is one analysis + one Possible Shifts + one Try a Shift; monthly projections
   assume VISITS_PER_USER full visits per user per month.
2. Response-cache pass: the same 27 analyses with the app's response cache on, as
   when people re-run a portfolio or switch fluency. Repeats and shared steps are
   served from the cache for free.
3. Quality vs cost: normal routing (the first repeat of the main pass) against
   MODEL_SMART for every agent (--smart-model), 3 personas x 3 fluency levels each:
   first-draft verifier pass rate, rewrites, safe-template fallbacks and cost.

Each run is its own ledger session. --max-usd (default $2.00) is enforced through
budget.check_run_budget before every run; the script also stops up front if the
daily budget (DAILY_BUDGET_USD) would block it, instead of changing .env.

Writes docs/cost_results.json + .md and docs/quality_vs_cost.json + .md.
Every request carries a Portkey trace id ("<session>-<agent>").

Usage:
    python scripts/measure_costs.py --estimate-only
    python scripts/measure_costs.py --smart-model gpt-6-sol
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
from secondlook.agents import analyst, explainer, pause, sandbox, shifts, verifier  # noqa: E402
from secondlook.agents.base import AgentContext, model_for  # noqa: E402
from secondlook.budget import BudgetExceeded  # noqa: E402
from secondlook.config import Settings, load_settings  # noqa: E402

DOCS = ROOT / "docs"
MAX_USD = 2.00
USERS = (100, 10_000)
VISITS_PER_USER = 2  # full visits (analysis + Possible Shifts + Try a Shift) per user per month
PAUSES = demo_runs.PAUSE_SCENARIOS  # the same 5 Pause Button scenarios the demo recordings cover
SPECS = {s.name: s for s in (analyst.SPEC, explainer.SPEC, verifier.SPEC, pause.SPEC, shifts.SPEC, sandbox.SPEC)}
ANALYSIS_STEPS = ["analyst", "explainer", "verifier", "explainer"]


def p95(values: list[float]) -> float:
    """Nearest-rank 95th percentile."""
    ordered = sorted(values)
    return ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)] if ordered else 0.0


def worst_case(settings: Settings, steps: list[str], input_tokens: int = 4000) -> float:
    return sum(budget.cost_usd(model_for(SPECS[a], settings), input_tokens, SPECS[a].max_tokens) for a in steps)


def stats(rows: list[dict], latency: list[float] | None = None) -> dict:
    def mean(key):
        return round(statistics.fmean(r[key] for r in rows), 1) if rows else 0

    return {
        "n": len(rows),
        "mean_input_tokens": mean("input_tokens"),
        "mean_cached_input_tokens": mean("cached_input_tokens"),
        "mean_output_tokens": mean("output_tokens"),
        "mean_usd": round(statistics.fmean(r["cost_usd"] for r in rows), 7) if rows else 0,
        "p95_latency_s": round(p95(latency if latency is not None else [r["latency_s"] for r in rows]), 2),
    }


def group(rows: list[dict], sessions: list[str]) -> list[dict]:
    out = []
    for s in sessions:
        mine = [r for r in rows if r["session_id"] == s]
        if mine:
            out.append({k: sum(r[k] for r in mine) for k in ("input_tokens", "cached_input_tokens", "output_tokens", "cost_usd")}
                       | {"latency_s": 0.0, "calls": len(mine)})
    return out


def quality(runs: list[dict], rows: list[dict]) -> dict:
    """First-draft verifier pass rate, rewrites, template fallbacks and cost for a set of analyses."""
    n = len(runs)
    first_pass = sum(1 for r in runs if "explainer_rewrite" not in r["steps"] and r["source"] == "ai")
    cost = sum(r["cost_usd"] for r in rows if r["session_id"] in {x["session"] for x in runs})
    return {
        "analyses": n,
        "first_draft_pass_rate": round(first_pass / n, 3) if n else 0,
        "rewrites": sum(1 for r in runs if "explainer_rewrite" in r["steps"]),
        "template_fallbacks": sum(1 for r in runs if r["source"] == "template"),
        "total_usd": round(cost, 6),
        "mean_usd_per_analysis": round(cost / n, 7) if n else 0,
    }


def cost_markdown(report: dict) -> str:
    lines = ["# Second Look: measured AI costs", "",
             f"Run {report['run_id']} ({report['run_at']}), models: fast = {report['models']['fast']}, "
             f"smart = {report['models']['smart']}. Main pass: app response cache off and Portkey gateway cache bypassed; "
             f"gateway cache hits: {report['gateway_cache_hits']}.", "",
             f"Main pass: ${report['total_usd']:.4f} on {report['calls']} calls "
             f"({report['analyses']} analyses, {report['pauses']} pause checks).", "",
             "| Unit | n | Mean input tokens | Mean cached input tokens | Mean output tokens | Mean $ | p95 latency (s) |",
             "|---|---:|---:|---:|---:|---:|---:|"]
    for name, s in list(report["per_agent"].items()) + [("**Full analysis**", report["per_analysis"]),
                                                         ("**Pause check**", report["per_pause"]),
                                                         ("**Possible Shifts**", report["per_shifts"]),
                                                         ("**Try a Shift**", report["per_sandbox"])]:
        lines.append(f"| {name} | {s['n']} | {s['mean_input_tokens']:,.0f} | {s['mean_cached_input_tokens']:,.0f} | "
                     f"{s['mean_output_tokens']:,.0f} | ${s['mean_usd']:.6f} | {s['p95_latency_s']:.2f} |")
    rc = report["response_cache"]
    lines += ["", f"A full visit (analysis + Possible Shifts + Try a Shift) costs ${report['per_visit_usd']:.6f}.", "",
              f"## Monthly cost ({VISITS_PER_USER} full visits per user per month)", "",
              f"With the app's response cache, the same 27 analyses cost ${rc['total_usd']:.4f} "
              f"(${rc['mean_usd_per_analysis']:.6f} each) because repeats and steps shared across fluency levels are "
              "served from the cache. That applies when people re-run a portfolio or switch fluency; a brand-new "
              "portfolio always costs the full amount. Possible Shifts and Try a Shift are counted at full price.", "",
              "| | Per user per month | 100 users | 10,000 users |", "|---|---:|---:|---:|"]
    for label, key in (("Without response cache", "without_cache"), ("With response cache", "with_cache")):
        m = report["monthly"][key]
        lines.append(f"| {label} | ${m['per_user_usd']:.4f} | ${m['users'][0]['total_usd']:,.2f} | "
                     f"${m['users'][1]['total_usd']:,.2f} |")
    lines += ["", f"Analysis sources (main pass): {report['sources']}. Latency for a full analysis is end-to-end wall time."]
    return "\n".join(lines) + "\n"


def quality_markdown(q: dict) -> str:
    lines = ["# Second Look: quality vs cost", "",
             f"3 personas x 3 fluency levels per routing ({q['run_id']}). Response cache off, gateway cache bypassed.", "",
             "| Routing | Models | First-draft verifier pass rate | Rewrites | Template fallbacks | Mean $ per analysis | Total $ |",
             "|---|---|---:|---:|---:|---:|---:|"]
    for name, r in q["routings"].items():
        lines.append(f"| {name} | {r['models']} | {100 * r['first_draft_pass_rate']:.0f}% | {r['rewrites']} | "
                     f"{r['template_fallbacks']} | ${r['mean_usd_per_analysis']:.6f} | ${r['total_usd']:.4f} |")
    if q.get("note"):
        lines += ["", q["note"]]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None, client=None, settings: Settings | None = None,
         db_path: Path | None = None, out_dir: Path = DOCS) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--max-usd", type=float, default=MAX_USD, help="stop before the run could pass this many dollars")
    parser.add_argument("--personas", nargs="*", default=list(demo_runs.PERSONAS))
    parser.add_argument("--fluencies", nargs="*", default=list(demo_runs.FLUENCIES))
    parser.add_argument("--pauses", type=int, default=len(PAUSES))
    parser.add_argument("--smart-model", help="model for every agent in the quality comparison (default: MODEL_SMART)")
    parser.add_argument("--skip-cache-pass", action="store_true")
    parser.add_argument("--skip-quality", action="store_true")
    parser.add_argument("--estimate-only", action="store_true", help="print the cost estimate and stop")
    args = parser.parse_args(argv)

    base = settings or load_settings()
    if not base.has_key:
        print("Measuring real costs needs PORTKEY_API_KEY in .env or the environment.")
        return 2
    live = base.model_copy(update={"demo_mode": False, "model_fast": base.model_fast or "gpt-6-luna",
                                   "model_smart": base.model_smart or "gpt-6-luna"})
    smart_model = args.smart_model or live.model_smart
    all_smart = live.model_copy(update={"model_fast": smart_model, "model_smart": smart_model})
    do_quality = not args.skip_quality and smart_model != live.model_fast

    n_analyses = len(args.personas) * len(args.fluencies) * args.repeats
    n_quality = len(args.personas) * len(args.fluencies) if do_quality else 0
    worst_analysis, worst_pause = worst_case(live, ANALYSIS_STEPS), worst_case(live, ["pause"])
    n_tabs = len(args.personas) * len(args.fluencies)
    worst_total = (n_analyses * worst_analysis * (1 if args.skip_cache_pass else 2) + args.pauses * worst_pause
                   + n_tabs * worst_case(live, ["shifts", "sandbox"]) + n_quality * worst_case(all_smart, ANALYSIS_STEPS))
    print(f"Planned: {n_analyses} analyses + {args.pauses} pause checks + {n_tabs} shifts + {n_tabs} sandbox"
          f"{'' if args.skip_cache_pass else f' + {n_analyses} response-cache analyses'}"
          f"{f' + {n_quality} all-{smart_model} analyses' if n_quality else ''}. "
          f"Worst-case cost ${worst_total:.4f} (cap ${args.max_usd:.2f}).")
    daily_left = live.daily_budget_usd - budget.spent_today(db_path)
    if worst_total > daily_left:
        print(f"The daily budget would block this run: worst case ${worst_total:.4f} but only ${daily_left:.4f} of "
              f"DAILY_BUDGET_USD=${live.daily_budget_usd:.2f} is left today. Raise DAILY_BUDGET_USD in .env yourself "
              "or run with fewer repeats; this script does not change .env.")
        return 3
    if args.estimate_only:
        return 0

    run_id = f"costs-{datetime.now():%Y%m%d-%H%M%S}"
    runs: list[dict] = []
    stopped = None

    def spent() -> float:
        return sum(r["cost_usd"] for r in budget.calls(db_path, run_id))

    def go(phase: str, kind: str, label: str, steps: list[str], run_settings: Settings, work) -> bool:
        nonlocal stopped
        try:
            budget.check_run_budget(spent(), worst_case(run_settings, steps), args.max_usd)
        except BudgetExceeded as exc:
            stopped = f"stopped before {phase} / {label}: {exc}"
            return False
        session = f"{run_id}-{len(runs):02d}"
        llm.TRACE_PREFIX = session
        start = time.perf_counter()
        source, used = work(AgentContext(session_id=session, settings=run_settings, client=client, db_path=db_path))
        runs.append({"phase": phase, "kind": kind, "label": label, "session": session, "source": source, "steps": used,
                     "wall_s": round(time.perf_counter() - start, 2)})
        print(f"  {phase:9} {kind:8} {label:40} {source:10} {time.perf_counter() - start:5.1f}s  total ${spent():.4f}")
        return True

    def analyses(phase: str, run_settings: Settings, repeats: int) -> None:
        for persona in args.personas:
            facts = demo_runs.persona_facts(persona)
            for fluency in args.fluencies:
                for r in range(repeats):
                    def work(ctx, facts=facts, fluency=fluency):
                        res = orchestrator.run_analysis(facts, fluency, ctx)
                        return res.source, res.steps
                    if not go(phase, "analysis", f"{persona} / {fluency} / run {r + 1}", ANALYSIS_STEPS, run_settings, work):
                        return

    llm.FORCE_REFRESH = True
    try:
        llm.CACHE.clear()
        llm.CACHE_ENABLED = False
        analyses("main", live, args.repeats)
        for name, action in ([] if stopped else PAUSES[: args.pauses]):
            def pause_run(ctx, name=name, action=action):
                return orchestrator.run_pause(demo_runs.persona_facts(name, action), ctx).source, ["pause"]
            if not go("main", "pause", f"{name} / {action.describe()}", ["pause"], live, pause_run):
                break
        for persona in ([] if stopped else args.personas):
            sh_facts, details, sh_banned = demo_runs.persona_shifts(persona)
            sb_facts, sb_banned = demo_runs.persona_sandbox(persona)
            for fluency in args.fluencies:
                def shifts_run(ctx, f=sh_facts, d=details, b=sh_banned, fluency=fluency):
                    r = orchestrator.run_shifts(f, d, fluency, ctx, b)
                    return r.source, ["shifts"] if r.source != "none" else []

                def sandbox_run(ctx, f=sb_facts, b=sb_banned, fluency=fluency):
                    return orchestrator.run_sandbox(f, fluency, ctx, b).source, ["sandbox"]

                if not go("main", "shifts", f"{persona} / {fluency}", ["shifts"], live, shifts_run):
                    break
                if not go("main", "sandbox", f"{persona} / {fluency}", ["sandbox"], live, sandbox_run):
                    break
        if not stopped and not args.skip_cache_pass:
            llm.CACHE.clear()
            llm.CACHE_ENABLED = True
            analyses("cache", live, args.repeats)
        if not stopped and do_quality:
            llm.CACHE.clear()
            llm.CACHE_ENABLED = False
            analyses("smart", all_smart, 1)
    finally:
        llm.CACHE_ENABLED = True
        llm.TRACE_PREFIX = None
        llm.FORCE_REFRESH = False

    rows = budget.calls(db_path, run_id)
    by_phase = {p: [r for r in runs if r["phase"] == p] for p in ("main", "cache", "smart")}
    main_sessions = {r["session"] for r in by_phase["main"]}
    main_rows = [r for r in rows if r["session_id"] in main_sessions]
    main_analyses = [r for r in by_phase["main"] if r["kind"] == "analysis"]
    main_pauses = [r for r in by_phase["main"] if r["kind"] == "pause"]
    main_shifts = [r for r in by_phase["main"] if r["kind"] == "shifts"]
    main_sandbox = [r for r in by_phase["main"] if r["kind"] == "sandbox"]
    analysis_stats = stats(group(main_rows, [r["session"] for r in main_analyses]), [r["wall_s"] for r in main_analyses])
    cache_runs = by_phase["cache"]
    cache_cost = sum(r["cost_usd"] for r in rows if r["session_id"] in {x["session"] for x in cache_runs})
    mean_with_cache = cache_cost / len(cache_runs) if cache_runs else analysis_stats["mean_usd"]

    shifts_stats = stats(group(main_rows, [r["session"] for r in main_shifts]), [r["wall_s"] for r in main_shifts])
    sandbox_stats = stats(group(main_rows, [r["session"] for r in main_sandbox]), [r["wall_s"] for r in main_sandbox])
    tabs_usd = shifts_stats["mean_usd"] + sandbox_stats["mean_usd"]
    visit_usd = analysis_stats["mean_usd"] + tabs_usd

    def monthly(mean_usd: float) -> dict:
        per_user = VISITS_PER_USER * mean_usd
        return {"per_user_usd": round(per_user, 6),
                "users": [{"users": u, "total_usd": round(u * per_user, 2)} for u in USERS]}

    first = main_rows[0] if main_rows else None
    report = {
        "run_id": run_id,
        "run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "models": {"fast": live.model_fast, "smart": live.model_smart},
        "max_usd": args.max_usd,
        "stopped": stopped,
        "total_usd": round(sum(r["cost_usd"] for r in main_rows), 6),
        "all_phases_usd": round(sum(r["cost_usd"] for r in rows), 6),
        "calls": len(main_rows),
        "gateway_cache_hits": sum(int(r["cache_hit"]) for r in main_rows),
        "analyses": len(main_analyses),
        "pauses": len(main_pauses),
        "sources": dict(sorted({r["source"]: sum(x["source"] == r["source"] for x in by_phase["main"])
                                for r in by_phase["main"]}.items())),
        "per_agent": {a: stats([r for r in main_rows if r["agent"] == a]) for a in sorted({r["agent"] for r in main_rows})},
        "per_analysis": analysis_stats,
        "per_pause": stats(group(main_rows, [r["session"] for r in main_pauses]), [r["wall_s"] for r in main_pauses]),
        "shifts_runs": len(main_shifts),
        "sandbox_runs": len(main_sandbox),
        "per_shifts": shifts_stats,
        "per_sandbox": sandbox_stats,
        "per_visit_usd": round(visit_usd, 7),
        "visits_per_user": VISITS_PER_USER,
        "response_cache": {"analyses": len(cache_runs), "total_usd": round(cache_cost, 6),
                           "mean_usd_per_analysis": round(mean_with_cache, 7)},
        # the response cache is measured for analyses only; shifts and sandbox are counted at full price
        "monthly": {"without_cache": monthly(visit_usd), "with_cache": monthly(mean_with_cache + tabs_usd)},
        "portkey_check": None if first is None else {
            "trace_id": f"{first['session_id']}-{first['agent']}", "timestamp_utc": first["timestamp"],
            "model": first["model"], "input_tokens": first["input_tokens"], "output_tokens": first["output_tokens"],
            "cached_input_tokens": first["cached_input_tokens"], "our_cost_usd": round(first["cost_usd"], 8)},
        "runs": runs,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "cost_results.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    (out_dir / "cost_results.md").write_text(cost_markdown(report), encoding="utf-8")
    print()
    print(cost_markdown(report))

    normal = [r for r in main_analyses if r["label"].endswith("run 1")]
    q = {"run_id": run_id, "routings": {
        "Normal routing": {"models": f"fast {live.model_fast}, smart {live.model_smart}", **quality(normal, rows)}}}
    if by_phase["smart"]:
        q["routings"][f"MODEL_SMART for every agent"] = {"models": f"all {smart_model}", **quality(by_phase["smart"], rows)}
    elif not args.skip_quality:
        q["note"] = (f"MODEL_SMART ({smart_model}) is the same model as MODEL_FAST, so there is nothing to compare; "
                     "rerun with --smart-model <model>.")
    (out_dir / "quality_vs_cost.json").write_text(json.dumps(q, indent=1), encoding="utf-8")
    (out_dir / "quality_vs_cost.md").write_text(quality_markdown(q), encoding="utf-8")
    print(quality_markdown(q))
    if stopped:
        print(stopped)
    if first:
        c = report["portkey_check"]
        print(f"Portkey check: filter logs by trace id {c['trace_id']} ({c['timestamp_utc']}): "
              f"{c['input_tokens']} in ({c['cached_input_tokens']} cached) / {c['output_tokens']} out tokens, "
              f"our cost ${c['our_cost_usd']:.8f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
