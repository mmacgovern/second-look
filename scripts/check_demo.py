"""Check that demo mode replays every recorded run for $0 with no API key.

Settings are built with DEMO_MODE=true and no PORTKEY_API_KEY (the .env file
and environment are ignored), and any attempt to reach the API raises. Every
run must come from the recordings (not the template), and the ledger must show
$0.00 spent.

Usage:
    python scripts/check_demo.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from secondlook import budget, llm  # noqa: E402
from secondlook.agents.base import AgentContext  # noqa: E402
from secondlook.config import load_settings  # noqa: E402
from secondlook.demo_runs import run_all  # noqa: E402


class NoNetwork:
    """Stands in for the Portkey client; any use means demo mode tried to call the API."""

    @property
    def chat(self):
        raise RuntimeError("Demo mode tried to call the API")


def main(argv: list[str] | None = None) -> int:
    settings = load_settings(env_file=None, environ={"DEMO_MODE": "true"})  # key removed
    assert settings.demo_mode and not settings.has_key

    llm.CACHE.clear()
    original_get_client = llm.get_client
    llm.get_client = lambda s: NoNetwork()
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "ledger.db"
        try:
            results = run_all(AgentContext(session_id="demo-check", settings=settings, client=NoNetwork(), db_path=db))
        finally:
            llm.get_client = original_get_client
        spent = budget.summary(db)

    print(f"Key present: {settings.has_key}   Demo mode: {settings.demo_mode}\n")
    for r in results:
        print(f"  {r.source:10} {r.label}")
    failed = [r.label for r in results if r.source == "template"]
    print(f"\n{len(results) - len(failed)}/{len(results)} runs replayed from recordings; "
          f"total cost ${spent['total_usd']:.2f}")
    if failed or spent["total_usd"] != 0:
        print("FAIL: some runs had no recording (run scripts/record_demo.py) or cost money.")
        return 1
    print("PASS: demo mode works with no key, for $0.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
