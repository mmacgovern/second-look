"""scripts/run_app.py: --live is stripped from Streamlit's arguments and reported."""

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("run_app", ROOT / "scripts" / "run_app.py")
ra = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ra)


def test_default_leaves_mode_to_settings():
    args, mode = ra.streamlit_args(["--server.port", "8501"])
    assert mode is None
    assert args[:2] == ["streamlit", "run"] and args[-2:] == ["--server.port", "8501"]


def test_live_flag_removed_from_streamlit_args():
    args, mode = ra.streamlit_args(["--live", "--server.port", "8502"])
    assert mode == "live" and "--live" not in args and args[-1] == "8502"


def test_demo_flag_forces_demo():
    args, mode = ra.streamlit_args(["--demo"])
    assert mode == "demo" and "--demo" not in args
