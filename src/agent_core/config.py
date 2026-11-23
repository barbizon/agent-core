"""Settings: built-in defaults, overridden by your main settings, .env, the folder's config file, and flags.

Everything the agent keeps for one folder lives in ``.orwick/`` inside the
folder it is started in (config.json, .env, logs/, tools/). Your main
settings, used in every folder, live in ``~/.orwick/config.json``.

Priority (highest first):

    1. command-line flag            e.g. --max-steps 5
    2. folder config file           .orwick/config.json
    3. environment / .env file      e.g. AGENT_MAX_STEPS=10   (.orwick/.env)
    4. main config file             ~/.orwick/config.json   (same keys, except workspace)
    5. built-in default

A ``null`` value in the JSON file means "not set here", so the next level
decides. Secrets (OPENAI_API_KEY) are read only from the environment / .env,
never from the JSON file.
"""

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from dotenv import load_dotenv

from agent_core.errors import ConfigError

BACKENDS = ("codex", "claude", "openai")
DEFAULT_OPENAI_MODEL = "gpt-4.1-mini"
AGENT_NAME = "orwick"
AGENT_DIR = f".{AGENT_NAME}"                      # the agent's own folder
DEFAULT_CONFIG_FILE = f"{AGENT_DIR}/config.json"
DEFAULT_ENV_FILE = f"{AGENT_DIR}/.env"
USER_CONFIG_LABEL = f"~/{AGENT_DIR}/config.json"
USER_CONFIG_FILE = Path(USER_CONFIG_LABEL).expanduser()   # main settings, for every folder

# JSON key -> environment variable that can also set it.
CONFIG_KEYS: dict[str, str] = {
    "backend": "AGENT_BACKEND",
    "model": "AGENT_MODEL",
    "max_steps": "AGENT_MAX_STEPS",
    "request_timeout_seconds": "AGENT_REQUEST_TIMEOUT_SECONDS",
    "max_tool_result_chars": "AGENT_MAX_TOOL_RESULT_CHARS",
    "audit_log": "AGENT_AUDIT_LOG",
    "workspace": "AGENT_WORKSPACE",
    "code_root": "AGENT_CODE_ROOT",
    "debug": "AGENT_DEBUG",
    "trace": "AGENT_TRACE",
    "history_budget_tokens": "AGENT_HISTORY_BUDGET_TOKENS",
    "history_warn_percent": "AGENT_HISTORY_WARN_PERCENT",
    "tools": "",  # {"tool_name": true/false}: files only, no environment variable
    "claude_tools": "",  # Claude Code's own tools to switch on (list); null = the safe ones
    "codex_tools": "",   # Codex's own tools to switch on (list); null = the safe ones
    "mcpServers": "",    # MCP servers, in the usual format (see mcp.py)
    "skills": "",        # {"skill-name": true/false} (see skills.py)
}
TOOL_NAME = re.compile(r"^[A-Za-z0-9_-]{1,64}$")  # as the AI services allow; MCP tools: mcp__server__tool

# Settings that only make sense for one folder, so the main file may not set them.
FOLDER_ONLY_KEYS = {
    "workspace": "the workspace is the folder Orwick starts in; one shared workspace for every "
                 "folder would let every session work in the same place",
}


@dataclass(frozen=True)
class Settings:
    backend: str = "codex"          # "codex" (ChatGPT subscription) | "claude" (Claude subscription) | "openai" (API key)
    model: str | None = None        # None = backend default
    openai_api_key: str = ""        # only needed for backend="openai"; only from .env
    max_steps: int = 25
    audit_log_path: Path | None = None  # None = this session's folder (.orwick/sessions/<id>/audit.jsonl)
    request_timeout_seconds: float = 300.0  # a timeout restarts the claude process and re-sends the whole conversation
    max_tool_result_chars: int = 20_000
    workspace: Path | None = None   # None = the file tools' own default
    code_root: Path | None = None   # the folder of all repos (read only for skills like service-map); None = found
    debug: bool = False
    trace: bool = True                    # a title line per tool call in the chat (off: just a count)
    history_budget_tokens: int = 100_000  # conversation size where compacting starts (see compaction.py)
    history_warn_percent: int = 70        # tell you when the conversation reaches this share of the budget
    # Tools switched on or off by name; a tool not listed is on. Main settings and the
    # folder's file are merged, the folder winning per tool.
    tools: dict[str, bool] = field(default_factory=dict)

    # The AI's own built-in tools (llm/own_tools.py): None = the safe defaults, [] = all off.
    claude_tools: list[str] | None = None
    codex_tools: list[str] | None = None
    # MCP servers (mcp.py), main settings and the folder's file merged, the folder winning per server.
    mcp_servers: dict[str, Any] = field(default_factory=dict)
    # Skills switched on or off by name (skills.py); merged like tools.
    skills: dict[str, bool] = field(default_factory=dict)

    def own_tools(self, backend: str | None = None) -> list[str]:
        """The chosen backend's own tools that are switched on."""
        from agent_core.llm.own_tools import enabled
        backend = backend or self.backend
        return enabled(backend, {"claude": self.claude_tools, "codex": self.codex_tools}.get(backend))

    @property
    def disabled_tools(self) -> set[str]:
        return {name for name, on in self.tools.items() if on is False}

    def __post_init__(self) -> None:
        """Check that the settings make sense; raise ConfigError if not."""
        if self.backend not in BACKENDS:
            raise ConfigError(f"backend must be one of {BACKENDS}, got {self.backend!r}")
        if self.model is not None and (not isinstance(self.model, str) or not self.model):
            raise ConfigError("model must be a non-empty string or null")
        if self.backend == "openai" and not self.openai_api_key:
            raise ConfigError("OPENAI_API_KEY is not set (required for backend 'openai')")
        if not _is_int(self.max_steps) or self.max_steps < 0:
            raise ConfigError("max_steps must be a whole number: 0 for no limit, or at least 1")
        # Every limit: 0 means no limit.
        if not _is_number(self.request_timeout_seconds) or self.request_timeout_seconds < 0:
            raise ConfigError("request_timeout_seconds must be a number of seconds: 0 for no limit, or more")
        if not _is_int(self.max_tool_result_chars) or (0 < self.max_tool_result_chars < 100) \
                or self.max_tool_result_chars < 0:
            raise ConfigError("max_tool_result_chars must be a whole number: 0 for no limit, or at least 100")
        if not isinstance(self.debug, bool):
            raise ConfigError("debug must be true or false")
        if not isinstance(self.trace, bool):
            raise ConfigError("trace must be true or false")
        if not _is_int(self.history_budget_tokens) or (0 < self.history_budget_tokens < 8_000) \
                or self.history_budget_tokens < 0:
            raise ConfigError("history_budget_tokens must be a whole number: 0 for no limit, or at least 8000")
        if not _is_int(self.history_warn_percent) or not 0 <= self.history_warn_percent <= 100:
            raise ConfigError("history_warn_percent must be a whole number from 0 (never warn) to 100")
        if not isinstance(self.tools, dict) or not all(
                isinstance(name, str) and TOOL_NAME.match(name) and isinstance(on, bool)
                for name, on in self.tools.items()):
            raise ConfigError('tools must look like {"run_command": false, "read_file": true}')
        if not isinstance(self.skills, dict) or not all(
                isinstance(n, str) and isinstance(on, bool) for n, on in self.skills.items()):
            raise ConfigError('skills must look like {"feature-docs": true}')
        from agent_core.mcp import check_servers
        check_servers(self.mcp_servers)
        from agent_core.llm.own_tools import CATALOG
        for backend, chosen in (("claude", self.claude_tools), ("codex", self.codex_tools)):
            if chosen is None:
                continue
            if not isinstance(chosen, list) or not all(isinstance(n, str) for n in chosen):
                raise ConfigError(f"{backend}_tools must be a list of tool names, [] for none, or null")
            unknown = sorted(set(chosen) - set(CATALOG[backend]))
            if unknown:
                raise ConfigError(f"{backend}_tools: unknown tool(s) {unknown}; "
                                  f"known: {sorted(CATALOG[backend])}")


def read_config_file(path: str | Path | None, main: bool = False) -> dict[str, Any]:
    """Read a JSON config file; a missing file means no settings from it.

    ``main`` marks the main file (~/.orwick/config.json), which may not set
    folder-only settings such as ``workspace``.
    """
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
    if main:
        for key, reason in FOLDER_ONLY_KEYS.items():
            if data.get(key) is not None:
                raise ConfigError(f"{path}: {key} can't be set in the main settings ({reason}). "
                                  f"Set it in the folder's {DEFAULT_CONFIG_FILE} instead.")
    return data


def load_settings(
    env_file: str | Path | None = DEFAULT_ENV_FILE,
    backend: str | None = None,
    model: str | None = None,
    max_steps: int | None = None,
    config_file: str | Path | None = None,
    debug: bool | None = None,
    environ: Mapping[str, str] | None = None,
    user_config_file: str | Path | None = None,
) -> Settings:
    """Combine flags, the config files, .env and defaults into Settings (see module docstring).

    ``user_config_file`` is the main settings file (the CLI passes
    ~/.orwick/config.json); None means no main settings.

    ``environ`` lets the caller pass a fixed copy of the environment (the CLI
    passes a snapshot taken at startup); by default the live one is used.
    """
    if env_file is not None:
        load_dotenv(env_file)
    env = os.environ if environ is None else environ
    file_values = read_config_file(config_file)
    main_values = read_config_file(user_config_file, main=True)

    def pick(key: str, flag_value: Any = None) -> Any:
        """The value for one setting, by priority: flag, folder file, environment, main file."""
        if flag_value is not None:
            return flag_value
        if file_values.get(key) is not None:
            return file_values[key]
        raw = env.get(CONFIG_KEYS[key])
        if raw not in (None, ""):
            return raw
        return main_values.get(key)

    workspace = pick("workspace")
    code_root = pick("code_root")
    audit_log = pick("audit_log")
    return Settings(
        backend=pick("backend", backend) or Settings.backend,
        model=pick("model", model),
        openai_api_key=env.get("OPENAI_API_KEY", ""),
        max_steps=_as_int("max_steps", pick("max_steps", max_steps), Settings.max_steps),
        audit_log_path=Path(audit_log) if audit_log else None,
        request_timeout_seconds=_as_number(
            "request_timeout_seconds", pick("request_timeout_seconds"),
            Settings.request_timeout_seconds,
        ),
        max_tool_result_chars=_as_int(
            "max_tool_result_chars", pick("max_tool_result_chars"), Settings.max_tool_result_chars
        ),
        workspace=Path(workspace).expanduser() if workspace else None,
        code_root=Path(code_root).expanduser() if code_root else None,
        debug=_as_bool("debug", pick("debug", debug or None), Settings.debug),
        trace=_as_bool("trace", pick("trace"), Settings.trace),
        history_budget_tokens=_as_int("history_budget_tokens", pick("history_budget_tokens"),
                                      Settings.history_budget_tokens),
        history_warn_percent=_as_int("history_warn_percent", pick("history_warn_percent"),
                                     Settings.history_warn_percent),
        tools=_merged_tools(main_values.get("tools"), file_values.get("tools")),
        claude_tools=pick("claude_tools"),
        codex_tools=pick("codex_tools"),
        mcp_servers=_merged_servers(main_values.get("mcpServers"), file_values.get("mcpServers")),
        skills=_merged_tools(main_values.get("skills"), file_values.get("skills")),
    )


def _merged_servers(*layers: Any) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for layer in layers:
        if layer is None:
            continue
        if not isinstance(layer, dict):
            raise ConfigError('mcpServers must look like {"name": {"command": "...", "args": [...]}}')
        merged.update(layer)
    return merged


def _merged_tools(*layers: Any) -> dict[str, bool]:
    """Main settings first, then the folder's file: the later one wins per tool."""
    merged: dict[str, Any] = {}
    for layer in layers:
        if layer is None:
            continue
        if not isinstance(layer, dict):
            raise ConfigError('tools must look like {"run_command": false, "read_file": true}')
        merged.update(layer)
    return merged


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
