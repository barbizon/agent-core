"""The /config report: every setting, its value, where it came from, and what it means."""

import argparse
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from agent_core.config import (
    CONFIG_KEYS,
    DEFAULT_CONFIG_FILE,
    DEFAULT_ENV_FILE,
    USER_CONFIG_FILE,
    USER_CONFIG_LABEL,
    Settings,
)
from agent_core.debug import Tracer
from agent_core.permissions import PermissionPolicy
from agent_core.tools.files import WORKSPACE_ENV, code_root, workspace_root
from agent_core.ui import Style, pad, panel, screen_width, wrap


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
    main_values: Mapping[str, Any] | None = None,
    main_name: str = USER_CONFIG_LABEL,
) -> str:
    """Where a value came from: a flag, the folder file, the environment / .env, the main file, or the default."""
    env = os.environ if environ is None else environ
    if flag and flag_value not in (None, False):
        return f"flag {flag}"
    if key and file_values and file_values.get(key) is not None:
        return config_name
    env_name = CONFIG_KEYS.get(key or "") or (key if key == "OPENAI_API_KEY" else None)
    if env_name and env.get(env_name):
        return f".env / environment: {env_name}"
    if key and main_values and main_values.get(key) is not None:
        return main_name
    return "default"


def config_items(
    settings: Settings,
    args: argparse.Namespace,
    policy: PermissionPolicy,
    tracer: Tracer,
    file_values: Mapping[str, Any] | None = None,
    environ: Mapping[str, str] | None = None,
    debug_source: str | None = None,
    backend_source: str | None = None,
    main_values: Mapping[str, Any] | None = None,
    trace: bool | None = None,
    trace_source: str | None = None,
) -> list[ConfigItem]:
    """Collect every setting with its value, source and a plain-English meaning."""
    config_name = getattr(args, "config", DEFAULT_CONFIG_FILE)
    main_config = Path(getattr(args, "main_config", None) or USER_CONFIG_FILE).expanduser()
    main_name = _home_short(main_config)

    def src(key: str | None, flag: str | None = None, flag_value: object = None) -> str:
        """Source label for one setting."""
        return source_of(flag, flag_value, key, file_values, environ, config_name,
                         main_values, main_name)

    workspace_source = src("workspace")
    return [
        ConfigItem(
            "backend", settings.backend, backend_source or src("backend", "--backend", args.backend),
            "Which AI service answers. codex = your ChatGPT subscription (codex CLI); "
            "claude = your Claude subscription (claude CLI); openai = the paid OpenAI API. "
            "Switch during the session with /backend.",
        ),
        ConfigItem(
            "model", settings.model or "(backend default)",
            backend_source or src("model", "--model", args.model),
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
            "costs a step. Stops endless loops; 0 means no limit (Ctrl-C stops it).",
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
            "history_budget_tokens", f"{settings.history_budget_tokens:,}", src("history_budget_tokens"),
            "How big the conversation may get (estimated tokens) before Orwick shortens it: from "
            "80% old tool output is trimmed, from 90% the older part is summarized by the AI. "
            "/compact does it now. /history still shows everything.",
        ),
        ConfigItem(
            "tools", _describe_tools(settings), src("tools"),
            "Tools switched on or off by name. A tool not listed is on. A switched-off tool is "
            "never offered to the AI. The folder's file overrides the main settings per tool.",
        ),
        ConfigItem(
            "skills", _describe_switches(settings.skills), src("skills"),
            "Skills switched on or off by name. Built-in skills and yours are on unless listed "
            "as false; a project's skills are off unless listed as true.",
        ),
        ConfigItem(
            "claude_tools", _describe_own("claude", settings), src("claude_tools"),
            "Claude Code's own tools switched on when Claude answers. Safe ones (web search, "
            "reading web pages) are on unless you change it; [] switches all off. Dangerous ones "
            "(Read, Edit, Write, Bash...) work in your folder WITHOUT asking you.",
        ),
        ConfigItem(
            "codex_tools", _describe_own("codex", settings), src("codex_tools"),
            "Codex's own tools switched on when Codex answers: web_search (on unless you change "
            "it) and shell (runs commands and changes files without asking you). [] switches all "
            "off; if Codex uses one that's off anyway, Orwick ignores that reply.",
        ),
        ConfigItem(
            "history_warn_percent", f"{settings.history_warn_percent}%", src("history_warn_percent"),
            "When the conversation reaches this share of its budget, Orwick tells you (and again "
            "every 10% more), so you can /compact or /clear before it gets shortened on its own.",
        ),
        ConfigItem(
            "audit_log", str(settings.audit_log_path), src("audit_log"),
            "File where every step of every request is recorded. By default each session "
            "keeps its own, in .orwick/sessions/<id>/.",
        ),
        ConfigItem(
            "workspace", _home_short(settings.workspace.resolve() if settings.workspace else workspace_root()),
            workspace_source if workspace_source != "default" else "default (the folder you started in)",
            "The only folder the tools may use, and the only place commands may write. "
            "Paths outside it are refused, and the agent's own .orwick folder is invisible.",
        ),
        ConfigItem(
            "code_root", _code_root_text(), src("code_root") if settings.code_root else "default (found)",
            "The folder that holds all your repos (e.g. ~/Documents/go). Skills that follow a flow "
            "into other repos (service-map) may read it, never change it. Default: the folder "
            "around this one, when it holds other git repos.",
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
            "trace", "on" if (settings.trace if trace is None else trace) else "off",
            trace_source or src("trace"),
            "Show one short line per tool call (→ the call, ← its result) in the chat. Off: just how "
            "many calls were made, after the answer. Switch it during the session with /trace on|off.",
        ),
        ConfigItem(
            "config_file",
            f"{config_name} ({'found' if Path(config_name).is_file() else 'not found'})",
            source_of("--config", config_name if config_name != DEFAULT_CONFIG_FILE else None),
            "This folder's settings. They beat the main settings. Edit it while the agent "
            "runs: changes apply before your next message. Tools may not change this file.",
        ),
        ConfigItem(
            "main_config_file",
            f"{main_name} ({'found' if main_config.is_file() else 'not found'})",
            source_of("--main-config", main_config if main_config != USER_CONFIG_FILE else None),
            "Your main settings, used in every folder (all the settings above except "
            "workspace). Edit it while the agent runs: changes apply before your next message.",
        ),
        ConfigItem(
            "env_file",
            f"{args.env_file} ({'found' if Path(args.env_file).is_file() else 'not found'})",
            source_of("--env-file", args.env_file if args.env_file != DEFAULT_ENV_FILE else None),
            "File with secrets (the API key) and fallback values. Read once at startup.",
        ),
    ]


# Sections of the /config screen: icon + title, and the settings in each.
GROUPS = (
    ("🤖 AI", ("backend", "model", "openai_api_key", "request_timeout_seconds")),
    ("🔁 Agent loop", ("max_steps", "max_tool_result_chars", "history_budget_tokens", "history_warn_percent")),
    ("🔒 Safety", ("workspace", "code_root", "permissions", "tools", "skills", "claude_tools", "codex_tools", "audit_log")),
    ("🐞 Debug", ("debug", "trace")),
    ("📄 Files", ("main_config_file", "config_file", "env_file")),
)
NAME_COLUMN = 25


def source_color(source: str) -> str:
    """Flags stand out, the config file is cyan, the environment yellow, defaults gray."""
    if source.startswith("flag") or source.startswith("/"):
        return "magenta"
    if source.startswith(".env"):
        return "yellow"
    if source.startswith(("default", "built-in")):
        return "gray"
    return "cyan"


def describe_config(
    settings: Settings,
    args: argparse.Namespace,
    policy: PermissionPolicy,
    tracer: Tracer,
    file_values: Mapping[str, Any] | None = None,
    environ: Mapping[str, str] | None = None,
    width: int | None = None,
    debug_source: str | None = None,
    style: Style | None = None,
    backend_source: str | None = None,
    main_values: Mapping[str, Any] | None = None,
    trace: bool | None = None,
    trace_source: str | None = None,
) -> str:
    """The /config screen: settings grouped by topic, each with value, source and meaning.

    Each setting is one row ``name  value  ◂ source`` with its meaning below.
    """
    style = style or Style()
    width = width or screen_width()
    items = {i.name: i for i in config_items(settings, args, policy, tracer, file_values,
                                             environ, debug_source, backend_source, main_values,
                                             trace, trace_source)}
    out = [
        style("⚙  Configuration", "bold") + "   "
        + style(f"priority: flag › {DEFAULT_CONFIG_FILE} › .env / environment › {USER_CONFIG_LABEL} › default", "dim"),
        "",
    ]
    text_width = width - NAME_COLUMN - 4
    indent = " " * NAME_COLUMN
    for title, names in GROUPS:
        body = []
        for name in names:
            item = items[name]
            source = style("◂ " + item.source, source_color(item.source))
            row = f"{style(pad(name, NAME_COLUMN), 'bold')}{style(item.value, 'green')}"
            if NAME_COLUMN + len(item.value) + len(item.source) + 4 <= width - 4:
                body.append(f"{row}  {source}")
            else:  # too long for one line: the source goes on its own line
                body += [row, indent + source]
            for line in wrap(item.meaning, text_width):
                body.append(indent + style(line, "dim"))
            body.append("")
        body.pop()
        out += [panel(title, body, style), ""]
    out.append(f"✎  Edit {USER_CONFIG_LABEL} (every folder) or {DEFAULT_CONFIG_FILE} (this folder): "
               "changes apply before your next message.")
    return "\n".join(out)


def _describe_switches(switches: dict[str, bool]) -> str:
    on = sorted(n for n, v in switches.items() if v)
    off = sorted(n for n, v in switches.items() if not v)
    parts = ([f"on: {', '.join(on)}"] if on else []) + ([f"off: {', '.join(off)}"] if off else [])
    return " · ".join(parts) or "defaults"


def _describe_own(backend: str, settings: Settings) -> str:
    from agent_core.llm.own_tools import any_dangerous
    names = settings.own_tools(backend)
    if not names:
        return "all off"
    return ", ".join(names) + ("  ⚠ works without asking" if any_dangerous(backend, names) else "")


def _describe_tools(settings: Settings) -> str:
    off = sorted(settings.disabled_tools)
    return f"off: {', '.join(off)}" if off else "all on"


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


def _code_root_text() -> str:
    found = code_root()
    return _home_short(found) if found else "none (no folder of repos around this one)"
