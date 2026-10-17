"""Find anything shaped like an API key, token or private key in text.

Used by tests/test_secrets.py (all tracked files and the rendered app) and by
scripts/run_redteam.py (every AI output). Findings are masked: a finding never
contains the full secret, so reporting one cannot leak it.

Lines containing "gitleaks:allow" are skipped (the deliberate fake keys in tests).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

PLACEHOLDERS = {"your-key-here", "your_model_name_here", "[redacted]", "redacted", "changeme", "xxx"}

PATTERNS: list[tuple[str, re.Pattern]] = [
    ("openai-style key", re.compile(r"\bsk-(?:proj-|ant-|live-|test-)?[A-Za-z0-9_\-]{20,}")),
    ("github token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}|\bgithub_pat_[A-Za-z0-9_]{30,}")),
    ("aws access key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("google api key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("slack token", re.compile(r"\bxox[abposr]-[A-Za-z0-9\-]{10,}")),
    ("stripe live key", re.compile(r"\b[sr]k_live_[A-Za-z0-9]{20,}")),
    ("bearer token", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/\-]{24,}")),
    ("private key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("key assignment", re.compile(
        r"(?i)\b[\w\-]*(?:api[_\-]?key|secret|access[_\-]?token|auth[_\-]?token)[\w\-]*\s*[:=]\s*[\"']?"
        r"(?P<value>[A-Za-z0-9_\-+/=]{20,})")),
]


@dataclass(frozen=True)
class SecretFinding:
    rule: str
    line: int
    masked: str


def _mask(value: str) -> str:
    return value[:4] + "…" + f"({len(value)} chars)"


def scan_text(text: str, extra_secrets: list[str] | None = None) -> list[SecretFinding]:
    """Key-shaped strings in `text`, plus exact matches of any `extra_secrets` (e.g. the real key)."""
    findings: list[SecretFinding] = []
    secrets = [s for s in (extra_secrets or []) if s and len(s) >= 8]
    for number, line in enumerate(text.splitlines(), start=1):
        if "gitleaks:allow" in line:
            continue
        for rule, pattern in PATTERNS:
            for m in pattern.finditer(line):
                value = m.groupdict().get("value") or m.group(0)
                if value.strip("\"'").lower() in PLACEHOLDERS:
                    continue
                findings.append(SecretFinding(rule, number, _mask(value)))
        for secret in secrets:
            if secret in line:
                findings.append(SecretFinding("the real API key", number, _mask(secret)))
    return findings
