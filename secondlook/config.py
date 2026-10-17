"""Settings for Second Look. The only module that reads environment variables.

Values come from the process environment, then from `.env` (loaded with
python-dotenv without modifying os.environ). The Portkey key is held as a
pydantic SecretStr, so it never appears in repr/str of the settings object.
"""

from __future__ import annotations

import logging
import os
import re
from functools import lru_cache
from pathlib import Path

from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = PROJECT_ROOT / ".env"
REDACTED = "[REDACTED]"

PLACEHOLDER_KEYS = {"", "your-key-here"}

# Every key value a Settings object has held in this process; redact() uses it.
_KNOWN_SECRETS: set[str] = set()

# Env var name -> Settings field name.
ENV_FIELDS = {
    "PORTKEY_API_KEY": "portkey_api_key",
    "PORTKEY_PROVIDER": "portkey_provider",
    "DEMO_MODE": "demo_mode",
    "MODEL_FAST": "model_fast",
    "MODEL_SMART": "model_smart",
    "SESSION_BUDGET_USD": "session_budget_usd",
    "DAILY_BUDGET_USD": "daily_budget_usd",
    "MAX_TOKENS_PER_CALL": "max_tokens_per_call",
    "MAX_COST_PER_CALL_USD": "max_cost_per_call_usd",
}

# Generic secret shapes, used in addition to the configured key's exact value.
_SECRET_PATTERNS = [
    re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"(?i)(\b[\w\-]*(?:key|secret|token)[\w\-]*\s*[:=]\s*[\"']?)(?!\[REDACTED\])[^\s\"',;]{8,}"),
    re.compile(r"(?i)(\bbearer\s+)[A-Za-z0-9._~+/\-]{16,}=*"),
]


class Settings(BaseModel):
    model_config = ConfigDict(frozen=True)

    portkey_api_key: SecretStr | None = None
    portkey_provider: str = "openai"
    demo_mode: bool = True
    model_fast: str = ""
    model_smart: str = ""
    session_budget_usd: float = Field(default=0.10, ge=0)
    daily_budget_usd: float = Field(default=2.00, ge=0)
    max_tokens_per_call: int = Field(default=1500, gt=0)
    max_cost_per_call_usd: float = Field(default=0.05, ge=0)

    @field_validator("portkey_api_key", mode="before")
    @classmethod
    def _blank_key_is_none(cls, value):
        if isinstance(value, SecretStr):
            value = value.get_secret_value()
        if value is None or str(value).strip() in PLACEHOLDER_KEYS:
            return None
        return str(value).strip()

    @field_validator("model_fast", "model_smart", "portkey_provider", mode="before")
    @classmethod
    def _strip(cls, value):
        return "" if value is None else str(value).strip()

    @model_validator(mode="after")
    def _demo_without_key(self) -> "Settings":
        if self.portkey_api_key is None and not self.demo_mode:
            object.__setattr__(self, "demo_mode", True)
        _KNOWN_SECRETS.update(self.secret_values())
        return self

    @property
    def has_key(self) -> bool:
        return self.portkey_api_key is not None

    def secret_values(self) -> list[str]:
        return [self.portkey_api_key.get_secret_value()] if self.portkey_api_key else []


def load_settings(env_file: Path | str | None = ENV_FILE, environ: dict | None = None) -> Settings:
    """Build Settings from `.env` plus the environment (environment wins)."""
    values: dict[str, str | None] = {}
    if env_file is not None and Path(env_file).exists():
        values.update(dotenv_values(env_file))
    source = os.environ if environ is None else environ
    values.update({k: source[k] for k in ENV_FIELDS if k in source})
    data = {field: values[env] for env, field in ENV_FIELDS.items() if values.get(env) not in (None, "")}
    if "portkey_api_key" not in data:
        data["demo_mode"] = True
    return Settings(**data)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return load_settings(ENV_FILE)


def redact(text: object, secrets: list[str] | None = None) -> str:
    """Replace the configured key (and anything key-shaped) with [REDACTED]."""
    out = str(text)
    candidates = set(_KNOWN_SECRETS if secrets is None else secrets)
    for secret in sorted((s for s in candidates if s), key=len, reverse=True):
        out = out.replace(secret, REDACTED)
    for pattern in _SECRET_PATTERNS:
        out = pattern.sub(lambda m: (m.group(1) if m.groups() else "") + REDACTED, out)
    return out


class RedactingFilter(logging.Filter):
    """Logging filter that redacts secrets from the message, args and traceback."""

    def __init__(self, secrets: list[str] | None = None, name: str = "") -> None:
        super().__init__(name)
        self._secrets = secrets

    def _redact(self, value: object) -> str:
        return redact(value, self._secrets)

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = self._redact(record.getMessage())
        record.args = None
        if record.exc_info:
            record.exc_text = self._redact(logging.Formatter().formatException(record.exc_info))
        elif record.exc_text:
            record.exc_text = self._redact(record.exc_text)
        if record.stack_info:
            record.stack_info = self._redact(record.stack_info)
        return True


def get_logger(name: str, secrets: list[str] | None = None) -> logging.Logger:
    """A logger with the redacting filter attached exactly once."""
    logger = logging.getLogger(name)
    if not any(isinstance(f, RedactingFilter) for f in logger.filters):
        logger.addFilter(RedactingFilter(secrets))
    return logger
