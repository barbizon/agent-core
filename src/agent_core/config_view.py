"""The /config report: every setting, its value, where it came from, and what it means."""

import argparse
import os
import textwrap
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from agent_core.config import CONFIG_KEYS, DEFAULT_CONFIG_FILE, DEFAULT_ENV_FILE, Settings
from agent_core.debug import Tracer
from agent_core.permissions import PermissionPolicy
from agent_core.tools.files import WORKSPACE_ENV, workspace_root


@dataclass(frozen=True)
class ConfigItem:
    name: str
    value: str
    source: str
    meaning: str


def source_of(
    flag: str | None = None,
    flag_value: object = None,
    key: str | None = None,
    file_values: Mapping[str, Any] | None = None,
    environ: Mapping[str, str] | None = None,
    config_name: str = DEFAULT_CONFIG_FILE,
) -> str:
    """Where a value came from: a flag, the JSON file, the environment / .env, or the default."""
    env = os.environ if environ is None else environ
    if flag and flag_value not in (None, False):
        return f"flag {flag}"
    if key and file_values and file_values.get(key) is not None:
        return config_name
    env_name = CONFIG_KEYS.get(key or "") or (key if key == "OPENAI_API_KEY" else None)
    if env_name and env.get(env_name):
        return f".env / environment: {env_name}"
    return "default"


def config_items(
    settings: Settings,
    args: argparse.Namespace,
    policy: PermissionPolicy,
    tracer: Tracer,
    file_values: Mapping[str, Any] | None = None,
    environ: Mapping[str, str] | None = None,
    debug_source: str | None = None,
) -> list[ConfigItem]:
    """Collect every setting with its value, source and a plain-English meaning."""
    config_name = getattr(args, "config", DEFAULT_CONFIG_FILE)

    def src(key: str | None, flag: str | None = None, flag_value: object = None) -> str:
        """Source label for one setting."""
        return source_of(flag, flag_value, key, file_values, environ, config_name)

    workspace_source = src("workspace")
    return [
        ConfigItem(
            "backend", settings.backend, src("backend", "--backend", args.backend),
            "Which AI service answers. codex = your ChatGPT subscription through the codex "
            "CLI; openai = the paid OpenAI API.",
        ),
        ConfigItem(
            "model", settings.model or "(backend default)", src("model", "--model", args.model),
            "Which AI model to use. Empty means the backend picks its own default.",
        ),
        ConfigItem(
            "openai_api_key", "set" if settings.openai_api_key else "not set",
            src("OPENAI_API_KEY"),
            "Key for the paid OpenAI API. Needed only when backend is openai. Read only "
            "from .env, never from the JSON file. The value itself is never shown.",
        ),
        ConfigItem(
            "max_steps", str(settings.max_steps), src("max_steps", "--max-steps", args.max_steps),
            "How many times the agent may call the AI for one message. Every tool use "
            "costs a step. Stops endless loops.",
        ),
        ConfigItem(
            "request_timeout_seconds", f"{settings.request_timeout_seconds:g}",
            src("request_timeout_seconds"),
            "How long to wait for one AI reply before giving up.",
        ),
        ConfigItem(
            "max_tool_result_chars", f"{settings.max_tool_result_chars:,}",
            src("max_tool_result_chars"),
            "Tool output longer than this is cut before it is sent to the AI, so one big "
            "file can't flood its memory.",
        ),
        ConfigItem(
            "audit_log", str(settings.audit_log_path), src("audit_log"),
            "File where every step of every request is recorded.",
        ),
        ConfigItem(
            "workspace", _home_short(settings.workspace.resolve() if settings.workspace else workspace_root()),
            workspace_source if workspace_source != "default" else "default (the folder you started in)",
            "The only folder the file tools may use. Paths outside it are refused, and the "
            "agent's own .orvik folder is invisible to them.",
        ),
        ConfigItem(
            "permissions", _describe_policy(policy), "built-in",
            "What tools may do. Read tools run without asking; write and dangerous tools "
            "need your 'y' on every call.",
        ),
        ConfigItem(
            "debug", "on" if tracer.enabled else "off",
            debug_source or src("debug", "-d", args.debug),
            "Show each message's path through the program as numbered boxes. "
            "Switch it during the session with /debug on or /debug off.",
        ),
        ConfigItem(
            "config_file",
            f"{config_name} ({'found' if Path(config_name).is_file() else 'not found'})",
            source_of("--config", config_name if config_name != DEFAULT_CONFIG_FILE else None),
            "JSON file with the settings above. Edit it while the agent runs: changes "
            "apply before your next message. Tools may not change this file.",
        ),
        ConfigItem(
            "env_file",
            f"{args.env_file} ({'found' if Path(args.env_file).is_file() else 'not found'})",
            source_of("--env-file", args.env_file if args.env_file != DEFAULT_ENV_FILE else None),
            "File with secrets (the API key) and fallback values. Read once at startup.",
        ),
    ]


def describe_config(
    settings: Settings,
    args: argparse.Namespace,
    policy: PermissionPolicy,
    tracer: Tracer,
    file_values: Mapping[str, Any] | None = None,
    environ: Mapping[str, str] | None = None,
    width: int = 78,
    debug_source: str | None = None,
) -> str:
    """Format the /config report as readable text."""
    lines = [
        "Current configuration  (name = value   [where it comes from])",
        f"Priority: flag > {DEFAULT_CONFIG_FILE} > .env / environment > default",
        "",
    ]
    for item in config_items(settings, args, policy, tracer, file_values, environ, debug_source):
        lines.append(f"{item.name} = {item.value}   [{item.source}]")
        lines += textwrap.wrap(
            item.meaning, width=width, initial_indent="    ", subsequent_indent="    "
        )
        lines.append("")
    lines.append(f"Change a value in {DEFAULT_CONFIG_FILE}; it applies before your next message.")
    return "\n".join(lines)


def _describe_policy(policy: PermissionPolicy) -> str:
    """Summarize the permission policy in one line."""
    def level(allowed: bool, needs_approval: bool) -> str:
        """How one risk level behaves."""
        if not allowed:
            return "blocked"
        return "ask every time" if needs_approval else "run without asking"

    return (
        f"read: {level(policy.allow_read, False)} · "
        f"write: {level(policy.allow_write, True)} · "
        f"dangerous: {level(policy.allow_dangerous, True)}"
    )


def _home_short(path: Path) -> str:
    """Show paths under the home folder as ~/…"""
    home = Path.home()
    if path == home:
        return "~"
    return f"~/{path.relative_to(home)}" if path.is_relative_to(home) else str(path)
