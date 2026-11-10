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
