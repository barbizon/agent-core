"""Settings: built-in defaults, overridden by .env, a JSON config file, and flags.

Everything the agent keeps lives in ``.orvik/`` inside the folder it is
started in (config.json, .env, logs/, tools/).

Priority (highest first):

    1. command-line flag            e.g. --max-steps 5
    2. JSON config file             .orvik/config.json
    3. environment / .env file      e.g. AGENT_MAX_STEPS=10   (.orvik/.env)
    4. built-in default

A ``null`` value in the JSON file means "not set here", so the next level
decides. Secrets (OPENAI_API_KEY) are read only from the environment / .env,
never from the JSON file.
"""

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from dotenv import load_dotenv

from agent_core.errors import ConfigError

BACKENDS = ("codex", "openai")
DEFAULT_OPENAI_MODEL = "gpt-4.1-mini"
AGENT_NAME = "orvik"
AGENT_DIR = f".{AGENT_NAME}"                      # the agent's own folder
DEFAULT_CONFIG_FILE = f"{AGENT_DIR}/config.json"
DEFAULT_ENV_FILE = f"{AGENT_DIR}/.env"
DEFAULT_AUDIT_LOG = f"{AGENT_DIR}/logs/audit.jsonl"

# JSON key -> environment variable that can also set it.
CONFIG_KEYS: dict[str, str] = {
    "backend": "AGENT_BACKEND",
    "model": "AGENT_MODEL",
    "max_steps": "AGENT_MAX_STEPS",
    "request_timeout_seconds": "AGENT_REQUEST_TIMEOUT_SECONDS",
    "max_tool_result_chars": "AGENT_MAX_TOOL_RESULT_CHARS",
    "audit_log": "AGENT_AUDIT_LOG",
    "workspace": "AGENT_WORKSPACE",
    "debug": "AGENT_DEBUG",
}


@dataclass(frozen=True)
class Settings:
    backend: str = "codex"          # "codex" (ChatGPT subscription) | "openai" (API key)
    model: str | None = None        # None = backend default
    openai_api_key: str = ""        # only needed for backend="openai"; only from .env
    max_steps: int = 10
    audit_log_path: Path = Path(DEFAULT_AUDIT_LOG)
    request_timeout_seconds: float = 120.0
    max_tool_result_chars: int = 8000
    workspace: Path | None = None   # None = the file tools' own default
    debug: bool = False

    def __post_init__(self) -> None:
        """Check that the settings make sense; raise ConfigError if not."""
        if self.backend not in BACKENDS:
            raise ConfigError(f"backend must be one of {BACKENDS}, got {self.backend!r}")
        if self.model is not None and (not isinstance(self.model, str) or not self.model):
            raise ConfigError("model must be a non-empty string or null")
        if self.backend == "openai" and not self.openai_api_key:
            raise ConfigError("OPENAI_API_KEY is not set (required for backend 'openai')")
        if not _is_int(self.max_steps) or self.max_steps < 1:
            raise ConfigError("max_steps must be a whole number of at least 1")
        if not _is_number(self.request_timeout_seconds) or self.request_timeout_seconds <= 0:
            raise ConfigError("request_timeout_seconds must be a positive number")
        if not _is_int(self.max_tool_result_chars) or self.max_tool_result_chars < 100:
            raise ConfigError("max_tool_result_chars must be a whole number of at least 100")
        if not isinstance(self.debug, bool):
            raise ConfigError("debug must be true or false")


def read_config_file(path: str | Path | None) -> dict[str, Any]:
    """Read the JSON config file; a missing file means no settings from it."""
    if path is None:
        return {}
    path = Path(path)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{path}: invalid JSON ({exc.msg}, line {exc.lineno})") from exc
    except OSError as exc:
        raise ConfigError(f"{path}: cannot read ({exc})") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"{path}: must contain a JSON object {{...}}")
    unknown = sorted(set(data) - set(CONFIG_KEYS))
    if unknown:
        raise ConfigError(
            f"{path}: unknown setting(s) {unknown}; allowed: {sorted(CONFIG_KEYS)}"
        )
    return data


def load_settings(
    env_file: str | Path | None = DEFAULT_ENV_FILE,
    backend: str | None = None,
    model: str | None = None,
    max_steps: int | None = None,
    config_file: str | Path | None = None,
    debug: bool | None = None,
    environ: Mapping[str, str] | None = None,
) -> Settings:
    """Combine flags, the JSON file, .env and defaults into Settings (see module docstring).

    ``environ`` lets the caller pass a fixed copy of the environment (the CLI
    passes a snapshot taken at startup); by default the live one is used.
    """
    if env_file is not None:
        load_dotenv(env_file)
    env = os.environ if environ is None else environ
    file_values = read_config_file(config_file)

    def pick(key: str, flag_value: Any = None) -> Any:
        """The value for one setting, by priority: flag, JSON file, environment."""
        if flag_value is not None:
            return flag_value
        if file_values.get(key) is not None:
            return file_values[key]
        raw = env.get(CONFIG_KEYS[key])
        return raw if raw not in (None, "") else None

    workspace = pick("workspace")
    audit_log = pick("audit_log")
    return Settings(
        backend=pick("backend", backend) or Settings.backend,
        model=pick("model", model),
        openai_api_key=env.get("OPENAI_API_KEY", ""),
        max_steps=_as_int("max_steps", pick("max_steps", max_steps), Settings.max_steps),
        audit_log_path=Path(audit_log) if audit_log else Settings.audit_log_path,
        request_timeout_seconds=_as_number(
            "request_timeout_seconds", pick("request_timeout_seconds"),
            Settings.request_timeout_seconds,
        ),
        max_tool_result_chars=_as_int(
            "max_tool_result_chars", pick("max_tool_result_chars"), Settings.max_tool_result_chars
        ),
        workspace=Path(workspace).expanduser() if workspace else None,
        debug=_as_bool("debug", pick("debug", debug or None), Settings.debug),
    )


def _as_int(name: str, value: Any, default: int) -> int:
    """Accept an int from JSON or a numeric string from .env."""
    if value is None:
        return default
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError as exc:
            raise ConfigError(f"{name} must be a whole number, got {value!r}") from exc
    return value  # type checked in Settings


def _as_number(name: str, value: Any, default: float) -> float:
    """Accept a number from JSON or a numeric string from .env."""
    if value is None:
        return default
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError as exc:
            raise ConfigError(f"{name} must be a number, got {value!r}") from exc
    return value  # type checked in Settings


def _as_bool(name: str, value: Any, default: bool) -> bool:
    """Accept true/false from JSON or 1/0/true/false strings from .env."""
    if value is None:
        return default
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in ("1", "true", "yes", "on"):
            return True
        if lowered in ("0", "false", "no", "off"):
            return False
        raise ConfigError(f"{name} must be true or false, got {value!r}")
    return value  # type checked in Settings


def _is_int(value: Any) -> bool:
    """True for whole numbers (but not True/False)."""
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value: Any) -> bool:
    """True for ints and floats (but not True/False)."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)
