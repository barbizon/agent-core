"""Settings loaded from the environment (and an optional .env file)."""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from agent_core.errors import ConfigError

BACKENDS = ("codex", "openai")
DEFAULT_OPENAI_MODEL = "gpt-4.1-mini"


@dataclass(frozen=True)
class Settings:
    backend: str = "codex"          # "codex" (ChatGPT subscription) | "openai" (API key)
    model: str | None = None        # None = backend default
    openai_api_key: str = ""        # only needed for backend="openai"
    max_steps: int = 10
    audit_log_path: Path = Path("logs/audit.jsonl")
    request_timeout_seconds: float = 120.0
    max_tool_result_chars: int = 8000

    def __post_init__(self) -> None:
        """Check that the settings make sense; raise ConfigError if not."""
        if self.backend not in BACKENDS:
            raise ConfigError(f"AGENT_BACKEND must be one of {BACKENDS}, got {self.backend!r}")
        if self.backend == "openai" and not self.openai_api_key:
            raise ConfigError("OPENAI_API_KEY is not set (required for backend 'openai')")
        if self.max_steps < 1:
            raise ConfigError("max_steps must be at least 1")
        if self.request_timeout_seconds <= 0:
            raise ConfigError("request_timeout_seconds must be positive")
        if self.max_tool_result_chars < 100:
            raise ConfigError("max_tool_result_chars must be at least 100")


def _env_int(name: str, default: int) -> int:
    """Read a whole-number env variable, or return the default."""
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer") from exc


def _env_float(name: str, default: float) -> float:
    """Read a number env variable, or return the default."""
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be a number") from exc


def load_settings(
    env_file: str | Path | None = ".env",
    backend: str | None = None,
    model: str | None = None,
    max_steps: int | None = None,
) -> Settings:
    """Read settings from env; explicit arguments (CLI flags) win over env."""
    if env_file is not None:
        load_dotenv(env_file)

    return Settings(
        backend=backend or os.getenv("AGENT_BACKEND") or Settings.backend,
        model=model or os.getenv("AGENT_MODEL") or None,
        openai_api_key=os.getenv("OPENAI_API_KEY", ""),
        max_steps=max_steps if max_steps is not None else _env_int("AGENT_MAX_STEPS", Settings.max_steps),
        audit_log_path=Path(os.getenv("AGENT_AUDIT_LOG") or Settings.audit_log_path),
        request_timeout_seconds=_env_float(
            "AGENT_REQUEST_TIMEOUT_SECONDS", Settings.request_timeout_seconds
        ),
        max_tool_result_chars=_env_int(
            "AGENT_MAX_TOOL_RESULT_CHARS", Settings.max_tool_result_chars
        ),
    )
