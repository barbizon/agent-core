import argparse

import pytest

from agent_core.config import Settings
from agent_core.config_view import config_items, describe_config, source_of
from agent_core.debug import Tracer
from agent_core.permissions import PermissionPolicy

ENV = ("AGENT_BACKEND", "AGENT_MODEL", "AGENT_MAX_STEPS", "OPENAI_API_KEY", "AGENT_WORKSPACE",
       "AGENT_AUDIT_LOG", "AGENT_REQUEST_TIMEOUT_SECONDS", "AGENT_MAX_TOOL_RESULT_CHARS")


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in ENV:
        monkeypatch.delenv(name, raising=False)


def args(**overrides):
    values = dict(backend=None, model=None, max_steps=None, debug=False, env_file=".orvik/.env",
                  config=".orvik/config.json")
    values.update(overrides)
    return argparse.Namespace(**values)


def items(settings=None, policy=None, tracer=None, **arg_overrides):
    found = config_items(settings or Settings(), args(**arg_overrides),
                         policy or PermissionPolicy(allow_write=True, allow_dangerous=True),
                         tracer or Tracer())
    return {item.name: item for item in found}


def test_every_setting_has_a_value_source_and_meaning():
    report = items()
    assert set(report) == {"backend", "model", "openai_api_key", "max_steps",
                           "request_timeout_seconds", "max_tool_result_chars", "audit_log",
                           "workspace", "permissions", "debug", "config_file", "env_file"}
    assert all(item.value and item.source and item.meaning for item in report.values())


def test_sources_flag_beats_file_beats_env_beats_default():
    env = {"AGENT_MODEL": "m"}
    assert source_of("--model", None, "model", {}, {}) == "default"
    assert source_of("--model", None, "model", {}, env) == ".env / environment: AGENT_MODEL"
    assert source_of("--model", None, "model", {"model": "f"}, env) == ".orvik/config.json"
    assert source_of("--model", "x", "model", {"model": "f"}, env) == "flag --model"
    # null in the file means "not set here": the next level decides
    assert source_of("--model", None, "model", {"model": None}, env) == ".env / environment: AGENT_MODEL"


def test_api_key_value_is_never_shown(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-secret-123")
    text = describe_config(Settings(backend="openai", openai_api_key="sk-secret-123"),
                           args(), PermissionPolicy(), Tracer())
    assert "sk-secret-123" not in text
    assert "openai_api_key = set" in text


def test_values_reflect_the_running_setup(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_WORKSPACE", str(tmp_path))
    report = items(Settings(max_steps=3), PermissionPolicy(), Tracer(enabled=True),
                   max_steps=3, debug=True)
    assert report["max_steps"].value == "3" and report["max_steps"].source == "flag --max-steps"
    assert report["debug"].value == "on"
    assert report["workspace"].source == ".env / environment: AGENT_WORKSPACE"
    assert report["permissions"].value == (
        "read: run without asking · write: blocked · dangerous: blocked")
    assert report["model"].value == "(backend default)"


def test_workspace_default_is_the_start_folder():
    assert items()["workspace"].source == "default (the folder you started in)"
