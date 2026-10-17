"""Start the Streamlit app from the project folder, so .streamlit/config.toml (the theme) is found.

Usage:  python scripts/run_app.py [--server.port 8501]
"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

if __name__ == "__main__":
    os.chdir(ROOT)
    from streamlit.web import cli

    sys.argv = ["streamlit", "run", str(ROOT / "app.py"), "--server.headless", "true", *sys.argv[1:]]
    sys.exit(cli.main())
