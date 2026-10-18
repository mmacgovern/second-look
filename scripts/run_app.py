"""Start the Streamlit app from the project folder, so .streamlit/config.toml (the theme) is found.

Usage:  python scripts/run_app.py [--live] [--server.port 8501]

--live sets DEMO_MODE=false for this process only (it writes the setting, never reads the key).
Without a Portkey key the app still falls back to demo mode.
"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def streamlit_args(argv: list[str]) -> tuple[list[str], bool]:
    """Streamlit's argv and whether --live was given."""
    live = "--live" in argv
    rest = [a for a in argv if a != "--live"]
    return ["streamlit", "run", str(ROOT / "app.py"), "--server.headless", "true", *rest], live


if __name__ == "__main__":
    os.chdir(ROOT)
    from streamlit.web import cli

    sys.argv, live = streamlit_args(sys.argv[1:])
    if live:
        os.environ["DEMO_MODE"] = "false"
    sys.exit(cli.main())
