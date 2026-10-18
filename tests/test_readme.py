"""README.md: the install sections say which platforms have been tested."""

from pathlib import Path

README = (Path(__file__).resolve().parents[1] / "README.md").read_text(encoding="utf-8")


def test_windows_steps_marked_tested():
    assert "## Install and run on Windows (tested)" in README


def test_macos_steps_marked_untested():
    assert "## Install and run on macOS (not yet tested on a Mac)" in README
