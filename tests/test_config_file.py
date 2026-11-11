"""The JSON config file: priority, validation, live-reload helpers, write protection."""

import json
from pathlib import Path

import pytest

from agent_core.cli import export_for_tools, file_stamp, settings_changes
from agent_core.config import Settings, load_settings, read_config_file
from agent_core.errors import ConfigError, PermanentToolError
from agent_core.tools import files


def write(path: Path, data) -> Path:
    path.write_text(json.dumps(data) if not isinstance(data, str) else data)
    return path


def load(config, env=None, **flags):
    return load_settings(env_file=None, config_file=config, environ=env or {}, **flags)


def test_priority_flag_then_file_then_env_then_default(tmp_path):
    cfg = write(tmp_path / "c.json", {"max_steps": 5})
    env = {"AGENT_MAX_STEPS": "7", "AGENT_REQUEST_TIMEOUT_SECONDS": "30"}
    assert load(cfg, env, max_steps=3).max_steps == 3      # flag
    assert load(cfg, env).max_steps == 5                   # file
    assert load(tmp_path / "missing.json", env).max_steps == 7  # env
    assert load(None, {}).max_steps == Settings.max_steps  # default
    assert load(cfg, env).request_timeout_seconds == 30    # not in file → env


def test_null_in_file_means_not_set(tmp_path):
    cfg = write(tmp_path / "c.json", {"model": None, "workspace": None})
    s = load(cfg, {"AGENT_MODEL": "env-model"})
    assert s.model == "env-model" and s.workspace is None


def test_file_values_are_typed(tmp_path):
    cfg = write(tmp_path / "c.json", {"debug": True, "workspace": "~/proj", "audit_log": "a.jsonl"})
    s = load(cfg)
    assert s.debug is True
    assert s.workspace == Path("~/proj").expanduser()
    assert s.audit_log_path == Path("a.jsonl")


def test_api_key_is_never_read_from_the_file(tmp_path):
    cfg = write(tmp_path / "c.json", {"backend": "openai"})
    with pytest.raises(ConfigError, match="OPENAI_API_KEY"):
        load(cfg)
    with pytest.raises(ConfigError, match="unknown setting"):
        read_config_file(write(tmp_path / "k.json", {"openai_api_key": "sk-x"}))


@pytest.mark.parametrize("content,message", [
    ("{not json", "invalid JSON"),
    ("[1, 2]", "JSON object"),
    ('{"max_step": 5}', "unknown setting"),          # typo
    ('{"max_steps": 0}', "at least 1"),
    ('{"max_steps": true}', "whole number"),
    ('{"debug": "maybe"}', "true or false"),
    ('{"backend": "gemini"}', "backend must be one of"),
    ('{"model": ""}', "non-empty string"),
])
def test_bad_files_give_clear_errors(tmp_path, content, message):
    with pytest.raises(ConfigError, match=message):
        load(write(tmp_path / "c.json", content))


def test_settings_changes_lists_differences_but_hides_the_key():
    old = Settings(max_steps=10, openai_api_key="sk-old")
    new = Settings(max_steps=5, debug=True, openai_api_key="sk-new")
    changes = settings_changes(old, new)
    assert "max_steps: 10 → 5" in changes and "debug: False → True" in changes
    assert "openai_api_key: (changed)" in changes
    assert not any("sk-" in change for change in changes)


def test_file_stamp_notices_changes(tmp_path):
    path = write(tmp_path / "c.json", {"max_steps": 5})
    first = file_stamp(path)
    write(path, {"max_steps": 50})
    assert file_stamp(path) != first
    assert file_stamp(tmp_path / "missing.json") is None


@pytest.fixture(autouse=True)
def restore_tool_env(monkeypatch):
    """export_for_tools sets environment variables; let pytest undo them after each test."""
    monkeypatch.delenv(files.PROTECTED_PATHS_ENV, raising=False)
    monkeypatch.delenv(files.WORKSPACE_ENV, raising=False)


@pytest.fixture
def project(tmp_path, monkeypatch):
    """A working directory with an .orvik folder, exported the way the CLI does it."""
    monkeypatch.chdir(tmp_path)
    agent_dir = tmp_path / ".orvik"
    (agent_dir / "tools" / "x").mkdir(parents=True)
    write(agent_dir / "config.json", {"max_steps": 5})
    (agent_dir / ".env").write_text("OPENAI_API_KEY=sk-secret")
    (tmp_path / "notes.txt").write_text("hello")
    export_for_tools(Settings(), agent_dir / "config.json", agent_dir / ".env")
    return tmp_path


def test_agent_folder_is_hidden_from_listings(project):
    names = [entry["name"] for entry in files.list_folder()["entries"]]
    assert names == ["notes.txt"]


@pytest.mark.parametrize("path", [".orvik/.env", ".orvik/config.json", ".orvik", ".ORVIK/.env"])
def test_agent_folder_cannot_be_read(project, path):
    with pytest.raises(PermanentToolError):
        files.read_file(path)


@pytest.mark.parametrize("path", [".orvik/config.json", ".orvik/tools/evil/tool.json"])
def test_agent_folder_cannot_be_written(project, path):
    with pytest.raises(PermanentToolError, match="belongs to the agent"):
        files.write_file(path, '{"risk_level": "read"}')
    assert json.loads((project / ".orvik" / "config.json").read_text()) == {"max_steps": 5}
    assert not (project / ".orvik" / "tools" / "evil").exists()


def test_custom_config_outside_the_folder_is_protected_too(project):
    custom = write(project / "my-config.json", {"max_steps": 3})
    export_for_tools(Settings(), custom)
    with pytest.raises(PermanentToolError, match="belongs to the agent"):
        files.write_file("my-config.json", "{}")
    assert files.write_file("other.json", "{}")["created"] is True  # normal files still fine


def test_export_for_tools_sets_and_clears_workspace(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    other = tmp_path / "other"
    other.mkdir()
    export_for_tools(Settings(workspace=other), tmp_path / "c.json")
    assert files.workspace_root() == other.resolve()
    export_for_tools(Settings(), tmp_path / "c.json")
    assert files.workspace_root() == tmp_path.resolve()  # default: the start folder
