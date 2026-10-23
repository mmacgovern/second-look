"""README.md: the install sections say which platforms have been tested."""

from pathlib import Path

README = (Path(__file__).resolve().parents[1] / "README.md").read_text(encoding="utf-8")


def test_windows_steps_marked_tested():
    assert "## Install and run on Windows (tested)" in README


def test_macos_steps_marked_untested():
    assert "## Install and run on macOS (not yet tested on a Mac)" in README


def test_graders_section_says_local_only_and_how_to_run():
    section = README[README.index("## For graders: how to run it"):README.index("## What you need first")]
    assert "runs locally, by design" in section and "no online version" in section
    assert "python -m pip install -r requirements.txt" in section and "python -m streamlit run app.py" in section
    assert ".env" in section and "demo mode" in section and "90 characters" in section
    for anchor in ("#install-and-run-on-windows-tested", "#install-and-run-on-macos-not-yet-tested-on-a-mac", "#troubleshooting"):
        assert anchor in section
