import logging

from conftest import FAKE_KEY, ODD_KEY
from secondlook import config
from secondlook.config import REDACTED, RedactingFilter, Settings, load_settings, redact


def test_missing_key_forces_demo_mode():
    s = load_settings(env_file=None, environ={"DEMO_MODE": "false"})
    assert s.portkey_api_key is None
    assert s.demo_mode is True


def test_placeholder_key_counts_as_missing():
    s = load_settings(env_file=None, environ={"PORTKEY_API_KEY": "your-key-here", "DEMO_MODE": "false"})
    assert s.has_key is False and s.demo_mode is True


def test_key_present_allows_live_mode():
    s = load_settings(env_file=None, environ={"PORTKEY_API_KEY": ODD_KEY, "DEMO_MODE": "false"})
    assert s.has_key and s.demo_mode is False


def test_env_file_loaded_and_environment_wins(tmp_path):
    env = tmp_path / "test.env"
    env.write_text("SESSION_BUDGET_USD=0.25\nMAX_TOKENS_PER_CALL=900\nMODEL_FAST=from-file\n", encoding="utf-8")
    s = load_settings(env_file=env, environ={"MODEL_FAST": "from-env"})
    assert s.session_budget_usd == 0.25
    assert s.max_tokens_per_call == 900
    assert s.model_fast == "from-env"


def test_defaults_match_env_example():
    s = load_settings(env_file=None, environ={})
    assert (s.session_budget_usd, s.daily_budget_usd, s.max_tokens_per_call) == (0.10, 2.00, 1500)


def test_key_never_in_repr_str_or_dump():
    s = Settings(portkey_api_key=ODD_KEY, demo_mode=False)
    for text in (repr(s), str(s), s.model_dump_json(), str(s.model_dump())):
        assert ODD_KEY not in text
    assert s.portkey_api_key.get_secret_value() == ODD_KEY


def test_redact_exact_value_and_key_shapes():
    Settings(portkey_api_key=ODD_KEY)
    text = redact(f"key={ODD_KEY} other {FAKE_KEY} Authorization: Bearer abcdefghijklmnop1234")
    assert ODD_KEY not in text and FAKE_KEY not in text and "abcdefghijklmnop1234" not in text
    assert REDACTED in text


def test_redact_leaves_ordinary_text():
    assert redact("Portfolio volatility is 12.3% over 252 days", secrets=[]) == (
        "Portfolio volatility is 12.3% over 252 days"
    )


def test_filter_redacts_message_args_and_traceback(caplog):
    logger = logging.getLogger("secondlook.test_filter")
    logger.addFilter(RedactingFilter([ODD_KEY]))
    with caplog.at_level(logging.INFO, logger="secondlook.test_filter"):
        logger.info("calling with %s", ODD_KEY)
        try:
            raise RuntimeError(f"bad auth {ODD_KEY}")
        except RuntimeError:
            logger.exception("failed")
    assert ODD_KEY not in caplog.text
    assert caplog.text.count(REDACTED) >= 2


def test_get_logger_adds_filter_once():
    a = config.get_logger("secondlook.once")
    b = config.get_logger("secondlook.once")
    assert a is b
    assert sum(isinstance(f, RedactingFilter) for f in a.filters) == 1


def test_reasoning_effort_setting():
    assert load_settings(env_file=None, environ={}).reasoning_effort == "low"
    assert load_settings(env_file=None, environ={"REASONING_EFFORT": " high "}).reasoning_effort == "high"
