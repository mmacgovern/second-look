"""Record live AI responses for demo mode.

Runs the full pipeline once for each sample persona x fluency level, plus the
Pause Button scenarios, with DEMO_MODE forced off, and saves each AI answer to
demo/recorded/<agent>/<key>.json, keyed exactly as llm.py looks them up.

Only the model's answer text and token counts are saved: nothing from .env
(no key, no model names, no settings). The key is read at run time by
secondlook/config.py like everywhere else.

Usage:
    python scripts/record_demo.py                       # models from .env, else gpt-6-luna
    python scripts/record_demo.py --smart gpt-6-sol     # override a model for this run
"""

from __future__ import annotations

import argparse
import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from secondlook import budget, llm  # noqa: E402
from secondlook.agents.base import AgentContext  # noqa: E402
from secondlook.config import load_settings  # noqa: E402
from secondlook.demo_runs import run_all  # noqa: E402

DEFAULT_MODEL = "gpt-6-luna"


def main(argv: list[str] | None = None, client=None, settings=None, db_path: Path | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--fast", help="model for intake and verifier (default: MODEL_FAST, else gpt-6-luna)")
    parser.add_argument("--smart", help="model for analyst, explainer and pause (default: MODEL_SMART, else gpt-6-luna)")
    parser.add_argument("--out", type=Path, default=llm.DEMO_DIR)
    parser.add_argument("--keep", action="store_true", help="keep existing recordings instead of replacing them")
    args = parser.parse_args(argv)

    base = settings or load_settings()
    if not base.has_key:
        print("No PORTKEY_API_KEY found, so nothing can be recorded. Set it in .env or your environment.")
        return 1
    fast = args.fast or base.model_fast or DEFAULT_MODEL
    smart = args.smart or base.model_smart or DEFAULT_MODEL
    for model in (fast, smart):
        if model not in budget.PRICES:
            print(f"Warning: {model} is not in budget.PRICES; it will be budgeted at the high fallback price.")
    live = base.model_copy(update={"demo_mode": False, "model_fast": fast, "model_smart": smart})

    if args.out.exists() and not args.keep:
        shutil.rmtree(args.out)
    args.out.mkdir(parents=True, exist_ok=True)

    session = f"record-demo-{datetime.now():%Y%m%d-%H%M%S}"
    ctx = AgentContext(session_id=session, settings=live, client=client, db_path=db_path)
    print(f"Recording with fast={fast}, smart={smart} -> {args.out}\n")

    previous = llm.RECORD_DIR
    llm.CACHE.clear()
    llm.record_responses_to(args.out)
    try:
        results = run_all(ctx, on_result=lambda r: print(f"  {r.source:10} {r.label:62} {' > '.join(r.steps)}"))
    finally:
        llm.record_responses_to(previous)

    spent = budget.summary(db_path, session_prefix=session)  # every run's session starts with this
    files = sorted(args.out.rglob("*.json"))
    templates = [r.label for r in results if r.source == "template"]
    print(f"\n{len(results)} runs, {len(files)} recorded responses, "
          f"{sum(a['calls'] for a in spent['by_agent'])} AI calls (including cache hits)")
    for agent in spent["by_agent"]:
        print(f"  {agent['name']:18} {agent['calls']:3} calls  ${agent['cost_usd']:.6f}")
    print(f"Total cost: ${spent['total_usd']:.6f}")
    if templates:
        print(f"\nWarning: {len(templates)} run(s) fell back to the template and will show it in demo mode too:")
        for label in templates:
            print(f"  - {label}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
