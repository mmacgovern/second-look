"""Start the Streamlit app from the project folder, so .streamlit/config.toml (the theme) is found.

Usage:  python scripts/run_app.py [--live | --demo] [--server.port 8501]

--live sets DEMO_MODE=false and --demo sets DEMO_MODE=true, for this process only (they write the
setting, never read the key). With neither, the app is live when a key is found and demo otherwise.
"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def streamlit_args(argv: list[str]) -> tuple[list[str], str | None]:
    """Streamlit's argv and the forced mode: "live", "demo" or None (decided by the settings)."""
    mode = "live" if "--live" in argv else "demo" if "--demo" in argv else None
    rest = [a for a in argv if a not in ("--live", "--demo")]
    return ["streamlit", "run", str(ROOT / "app.py"), "--server.headless", "true", *rest], mode


if __name__ == "__main__":
    os.chdir(ROOT)
    from streamlit.web import cli

    sys.argv, mode = streamlit_args(sys.argv[1:])
    if mode is not None:
        os.environ["DEMO_MODE"] = "false" if mode == "live" else "true"
    sys.exit(cli.main())
