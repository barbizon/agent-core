import pytest

from agent_core.config import load_settings
from agent_core.errors import ConfigError

ENV = ("AGENT_BACKEND", "AGENT_MODEL", "OPENAI_API_KEY", "AGENT_MAX_STEPS")


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in ENV:
        monkeypatch.delenv(name, raising=False)


def test_default_backend_is_codex_and_needs_no_key():
    settings = load_settings(env_file=None)
    assert settings.backend == "codex"
    assert settings.model is None


def test_openai_backend_requires_key(monkeypatch):
    with pytest.raises(ConfigError):
        load_settings(env_file=None, backend="openai")
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    assert load_settings(env_file=None, backend="openai").backend == "openai"


def test_unknown_backend_rejected():
    with pytest.raises(ConfigError):
        load_settings(env_file=None, backend="gemini")


def test_env_and_flag_precedence(monkeypatch):
    monkeypatch.setenv("AGENT_MODEL", "env-model")
    monkeypatch.setenv("AGENT_MAX_STEPS", "3")
    settings = load_settings(env_file=None)
    assert (settings.model, settings.max_steps) == ("env-model", 3)
    settings = load_settings(env_file=None, model="flag-model", max_steps=7)
    assert (settings.model, settings.max_steps) == ("flag-model", 7)

    monkeypatch.setenv("AGENT_MAX_STEPS", "abc")
    with pytest.raises(ConfigError):
        load_settings(env_file=None)


def test_claude_backend_needs_no_key():
    assert load_settings(env_file=None, backend="claude").backend == "claude"


# --- Main settings: ~/.orwick/config.json, used in every folder -----------------------------

def write(path, values):
    import json
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(values))
    return path


def test_main_settings_apply_when_nothing_else_sets_a_value(tmp_path):
    main = write(tmp_path / "home" / "config.json", {"backend": "claude", "max_steps": 4, "debug": True})
    settings = load_settings(env_file=None, user_config_file=main)
    assert (settings.backend, settings.max_steps, settings.debug) == ("claude", 4, True)


def test_folder_file_env_and_flags_all_beat_the_main_settings(tmp_path, monkeypatch):
    main = write(tmp_path / "home" / "config.json", {"backend": "claude", "model": "main-model",
                                                     "max_steps": 4, "request_timeout_seconds": 30})
    folder = write(tmp_path / "folder.json", {"backend": "codex", "max_steps": None})
    monkeypatch.setenv("AGENT_MODEL", "env-model")
    settings = load_settings(env_file=None, config_file=folder, user_config_file=main)
    assert settings.backend == "codex"              # folder file
    assert settings.model == "env-model"            # environment
    assert settings.max_steps == 4                  # null in the folder file: main decides
    assert settings.request_timeout_seconds == 30   # only in main
    assert load_settings(env_file=None, max_steps=9, user_config_file=main).max_steps == 9


def test_main_settings_may_not_set_the_workspace(tmp_path):
    main = write(tmp_path / "home" / "config.json", {"workspace": "/somewhere"})
    with pytest.raises(ConfigError, match="workspace can't be set in the main settings"):
        load_settings(env_file=None, user_config_file=main)
    write(main, {"workspace": None, "backend": "claude"})
    assert load_settings(env_file=None, user_config_file=main).backend == "claude"


def test_main_settings_are_checked_like_any_config_file(tmp_path):
    main = write(tmp_path / "home" / "config.json", {"temperature": 1})
    with pytest.raises(ConfigError, match="unknown setting"):
        load_settings(env_file=None, user_config_file=main)
    write(main, {"max_steps": -1})
    with pytest.raises(ConfigError, match="max_steps"):
        load_settings(env_file=None, user_config_file=main)


def test_a_missing_main_file_changes_nothing(tmp_path):
    assert load_settings(env_file=None, user_config_file=tmp_path / "nope.json").backend == "codex"



# --- Tools on/off -----------------------------------------------------------------------

def test_tools_from_main_and_folder_are_merged_folder_winning(tmp_path):
    main = write(tmp_path / "home" / "config.json", {"tools": {"run_command": False, "grep": False}})
    folder = write(tmp_path / "folder.json", {"tools": {"run_command": True}})
    settings = load_settings(env_file=None, config_file=folder, user_config_file=main)
    assert settings.tools == {"run_command": True, "grep": False}
    assert settings.disabled_tools == {"grep"}


@pytest.mark.parametrize("bad", [["run_command"], {"run_command": "no"}, {"Bad Name": False}])
def test_tools_must_be_names_with_true_or_false(tmp_path, bad):
    main = write(tmp_path / "home" / "config.json", {"tools": bad})
    with pytest.raises(ConfigError, match="tools must look like"):
        load_settings(env_file=None, user_config_file=main)


def test_every_limit_accepts_zero_for_no_limit():
    from agent_core.config import Settings
    s = Settings(max_steps=0, request_timeout_seconds=0, max_tool_result_chars=0,
                 history_budget_tokens=0, history_warn_percent=0)
    assert (s.max_steps, s.request_timeout_seconds, s.max_tool_result_chars) == (0, 0, 0)
    for bad in ({"max_tool_result_chars": 50}, {"history_budget_tokens": 5000}, {"request_timeout_seconds": -1}):
        with pytest.raises(ConfigError, match="0 for no limit"):
            Settings(**bad)
