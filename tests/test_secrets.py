"""Nothing shaped like an API key, and never the real key, in any file git tracks (or would
track) or in anything the app renders.

Failures name the file, line and rule with a masked value; the secret itself is never printed.
"""

import subprocess
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from secondlook import orchestrator
from secondlook.config import load_settings
from secondlook.safety.secret_scan import scan_text

ROOT = Path(__file__).resolve().parents[1]
BINARY = {".png", ".jpg", ".jpeg", ".gif", ".parquet", ".db", ".pyc", ".ico"}

# Read once at import, before the test fixtures clear the environment. Only compared, never printed.
REAL_SECRETS = load_settings().secret_values()


def repo_files() -> list[Path]:
    out = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard"],
                         cwd=ROOT, capture_output=True, text=True, check=True).stdout
    return [ROOT / line for line in out.splitlines() if line and Path(line).suffix.lower() not in BINARY]


def test_env_file_is_never_tracked():
    names = {p.name for p in repo_files()}
    assert ".env" not in names
    out = subprocess.run(["git", "check-ignore", ".env"], cwd=ROOT, capture_output=True, text=True)
    assert out.stdout.strip() == ".env"


def test_no_key_shaped_strings_in_repo_files():
    findings = []
    for path in repo_files():
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        findings += [f"{path.relative_to(ROOT)}:{f.line} {f.rule} {f.masked}" for f in scan_text(text, REAL_SECRETS)]
    assert findings == []


def rendered_text(at: AppTest) -> str:
    parts = []
    for kind in ("markdown", "caption", "title", "header", "subheader", "info", "warning", "error", "text"):
        parts += [str(e.value) for e in getattr(at, kind)]
    parts += [f"{m.label} {m.value} {m.help}" for m in at.metric]
    parts += [df.value.to_csv() for df in at.dataframe]
    parts += [str(e.value) for e in at.sidebar.markdown]
    return "\n".join(parts)


VIEWS = [{"page": "Admin"}] + [{"sample": s, "tab": t} for s in ("beginner", "midcareer", "retiree")
                               for t in ("X-Ray", "Fee Clock", "Crash Replay", "Retirement Plan", "Top 3 Things Worth Knowing")]


@pytest.mark.parametrize("params", VIEWS, ids=lambda p: "-".join(p.values()))
def test_no_key_shaped_strings_in_rendered_app(params, monkeypatch):
    # Pretend a real key is loaded, to prove it would never be rendered.
    fake = "pk-" + "Zq7" * 10
    monkeypatch.setenv("PORTKEY_API_KEY", fake)
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=120)
    for k, v in params.items():
        at.query_params[k] = v
    at.run()
    assert not at.exception
    text = rendered_text(at)
    assert text.strip()
    findings = [f"{params} line {f.line}: {f.rule} {f.masked}" for f in scan_text(text, [fake] + REAL_SECRETS)]
    assert findings == []


# --- the scanner itself ------------------------------------------------------------

def test_scanner_finds_key_shapes():
    # Built at run time so no key-shaped literal is committed.
    samples = {
        "openai-style key": "sk-" + "proj-" + "a1B2" * 6,
        "github token": "ghp_" + "x9" * 18,
        "aws access key": "AKIA" + "ABCDEFGHIJ123456",
        "google api key": "AIza" + "S" * 35,
        "key assignment": "PORTKEY_API_KEY=" + "q8W" * 9,
        "bearer token": "Authorization: Bearer " + "t0k" * 10,
        "private key": "-----BEGIN RSA " + "PRIVATE KEY-----",
    }
    for rule, text in samples.items():
        assert rule in {f.rule for f in scan_text(text)}, rule


def test_scanner_masks_and_skips_placeholders_and_allowed_lines():
    secret = "sk-" + "live-" + "Q1w2" * 6
    (finding,) = scan_text(f"key: {secret}")
    assert secret not in finding.masked and finding.masked.startswith(secret[:4])
    assert scan_text("PORTKEY_API_KEY=your-key-here") == []
    assert scan_text(f'FAKE = "{secret}"  # gitleaks:allow') == []
    assert scan_text("sha256 9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08") == []


def test_scanner_finds_the_exact_real_key_even_without_a_known_shape():
    odd = "plainlookingvalue123"
    assert [f.rule for f in scan_text(f"note {odd}", [odd])] == ["the real API key"]
