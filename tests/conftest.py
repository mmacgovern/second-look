"""Shared fixtures. Tests never touch the real .env, ledger, cache or recordings."""

from types import SimpleNamespace

import pytest

from secondlook import budget, config, llm

# Two fake keys: one shaped like a real key, one that only exact-value redaction can catch.
FAKE_KEY = "sk-test-1234567890abcdef1234567890"
ODD_KEY = "portkey-NOTAREAL-q7w8e9r0t1y2"


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    for name in config.ENV_FIELDS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(config, "ENV_FILE", tmp_path / "no-such.env")
    config.get_settings.cache_clear()
    monkeypatch.setattr(budget, "LEDGER_PATH", tmp_path / ".runtime" / "ledger.db")
    monkeypatch.setattr(llm, "DEMO_DIR", tmp_path / "demo" / "recorded")
    llm.CACHE.clear()
    yield
    llm.CACHE.clear()
    config.get_settings.cache_clear()


@pytest.fixture
def ledger(tmp_path):
    return tmp_path / ".runtime" / "ledger.db"


@pytest.fixture
def live_settings():
    """Live-mode settings with small, round limits that are easy to hit."""
    return config.Settings(
        portkey_api_key=ODD_KEY,
        demo_mode=False,
        model_fast="TODO-fast-model",
        session_budget_usd=0.10,
        daily_budget_usd=2.00,
        max_tokens_per_call=1500,
        max_cost_per_call_usd=0.05,
    )


def fake_completion(text="hello", prompt_tokens=100, completion_tokens=50):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=text))],
        usage=SimpleNamespace(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens),
    )


class FakeClient:
    """Stands in for Portkey: returns or raises the queued outcomes in order."""

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes) or [fake_completion()]
        self.calls = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        outcome = self.outcomes.pop(0) if len(self.outcomes) > 1 else self.outcomes[0]
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


@pytest.fixture
def fake_client_cls():
    return FakeClient
