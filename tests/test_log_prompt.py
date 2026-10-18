"""Tests for .claude/hooks/log_prompt.py, run against a copy in a temp project."""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parents[1] / ".claude" / "hooks" / "log_prompt.py"
FAKE_KEY = "sk-test-1234567890abcdef1234567890"  # gitleaks:allow


@pytest.fixture
def project(tmp_path):
    hooks = tmp_path / ".claude" / "hooks"
    hooks.mkdir(parents=True)
    shutil.copy(HOOK, hooks / "log_prompt.py")
    (tmp_path / "AI_Prompts.md").write_text("# Log\n\n## Claude Code prompts\n", encoding="utf-8")
    return tmp_path


def run_hook(project: Path, stdin: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(project / ".claude" / "hooks" / "log_prompt.py")],
        input=stdin.encode("utf-8"),
        capture_output=True,
    )


def send(project: Path, prompt: str) -> subprocess.CompletedProcess:
    return run_hook(project, json.dumps({"hook_event_name": "UserPromptSubmit", "prompt": prompt}))


def log(project: Path) -> str:
    return (project / "AI_Prompts.md").read_text(encoding="utf-8")


def test_fake_key_is_redacted(project):
    result = send(project, f"Use this key for testing: {FAKE_KEY} thanks")
    assert result.returncode == 0
    text = log(project)
    assert FAKE_KEY not in text
    assert "1234567890abcdef" not in text
    assert "[REDACTED]" in text
    assert "Use this key for testing" in text


def test_named_secret_values_redacted_but_numbers_kept(project):
    send(project, "PORTKEY_API_KEY=abc123realvalue\nMY_SECRET: hunter2\nMAX_TOKENS_PER_CALL=1500")
    text = log(project)
    assert "abc123realvalue" not in text
    assert "hunter2" not in text
    assert "PORTKEY_API_KEY=[REDACTED]" in text
    assert "MAX_TOKENS_PER_CALL=1500" in text


def test_long_random_string_redacted(project):
    blob = "Zx9QwErTy7UiOpAsDfGh3JkLzXcVbNm42Lq"
    send(project, f"here is a blob {blob}")
    assert blob not in log(project)


def test_entries_numbered_with_phase_and_fenced(project):
    (project / ".claude" / "current_phase.txt").write_text("Phase 3 — Quant engine\n", encoding="utf-8")
    send(project, "first prompt")
    send(project, "second prompt")
    text = log(project)
    assert "### #1 — " in text and "### #2 — " in text
    assert "— Phase 3 — Quant engine" in text
    assert "```text\nsecond prompt\n```" in text


def test_missing_phase_file_uses_default(project):
    send(project, "hello")
    assert "No phase set" in log(project)


def test_backticks_in_prompt_get_longer_fence(project):
    send(project, "code:\n```py\nx = 1\n```")
    assert "````text\n" in log(project)


@pytest.mark.parametrize("prompt", ["/clear", "  /compact  ", "/loop 5m /foo", ""])
def test_slash_commands_and_empty_prompts_skipped(project, prompt):
    before = log(project)
    assert send(project, prompt).returncode == 0
    assert log(project) == before


def test_bad_input_exits_zero_and_prints_nothing(project):
    result = run_hook(project, "not json")
    assert result.returncode == 0
    assert result.stdout == b""


def test_never_writes_to_stdout(project):
    result = send(project, "normal prompt")
    assert result.returncode == 0
    assert result.stdout == b""


def test_ordinary_words_about_tokens_survive(project):
    send(project, "5. Tokenomics: measured tokens per typical run (input, cached input, output)\n"
                  "Token budget: about 2,000 tokens. Keys: never in chat.")
    text = log(project)
    assert "Tokenomics: measured tokens per typical run" in text
    assert "Token budget: about 2,000 tokens" in text and "Keys: never in chat" in text
    assert "[REDACTED]" not in text


def test_fake_key_still_redacted_next_to_prose(project):
    send(project, f"Tokenomics: measured tokens. api key: {FAKE_KEY} and token: Zx9Qw7Er")
    text = log(project)
    assert "Tokenomics: measured tokens" in text
    assert FAKE_KEY not in text and "Zx9Qw7Er" not in text
    assert text.count("[REDACTED]") == 2
