"""UserPromptSubmit hook: append each prompt to AI_Prompts.md with secrets redacted.

Reads the hook JSON from stdin. Never blocks or modifies the prompt, prints nothing
to stdout (UserPromptSubmit stdout would be added to the conversation), and always
exits 0. Redaction is pattern matching only; it never reads .env.
"""

import json
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]  # <project>/.claude/hooks/log_prompt.py
LOG_FILE = ROOT / "AI_Prompts.md"
PHASE_FILE = ROOT / ".claude" / "current_phase.txt"

REDACTED = "[REDACTED]"
NOT_SECRET_VALUE = re.compile(r"(?i)(\d+(\.\d+)?|true|false|none|null)")

# Known credential formats.
TOKEN_PATTERNS = [
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S),
    re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}"),                      # OpenAI / Anthropic style
    re.compile(r"\b(?:sk|pk|rk)_(?:live|test)_[A-Za-z0-9]{10,}"),  # Stripe
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"),                  # GitHub
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"\bxox[abposr]-[A-Za-z0-9\-]{10,}"),              # Slack
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),                          # AWS access key id
    re.compile(r"\bAIza[0-9A-Za-z_\-]{30,}"),                     # Google
    re.compile(r"\bhf_[A-Za-z0-9]{20,}"),                         # Hugging Face
    re.compile(r"\beyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+"),  # JWT
]

# NAME=value / NAME: value where NAME contains KEY, SECRET, TOKEN or PASSWORD.
ASSIGNMENT = re.compile(
    r"(?i)(\b[\w\-]*(?:key|secret|token|passw(?:or)?d|pwd)[\w\-]*)(\s*[:=]\s*)([\"']?)([^\s\"'`,;]+)"
)
PASSWORD_PROSE = re.compile(r"(?i)\b(password|passwd|passphrase)(\s+(?:is|was)\s+)(\S+)")
BEARER = re.compile(r"(?i)\b(bearer\s+)[A-Za-z0-9._~+/\-]{16,}=*")
URL_CREDENTIALS = re.compile(r"(://[^\s:/@]+:)[^\s@/]+@")
LONG_RANDOM = re.compile(r"[A-Za-z0-9_\-+=]{32,}")

SLASH_COMMAND = re.compile(r"/[\w:.\-]+(\s+.*)?", re.S)


def _looks_like_setting(name: str) -> bool:
    """PORTKEY_API_KEY, my_secret, api-token: identifiers, not ordinary words like "Tokenomics"."""
    return "_" in name or "-" in name or (name.isupper() and len(name) > 3)


def _looks_like_key(value: str) -> bool:
    """Letters mixed with digits or symbols, the shape of a real key or password."""
    return len(value) >= 6 and bool(re.search(r"[A-Za-z]", value)) and bool(re.search(r"[\d_\-+/=]", value))


def _redact_assignment(match: re.Match) -> str:
    name, sep, quote, value = match.groups()
    if NOT_SECRET_VALUE.fullmatch(value):
        return match.group(0)
    if not (_looks_like_setting(name) or _looks_like_key(value)):
        return match.group(0)  # prose such as "Tokenomics: measured tokens"
    return f"{name}{sep}{quote}{REDACTED}"


def _redact_long_random(match: re.Match) -> str:
    s = match.group(0)
    if re.search(r"\d", s) and re.search(r"[A-Za-z]", s):
        return REDACTED
    return s


def redact(text: str) -> str:
    for pattern in TOKEN_PATTERNS:
        text = pattern.sub(REDACTED, text)
    text = ASSIGNMENT.sub(_redact_assignment, text)
    text = PASSWORD_PROSE.sub(lambda m: f"{m.group(1)}{m.group(2)}{REDACTED}", text)
    text = BEARER.sub(lambda m: f"{m.group(1)}{REDACTED}", text)
    text = URL_CREDENTIALS.sub(lambda m: f"{m.group(1)}{REDACTED}@", text)
    text = LONG_RANDOM.sub(_redact_long_random, text)
    return text


def is_slash_command(prompt: str) -> bool:
    return bool(SLASH_COMMAND.fullmatch(prompt.strip()))


def read_phase() -> str:
    try:
        phase = PHASE_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return "No phase set"
    return phase.splitlines()[0].strip() if phase else "No phase set"


def next_number(log_text: str) -> int:
    numbers = [int(n) for n in re.findall(r"^### #(\d+)\b", log_text, re.M)]
    return max(numbers, default=0) + 1


def fence_for(text: str) -> str:
    longest = max((len(run) for run in re.findall(r"`+", text)), default=0)
    return "`" * max(3, longest + 1)


def append_entry(prompt: str) -> None:
    if LOG_FILE.exists():
        log_text = LOG_FILE.read_text(encoding="utf-8")
    else:
        log_text = "# Second Look — AI Prompt Log\n\n## Claude Code prompts\n"

    clean = redact(prompt.strip())
    fence = fence_for(clean)
    timestamp = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M")
    entry = (
        f"\n### #{next_number(log_text)} — {timestamp} — {read_phase()}\n\n"
        f"{fence}text\n{clean}\n{fence}\n"
    )
    if not log_text.endswith("\n"):
        log_text += "\n"
    LOG_FILE.write_text(log_text + entry, encoding="utf-8")


def main() -> None:
    try:
        payload = json.loads(sys.stdin.buffer.read().decode("utf-8", errors="replace"))
        prompt = payload.get("prompt", "")
        if isinstance(prompt, str) and prompt.strip() and not is_slash_command(prompt):
            append_entry(prompt)
    except Exception:
        pass  # Logging must never interfere with the prompt.
    sys.exit(0)


if __name__ == "__main__":
    main()
