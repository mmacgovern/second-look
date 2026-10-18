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


def test_env_one_folder_up_is_read_and_project_env_wins(tmp_path):
    parent, project = tmp_path / "parent.env", tmp_path / "project.env"
    parent.write_text(f"PORTKEY_API_KEY={ODD_KEY}\nMODEL_FAST=parent-model\n", encoding="utf-8")
    s = load_settings(env_file=project, environ={}, parent_env_file=parent)  # project .env missing
    assert s.has_key and s.model_fast == "parent-model"
    project.write_text("MODEL_FAST=project-model\n", encoding="utf-8")
    assert load_settings(env_file=project, environ={}, parent_env_file=parent).model_fast == "project-model"
    assert load_settings(env_file=project, environ={"MODEL_FAST": "env-model"}, parent_env_file=parent).model_fast == "env-model"


def test_key_without_demo_setting_runs_live_and_demo_true_forces_demo(tmp_path):
    parent = tmp_path / "parent.env"
    parent.write_text(f"PORTKEY_API_KEY={ODD_KEY}\n", encoding="utf-8")
    assert load_settings(env_file=tmp_path / "none.env", environ={}, parent_env_file=parent).demo_mode is False
    assert load_settings(env_file=tmp_path / "none.env", environ={"DEMO_MODE": "true"}, parent_env_file=parent).demo_mode
    parent.write_text(f"PORTKEY_API_KEY={ODD_KEY}\nDEMO_MODE=\n", encoding="utf-8")  # blank, as in .env.example
    assert load_settings(env_file=tmp_path / "none.env", environ={}, parent_env_file=parent).demo_mode is False


def test_no_key_is_always_demo_and_env_file_none_skips_both_files(tmp_path):
    parent = tmp_path / "parent.env"
    parent.write_text(f"PORTKEY_API_KEY={ODD_KEY}\n", encoding="utf-8")
    assert load_settings(env_file=None, environ={}, parent_env_file=parent).demo_mode is True
    assert load_settings(env_file=None, environ={"DEMO_MODE": "false"}).demo_mode is True
