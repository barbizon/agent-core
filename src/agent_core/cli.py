"""Interactive chat CLI."""

import argparse
import contextlib
import dataclasses
import functools
import json
import os
import re
import shutil
import signal
import sys
import threading
import time
from dataclasses import fields
from pathlib import Path
from collections.abc import Iterator
from typing import Any

from dotenv import load_dotenv

from agent_core import compaction, mcp, screens, sessions, skills, status_file
from agent_core.tools.knowledge import SESSION_DOCS_ENV
from agent_core.agent import DEFAULT_SYSTEM_PROMPT, Agent, AgentEvent
from agent_core.approvals import TerminalApprovalProvider
from agent_core.audit import JsonlAuditLogger
from agent_core.config import (
    AGENT_DIR,
    AGENT_NAME,
    BACKENDS,
    DEFAULT_CONFIG_FILE,
    DEFAULT_ENV_FILE,
    DEFAULT_OPENAI_MODEL,
    USER_CONFIG_FILE,
    USER_CONFIG_LABEL,
    Settings,
    load_settings,
    read_config_file,
)
from agent_core.config_view import describe_config
from agent_core.debug import Tracer
from agent_core.errors import ConfigError, ToolExecutionError, ModelError, StepLimitExceeded, ToolManifestError
from agent_core.execution.process_executor import ProcessExecutor
from agent_core.gateway import ToolGateway
from agent_core.llm import ChatModel
from agent_core.permissions import PermissionPolicy
from agent_core.tools import DEFAULT_TOOLS_DIR, TOOL_MODULE_PREFIX, USER_TOOLS_DIR, analytics, commands, load_all_tools, subagent
from agent_core.tools.registry import ToolRegistry
from agent_core.tools.files import CODE_ROOT_ENV, PROTECTED_PATHS_ENV, RELATED_DIRS_ENV, WORKSPACE_ENV, open_code_root, workspace_root
from agent_core.ui import Style
from agent_core.usage import CountingAuditLogger, SessionUsage, UsageStats, codex_last_known_limits

DEBUG_INTRO = (
    "Debug mode: each message is traced through the pipeline as numbered points.\n"
    "Colors: cyan = your input, blue = to the model, green = from the model, "
    "yellow = tools, gray = internal, magenta = answer, red = error."
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Read the command-line flags."""
    parser = argparse.ArgumentParser(prog=AGENT_NAME, description="Chat with Orwick, your AI agent.")
    parser.add_argument("--backend", choices=BACKENDS, help="Model backend (overrides the config file and AGENT_BACKEND)")
    parser.add_argument("--model", help="Model name (overrides the config file and AGENT_MODEL)")
    parser.add_argument("--max-steps", type=int, help="Model calls per message")
    parser.add_argument("-d", "--debug", action="store_true",
                        help="Print every step of the flow (model input/output, safety checks) in frames")
    parser.add_argument("--config", default=DEFAULT_CONFIG_FILE,
                        help=f"This folder's JSON settings file, reloaded when it changes (default {DEFAULT_CONFIG_FILE})")
    parser.add_argument("--main-config", default=str(USER_CONFIG_FILE),
                        help=f"Your main settings, used in every folder; reloaded when it changes "
                             f"(default {USER_CONFIG_LABEL})")
    session = parser.add_mutually_exclusive_group()
    session.add_argument("--session-id",
                         help="Name of the new session's folder, .orwick/sessions/<id>/ (the Orwick Mac app "
                              "sets it). Default: a new id from the date and time.")
    session.add_argument("--resume", metavar="ID",
                         help="Continue a saved session (see /sessions for ids)")
    parser.add_argument("--feature", metavar="KEY",
                        help="Link the session to a ticket, e.g. PROJ-16765 (same as /feature KEY)")
    parser.add_argument("--related", metavar="DIR", action="append", default=[],
                        help="Another project this session may read but never change, e.g. ../billing "
                             "(repeat for more; same as /related add). Kept with the session.")
    parser.add_argument("--list-tools", action="store_true",
                        help="Print every tool available here as JSON (built-in, yours, this folder's) and exit")
    parser.add_argument("--delete-session", metavar="ID",
                        help="Delete a saved session of this folder (everything in it), without asking, and exit. "
                             "In the chat, /sessions delete <id> asks first.")
    parser.add_argument("--create-analytics", metavar="TITLE",
                        help="Save an analytics (a skill of yours) from a title and --wanted, and exit. "
                             "Run it from the app's Analytics screen, or with /do <name> run.")
    parser.add_argument("--wanted", metavar="TEXT", help="With --create-analytics: what you want to get")
    parser.add_argument("--analytics-name", metavar="NAME",
                        help="With --create-analytics: the analytics to replace (keeps its name and reports)")
    parser.add_argument("--list-models", action="store_true",
                        help="Print the models each backend offers, as JSON, and exit")
    parser.add_argument("--env-file", default=DEFAULT_ENV_FILE,
                        help=f"File with secrets and fallback values (default {DEFAULT_ENV_FILE})")
    return parser.parse_args(argv)


def build_model(settings: Settings, tracer: Tracer, usage: UsageStats | None = None) -> ChatModel:
    """Create the model client for the chosen backend (codex, claude or openai)."""
    if settings.backend == "openai":
        from agent_core.llm.openai_model import OpenAIChatModel

        return OpenAIChatModel(
            api_key=settings.openai_api_key,
            model=settings.model or DEFAULT_OPENAI_MODEL,
            timeout_seconds=settings.request_timeout_seconds,
            tracer=tracer,
            usage=usage,
        )

    if settings.backend == "claude":
        from agent_core.llm.claude_model import ClaudeCLIModel

        return ClaudeCLIModel(
            model=settings.model, timeout_seconds=settings.request_timeout_seconds, tracer=tracer,
            usage=usage, own_tools=settings.own_tools("claude"), workspace=workspace_root(),
        )

    from agent_core.llm.codex_model import CodexCLIModel

    return CodexCLIModel(
        model=settings.model, timeout_seconds=settings.request_timeout_seconds, tracer=tracer,
        usage=usage, own_tools=settings.own_tools("codex"), workspace=workspace_root(),
    )


def model_label(settings: Settings) -> str:
    """The model name to show; for the default, which model that is, e.g. "gpt-6.1-sol (default)"."""
    if settings.model:
        return settings.model
    if settings.backend == "openai":
        return DEFAULT_OPENAI_MODEL
    from agent_core.models_catalog import default_model
    named = default_model(settings.backend)
    return f"{named} (default)" if named else f"{settings.backend} default"


def model_key(settings: Settings) -> tuple[Any, ...]:
    """Settings that need a new model client when they change."""
    return (settings.backend, settings.model, settings.request_timeout_seconds, settings.openai_api_key,
            tuple(settings.own_tools()))


def close_model(agent: Agent) -> None:
    """Stop a model's background process, if it has one (the claude backend does)."""
    close = getattr(agent.model, "close", None)
    if callable(close):
        close()


# MCP servers of this run: started on first use, closed when Orwick exits.
MCP_POOL = mcp.Pool()


def system_prompt(feature: str | None = None, skills_index: str = "") -> str:
    """Orwick's rules, the ticket this session works on, and the skills index."""
    parts = [DEFAULT_SYSTEM_PROMPT]
    if feature:
        parts.append(f"This session works on the feature in ticket {feature}. Look it up when you "
                     "need its details; the session's docs (read_session_doc) may already have a brief.")
    if skills_index:
        parts.append(skills_index)
    return "\n\n".join(parts)


def link_feature(agent: Agent, feature: str | None) -> str | None:
    """Link the session to a ticket (or none). Before the first message the system
    prompt changes; later a note for the next message is returned instead, because a
    new system prompt makes the claude backend start over and re-send (and pay for)
    the whole conversation."""
    agent.feature = feature
    if len(agent.messages) <= 1:
        agent.prompt_feature = feature
        agent.system_prompt = system_prompt(feature, skills.index(agent.skills_on))
        agent.messages[0] = {"role": "system", "content": agent.system_prompt}
        return None
    if feature:
        return (f"[Orwick] This session now works on the feature in ticket {feature}. Look it up "
                "when you need its details; the session's docs (read_session_doc) may already have a brief.")
    return "[Orwick] This session is no longer linked to a ticket."


HELPER_PROMPT = DEFAULT_SYSTEM_PROMPT + """

You are a HELPER agent: another agent handed you one task. Nobody can answer your
questions, so don't ask; do the task with the tools you have, then give a final
answer that is the report the other agent needs: findings with file:line, short,
no preamble (at most about 400 words). Read only what the task needs."""


def run_helper(settings: Settings, tracer: Tracer, gateway: ToolGateway, registry: ToolRegistry,
               session: SessionUsage, skills_index: str, task: str, allow_changes: bool,
               max_steps: int) -> str:
    """Run one helper agent (run_subagent) to its final answer: a fresh conversation,
    the same backend, Orwick's tools through the same checks; read-only ones unless
    ``allow_changes``. Its tokens count in this session; its tool calls show in the
    chat as "helper ▸ …"."""
    from agent_core.permissions import RiskLevel
    tools = [t for t in registry.list_tools() if t.name != "run_subagent"
             and (allow_changes or t.risk_level == RiskLevel.READ)]
    helper_registry = ToolRegistry(tools)
    helper_gateway = ToolGateway(
        registry=helper_registry, permission_policy=gateway.permission_policy,
        approval_provider=gateway.approval_provider, audit_logger=gateway.audit_logger,
        process_executor=gateway.process_executor, tracer=tracer,
    )
    model = build_model(settings, tracer, session.for_backend(settings.backend))
    helper = Agent(
        model=model, registry=helper_registry, gateway=helper_gateway,
        audit_logger=gateway.audit_logger, max_steps=max_steps,
        max_tool_result_chars=settings.max_tool_result_chars,
        on_event=functools.partial(helper_event, show_tools=not tracer.enabled, session=session),
        tracer=tracer, system_prompt="\n\n".join(p for p in (HELPER_PROMPT, skills_index) if p),
    )
    try:
        return helper.send(task) or "(the helper gave no report)"
    except StepLimitExceeded:
        return f"(the helper stopped at its {max_steps}-step limit without a report)"
    finally:
        close_model(helper)


def helper_event(event: AgentEvent, show_tools: bool = True, session: SessionUsage | None = None) -> None:
    """A helper's progress: like print_event, marked "helper", and never saving the main
    conversation mid-step (its run_subagent call is still waiting for an answer)."""
    data = event.data
    if event.kind == "model_request":
        _report(f"Helper thinking (step {data.get('step')})")
        LIVE.show(f"Helper thinking (step {data.get('step')})")
        return
    LIVE.clear()
    if event.kind == "tool_call":
        title = describe_call(data["name"], data.get("arguments"))
        if session is not None:
            session.add_call(f"Helper: {title}", data["name"])
        _report(f"Helper: {title}")
        if show_tools and TRACE["on"]:
            print(f"    helper ▸ {title}", flush=True)
        LIVE.show(f"Helper: {title}")
    elif event.kind == "tool_result":
        if session is not None:
            session.finish_call(data.get("name"), bool(data["ok"]))
        if show_tools and TRACE["on"] and not data["ok"]:
            print(f"      ✗ {data['error']['type']}: {_one_line(str(data['error']['message']), 120)}", flush=True)
        LIVE.working()


def build_agent(
    settings: Settings, policy: PermissionPolicy, tracer: Tracer, model: ChatModel | None = None,
    session: SessionUsage | None = None, feature: str | None = None,
) -> Agent:
    """Wire everything together: tools, safety gateway, audit log and model."""
    session = session or SessionUsage()
    loaded = load_all_tools(DEFAULT_TOOLS_DIR, USER_TOOLS_DIR)
    # MCP tools: only the switched-on ones, named mcp__server__tool (off unless switched on).
    # Starting an MCP server can take a while (uvx, a Jira login…). While a line is already
    # up ("Loading session …") it stays as it is; otherwise (a tool switched on mid-session)
    # the live line says which server is starting.
    started: list[str] = []

    def starting(server: str) -> None:
        if not LIVE.busy:
            started.append(server)
            LIVE.show(f"Starting MCP server {server}")

    mcp_tools, mcp_origins = MCP_POOL.tools(mcp.specs(settings.mcp_servers), settings.tools, starting)
    if started:
        LIVE.clear()
    # Skills: only an index goes to the AI; use_skill loads one when needed (skills.py).
    analytics.sync_addon()   # the review patterns you switched on, as a skill that extends code-check
    found = skills.discover()
    skills_on = skills.switched_on(found.skills, settings.skills)
    skills.export(skills_on)
    builtin = [t for t in loaded.registry.list_tools() if t.name != "use_skill" or skills_on]
    every_tool = ToolRegistry([*builtin, *mcp_tools])
    loaded.origins.update(mcp_origins)
    # Tools switched off in the settings are never offered to the AI, so they can't be called.
    off = settings.disabled_tools
    registry = ToolRegistry([tool for tool in every_tool.list_tools() if tool.name not in off])
    # Every audit event is also counted for /status on its way to the log file.
    # main() always sets the path (by default the session's folder); the fallback is for other callers.
    audit_path = settings.audit_log_path or Path(AGENT_DIR) / "logs" / "audit.jsonl"
    audit_logger = CountingAuditLogger(JsonlAuditLogger(audit_path), session.stats)
    gateway = ToolGateway(
        registry=registry,
        permission_policy=policy,
        approval_provider=QuietApprovals(TerminalApprovalProvider()),
        audit_logger=audit_logger,
        process_executor=ProcessExecutor(allowed_module_prefixes=(TOOL_MODULE_PREFIX,)),
        tracer=tracer,
    )
    agent = Agent(
        model=model or build_model(settings, tracer, session.for_backend(settings.backend)),
        registry=registry,
        gateway=gateway,
        audit_logger=audit_logger,
        max_steps=settings.max_steps,
        max_tool_result_chars=settings.max_tool_result_chars,
        # In debug mode the frames already show tool calls, so skip the short lines.
        on_event=functools.partial(print_event, show_tools=not tracer.enabled, session=session),
        tracer=tracer,
        system_prompt=system_prompt(feature, skills.index(skills_on)),
    )
    subagent.RUNNER = functools.partial(run_helper, settings, tracer, gateway, registry, session,
                                        skills.index(skills_on))
    agent.model_key = model_key(settings)
    agent.session = session
    agent.disabled_tools = [tool for tool in every_tool.list_tools() if tool.name in off]
    agent.tool_origins = loaded.origins
    agent.own_tools = settings.own_tools()
    agent.model_choice = settings.model
    agent.feature = feature
    agent.prompt_feature = feature   # the one named in the system prompt
    empty = [f"MCP server {server}: {', '.join('${' + v + '}' for v in names)} "
             f"{'is' if len(names) == 1 else 'are'} empty here, so it may start without its settings. "
             "Set them in the environment Orwick starts from (e.g. export them in ~/.zprofile)."
             for server, names in mcp.empty_variables(settings.mcp_servers).items()
             if any(on and name.startswith(mcp.tool_name(server, "")) for name, on in settings.tools.items())]
    agent.tool_notes = loaded.notes + empty + MCP_POOL.notes + found.notes
    agent.skills = found.skills
    agent.skills_on = skills_on
    return agent


# Whether each tool call gets a title line ("▸ Read app.py"): the "trace" setting, or
# /trace for this session. Off: just a count after the answer.
TRACE = {"on": True}
TRACE_PREVIEW = 160


def _one_line(text: str, limit: int = TRACE_PREVIEW) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


# Set by main(): saves status.json while an answer is being worked on, so the app's
# Info panel follows each step. Called as fn(activity, whole): whole=True also saves
# the conversation (only between steps, when every tool call has its answer).
PROGRESS: dict[str, Any] = {"fn": None}
def _clock(seconds: int) -> str:
    return f"{seconds}s" if seconds < 60 else f"{seconds // 60}m {seconds % 60:02d}s"


class LiveLine:
    """One line at the bottom that says what Orwick is doing while it works:
    a spinner, the step or tool, and how long it has taken ("⠹ Read Jira issue PROJ-1 · 34s").
    Redrawn in place by a small thread; wiped before anything else prints, paused
    while you answer an approval. Only on a real terminal."""

    FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
    EVERY = 0.12

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._text: str | None = None
        self._since = 0.0
        self._shown = False
        self._paused = 0
        self._frame = 0
        self._thread: threading.Thread | None = None
        self._run_since: float | None = None   # when the current answer started (begin/end)

    def begin(self, text: str = "Starting") -> None:
        """An answer starts: the line stays up, with the total time, until end()."""
        self._run_since = time.monotonic()
        self.show(text)

    def end(self) -> None:
        self._run_since = None
        self.clear()

    @property
    def busy(self) -> bool:
        """Between begin() and end(): a line is up for the whole of it."""
        return self._run_since is not None

    def working(self) -> None:
        """Between steps (a tool just finished, the next AI request is being sent)."""
        if self._run_since is not None:
            self.show("Working")

    def show(self, text: str) -> None:
        if not sys.stdout.isatty():
            return
        with self._lock:
            self._text, self._since = text, time.monotonic()
            self._draw()
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="orwick-live-line", daemon=True)
            self._thread.start()

    def clear(self) -> None:
        with self._lock:
            self._text = None
            self._wipe()

    @contextlib.contextmanager
    def paused(self) -> Iterator[None]:
        with self._lock:
            self._paused += 1
            self._wipe()
        try:
            yield
        finally:
            with self._lock:
                self._paused -= 1

    def _wipe(self) -> None:
        if self._shown:
            sys.stdout.write("\r\033[K")
            sys.stdout.flush()
            self._shown = False

    def _draw(self) -> None:
        if self._text is None or self._paused:
            return
        now = time.monotonic()
        seconds = int(now - self._since)
        total = int(now - self._run_since) if self._run_since is not None else seconds
        timing = f"{seconds}s" if total <= seconds + 1 else f"{seconds}s · {_clock(total)} in all"
        frame = self.FRAMES[self._frame % len(self.FRAMES)]
        width = max(20, shutil.get_terminal_size((80, 24)).columns - 8 - len(timing))
        text = _one_line(self._text, width)
        sys.stdout.write(f"\r\033[K\033[36m{frame}\033[0m \033[2m{text} · {timing}\033[0m")
        sys.stdout.flush()
        self._shown = True

    def _run(self) -> None:
        while True:
            time.sleep(self.EVERY)
            with self._lock:
                self._frame += 1
                self._draw()


LIVE = LiveLine()


class QuietApprovals:
    """The terminal approval prompt, with the live line out of its way."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner

    def approve(self, request: Any) -> bool:
        with LIVE.paused():
            return self.inner.approve(request)


def _report(activity: str, whole: bool = False) -> None:
    if PROGRESS["fn"] is not None:
        try:
            PROGRESS["fn"](activity, whole)
        except Exception:  # noqa: BLE001 - a status file must never stop an answer
            pass


def clear_screen() -> None:
    """Wipe the terminal and its scrollback (only a real terminal, never a pipe or log)."""
    if sys.stdout.isatty():
        sys.stdout.write("\033[H\033[2J\033[3J")
        sys.stdout.flush()


def describe_call(name: str, arguments: Any) -> str:
    """A short title for a tool call, e.g. "Read Jira issue PROJ-1" or "Read src/app.py"."""
    args = arguments if isinstance(arguments, dict) else {}

    def arg(*keys: str) -> str:
        for key in keys:
            value = args.get(key)
            if value not in (None, "", [], {}):
                return _one_line(str(value), 90)
        return ""

    short = name.rsplit("__", 1)[-1]
    path = arg("path")
    where = f" in {path}" if path and path != "." else ""
    lines = ""
    if isinstance(args.get("start_line"), int):
        first = args["start_line"]
        count = args.get("line_count")
        lines = f" (lines {first}–{first + count - 1})" if isinstance(count, int) else f" (from line {first})"
    titles = {
        "read_file": lambda: f"Read {path}{lines}",
        "write_file": lambda: f"Write {path}",
        "edit_file": lambda: f"Edit {path}",
        "list_folder": lambda: f"List folder {path or '.'}",
        "grep": lambda: f"Search for “{arg('pattern')}”{where}" + (f" ({arg('glob')} files)" if arg("glob") else ""),
        "run_command": lambda: f"Run: {arg('command')}",
        "use_skill": lambda: (f"Read {arg('file')} of skill {arg('name')}" if arg("file")
                              else f"Load skill {arg('name')}"),
        "run_subagent": lambda: f"Ask a helper: {_one_line(str(arg('task') or ''), 80)}",
        "read_session_doc": lambda: f"Read doc {arg('name')}" if arg("name") else "List session docs",
        "write_session_doc": lambda: f"Save doc {arg('name')}",
        "write_session_docs": lambda: "Save docs: " + ", ".join(args.get("docs") or {}),
        "read_service_map": lambda: (f"Read map {arg('service')} · {arg('name')}" if arg("name")
                                     else f"Open map {arg('service')}" if arg("service") else "List service maps"),
        "write_service_map": lambda: f"Save map {arg('service')}: " + ", ".join(args.get("docs") or {}),
        "save_analytics_report": lambda: f"Save the {arg('analytics')} report",
        "collect_review_comments": lambda: "Collect review comments from pull requests",
        "read_review_comments": lambda: "Read review comments to group",
        "save_review_patterns": lambda: f"Group {len(args.get('groups') or {})} review comments",
        "read_pull_request": lambda: ((f"Read {arg('file')} in " if arg("file") else "Read ")
                                      + f"pull request {arg('pr') or arg('branch')}"),
        "jira_get_issue": lambda: f"Read Jira issue {arg('issue_key')}",
        "jira_search": lambda: f"Search Jira: {arg('jql')}",
    }
    if short in titles:
        return titles[short]()
    title = short.replace("_", " ").capitalize()
    if name.startswith("mcp__"):
        title = f"{name.split('__')[1]}: {title}"
    first = next((v for v in args.values() if isinstance(v, (str, int, float)) and v != ""), None)
    return f"{title} {_one_line(str(first), 90)}" if first is not None else title


def print_event(event: AgentEvent, show_tools: bool = True, session: SessionUsage | None = None) -> None:
    """Show the progress of an answer: a "thinking" line while the AI works and (trace
    on) a title for each tool call, never its raw result (the app's Info panel lists
    the calls). Also keeps status.json current.
    ``show_tools`` is off in debug mode, where the frames already show tool calls.
    Each call is also listed in ``session`` (for the app's Info panel)."""
    data = event.data
    if event.kind == "model_request":
        _report(f"Thinking (step {data.get('step')})")
        LIVE.show(f"Thinking (step {data.get('step')})")
        return
    LIVE.clear()
    if event.kind == "step_done":
        _report(f"Step {data.get('step')} done", whole=True)
        LIVE.working()
        return
    if event.kind == "tool_call":
        title = describe_call(data["name"], data.get("arguments"))
        if session is not None:
            session.add_call(title, data["name"])
        _report(title)
        if show_tools and TRACE["on"]:
            print(f"  ▸ {title}", flush=True)
        LIVE.show(title)   # a slow tool (a Jira call can take minutes) still shows it's alive
        return
    if event.kind != "tool_result":
        return
    if session is not None:
        session.finish_call(data.get("name"), bool(data["ok"]))
    if show_tools and TRACE["on"] and not data["ok"]:   # a failure gets one short line
        print(f"    ✗ {data['error']['type']}: {_one_line(str(data['error']['message']), 120)}", flush=True)
    LIVE.working()


# What you typed at the prompt, for ↑/↓ (shared by all sessions, like a shell's history).
HISTORY_FILE = Path.home() / ".orwick" / "history"
HISTORY_LENGTH = 1000
_ANSI = re.compile(r"(\x1b\[[0-9;]*m)")


def setup_line_editing(path: Path = HISTORY_FILE) -> Any:
    """Turn on line editing for the prompt: arrows, ↑/↓ history, Ctrl-R, Ctrl-A/E…
    Returns the readline module, or None where there is none (pipes, tests)."""
    if not sys.stdin.isatty():
        return None
    try:
        import readline
    except ImportError:
        return None
    readline.set_auto_history(False)   # only prompt lines, never answers to y/N questions
    readline.set_history_length(HISTORY_LENGTH)
    try:
        readline.read_history_file(path)
    except OSError:
        pass
    return readline


def remember_line(line_editor: Any, line: str, path: Path = HISTORY_FILE) -> None:
    """Add a prompt line to the history (no repeats in a row) and save it (only you can read it)."""
    if line_editor is None or not line:
        return
    count = line_editor.get_current_history_length()
    if count and line_editor.get_history_item(count) == line:
        return
    line_editor.add_history(line)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        line_editor.write_history_file(path)
        os.chmod(path, 0o600)
    except OSError:
        pass


def readline_prompt(prompt: str) -> str:
    """Mark color codes as zero-width, so readline knows where the cursor is."""
    return _ANSI.sub("\x01\\1\x02", prompt)


def file_stamp(path: str | Path) -> tuple[int, int] | None:
    """A cheap fingerprint (modified time, size) to notice when a file changes."""
    try:
        stat = Path(path).stat()
    except OSError:
        return None
    return stat.st_mtime_ns, stat.st_size


def list_tools() -> int:
    """--list-tools: every tool available in this folder, as JSON (the Mac app uses it).

    Includes every tool of each configured MCP server (starting them briefly), so
    you can choose which to switch on.
    """
    try:
        loaded = load_all_tools(DEFAULT_TOOLS_DIR, USER_TOOLS_DIR)
    except ToolManifestError as exc:
        print(json.dumps({"error": str(exc)}))
        return 2
    servers: dict[str, Any] = {}
    try:
        settings = load_settings(env_file=None, config_file=DEFAULT_CONFIG_FILE,
                                 user_config_file=USER_CONFIG_FILE)
    except ConfigError as exc:
        servers["(settings)"] = {"error": str(exc)}
    else:
        pool = mcp.Pool()
        try:
            for name, spec in mcp.specs(settings.mcp_servers).items():
                try:
                    servers[name] = {"tools": [
                        {"name": mcp.tool_name(name, t["name"]), "risk": str(mcp.risk_of(t)),
                         "description": (t.get("description") or "").strip()[:300],
                         "on": bool(settings.tools.get(mcp.tool_name(name, t["name"])))}
                        for t in pool.catalog(spec)]}
                except (ToolExecutionError, OSError) as exc:
                    servers[name] = {"error": str(exc)}
        finally:
            pool.close()
    from agent_core.llm.own_tools import CATALOG, DEFAULTS
    own = {backend: [{"name": t.name, "risk": t.risk, "what": t.what, "default": t.name in DEFAULTS[backend]}
                     for t in tools.values()] for backend, tools in CATALOG.items()}
    from agent_core.models_catalog import available
    found = skills.discover()
    print(json.dumps({"format": 1, "notes": loaded.notes, "own_tools": own, "mcp": servers,
                      "models": available(), "skills": skills.describe(found.skills),
                      "skill_notes": found.notes, "tools": [
        {"name": tool.name, "risk": str(tool.risk_level), "description": tool.description,
         "origin": loaded.origins[tool.name]} for tool in loaded.registry.list_tools()]}))
    return 0


def export_for_tools(
    settings: Settings, config_path: str | Path, env_path: str | Path | None = None,
    main_config: str | Path | None = None,
) -> None:
    """Pass settings the tools read from the environment (also seen by the process worker)."""
    if settings.workspace is not None:
        os.environ[WORKSPACE_ENV] = str(settings.workspace.resolve())
    else:
        os.environ.pop(WORKSPACE_ENV, None)
    if settings.code_root is not None:
        os.environ[CODE_ROOT_ENV] = str(settings.code_root.resolve())
    else:
        os.environ.pop(CODE_ROOT_ENV, None)
    # The agent's own folder, config and secrets are invisible to its tools.
    protected = [AGENT_DIR, config_path] + ([env_path] if env_path else [])
    if main_config:  # the main settings and the folder that holds them
        protected += [main_config, Path(main_config).parent]
    os.environ[PROTECTED_PATHS_ENV] = os.pathsep.join(str(Path(p).resolve()) for p in protected)


def _home_short_path(path: str | Path) -> str:
    """Show a path under the home folder as ~/…"""
    path, home = Path(path).expanduser(), Path.home()
    return f"~/{path.relative_to(home)}" if path.is_relative_to(home) else str(path)


def settings_changes(old: Settings, new: Settings) -> list[str]:
    """Human-readable list of settings that differ, e.g. 'max_steps: 10 → 5'."""
    changes = []
    for field in fields(Settings):
        if field.name == "openai_api_key":
            if old.openai_api_key != new.openai_api_key:
                changes.append("openai_api_key: (changed)")  # never print the key
            continue
        before, after = getattr(old, field.name), getattr(new, field.name)
        if field.name == "tools":
            def on(values: dict[str, bool], name: str) -> bool:
                return values.get(name, not name.startswith(mcp.PREFIX))   # MCP tools: off unless on
            turned = [f"{name} {'on' if on(after, name) else 'off'}"
                      for name in sorted(set(before) | set(after))
                      if on(before, name) != on(after, name)]
            if turned:
                changes.append("tools: " + ", ".join(turned))
            continue
        if before != after:
            changes.append(f"{field.name}: {before} → {after}")
    return changes


def rebuild_agent(
    agent: Agent, settings: Settings, policy: PermissionPolicy, debug: bool
) -> tuple[Agent, Tracer]:
    """Build a fresh agent (new settings and/or debug state) that continues the same chat."""
    tracer = Tracer(enabled=debug, request_count=agent.tracer.request_count)
    model = None
    if getattr(agent, "model_key", None) == model_key(settings):
        # Same backend and model: keep the client (the claude process keeps its conversation).
        model = agent.model
        model.tracer = tracer
    else:
        close_model(agent)
    # The feature named in the system prompt stays as it was (a later change went in as a
    # note, see link_feature): a different system prompt would restart the claude process.
    new_agent = build_agent(settings, policy, tracer, model=model, session=agent.session,
                            feature=getattr(agent, "prompt_feature", getattr(agent, "feature", None)))
    new_agent.feature = getattr(agent, "feature", None)
    new_agent.messages = agent.messages
    new_agent.messages[0] = {"role": "system", "content": new_agent.system_prompt}  # skills may have changed
    return new_agent, tracer


def check_related(raw: list[str]) -> tuple[list[str], list[str]]:
    """Turn folder names (relative to the workspace, ~ or absolute) into the related folders
    a session may read: (accepted absolute paths, problems to show)."""
    root, home = workspace_root(), Path.home().resolve()
    accepted, problems = [], []
    for text in raw:
        folder = (root / Path(text).expanduser()).resolve()
        if not folder.is_dir():
            problems.append(f"{text}: no such folder")
        elif folder == root or folder.is_relative_to(root):
            problems.append(f"{text}: already inside this workspace")
        elif root.is_relative_to(folder) or folder in (home, Path("/")):
            problems.append(f"{text}: too wide (it contains this workspace or your home folder); "
                            "name the project folders one by one")
        elif str(folder) not in accepted:
            accepted.append(str(folder))
    return accepted, problems


def service_map_request(focus: str, name: str, related: list[str], session_id: str, code_root: bool,
                        rebuild: bool = False) -> str:
    """What the AI is asked for a service map: the service (its real folder: the AI may see
    another working folder), the map's name (chosen here when not given), the repos to read.
    Rebuild: scan again from scratch and replace the map (keeping what it was asked)."""
    from agent_core.tools.service_map import auto_name, map_question
    root = workspace_root()
    name = name or auto_name(session_id)
    asked = map_question(name) if rebuild and not focus else None
    return ("Use the service-map skill. Find the flows this service offers and map how they connect to the system"
            + (f", focus: {focus}" if focus else "") + f". The service: {root.name} (its folder: {root}). "
            + f"Map name: {name}. "
            + ("Rebuild it: scan the code again from scratch, don't reuse the map's current content, and "
               "write every doc again with rebuild set" + (f"; it was made for: {asked}" if asked else "") + ". "
               if rebuild else "")
            + ("The other repos are beside it, in ../ (read only): find yourself which ones take part. "
               if code_root else "")
            + ("Also read: " + related_line(related) + "." if related else ""))


def map_name(text: str) -> str:
    """A service map's name as the library keeps it: lowercase, words joined by -."""
    words = re.findall(r"[a-z0-9_]+", text.lower())
    return "-".join(words)[:60].strip("-_")


def parse_map(text: str) -> tuple[str, str, list[str], bool]:
    """`/map [flow] [--name NAME] [--rebuild] [--with folder…]` → (flow, name, folders, rebuild)."""
    rebuild = " --rebuild" in f" {text} "
    text = f" {text} ".replace(" --rebuild ", " ")
    focus: list[str] = []
    name: list[str] = []
    folders: list[str] = []
    part = focus
    for word in text.split():
        if word == "--name":
            part = name
        elif word == "--with":
            part = folders
        else:
            part.append(word)
    return " ".join(focus), map_name(" ".join(name)), folders, rebuild


def export_related(paths: list[str]) -> None:
    """Let the file tools and commands read these folders (never write them)."""
    if paths:
        os.environ[RELATED_DIRS_ENV] = os.pathsep.join(paths)
    else:
        os.environ.pop(RELATED_DIRS_ENV, None)


def related_line(paths: list[str]) -> str:
    """The related folders as the AI and you should name them: ../billing, ..."""
    root = workspace_root()
    return ", ".join(Path(os.path.relpath(p, root)).as_posix() for p in paths)


def main(argv: list[str] | None = None) -> int:
    """Run the interactive chat and return the exit code."""
    args = parse_args(argv)
    if args.list_tools:
        return list_tools()
    if args.delete_session:
        try:
            gone = sessions.delete_session(args.delete_session)
        except ConfigError as exc:
            print(f"Not deleted: {exc}", file=sys.stderr)
            return 2
        print(f"Deleted session {gone['id']} “{gone['title']}” ({gone['messages']} messages, "
              f"{len(gone['docs'])} docs).")
        return 0
    if args.create_analytics:
        from agent_core.errors import PermanentToolError
        try:
            made = analytics.create_analytics(args.create_analytics, args.wanted or "", args.analytics_name or "")
        except PermanentToolError as exc:
            print(f"Not saved: {exc}", file=sys.stderr)
            return 2
        print(json.dumps(made))
        return 0
    if args.list_models:
        from agent_core.models_catalog import available
        from agent_core.models_catalog import CLAUDE_ALIASES
        from agent_core.models_catalog import defaults
        print(json.dumps({"format": 1, "models": available(), "aliases": {"claude": CLAUDE_ALIASES},
                          "defaults": defaults()}))
        return 0

    # .env is read once, at startup. The snapshot keeps reloads independent of the
    # values the CLI itself exports for the tools (see export_for_tools).
    if args.env_file:
        load_dotenv(args.env_file)
    # Yours, for every folder: the .env beside the main settings (~/.orwick/.env). A value the
    # folder's .env or the environment already set is kept.
    load_dotenv(Path(args.main_config).expanduser().with_name(".env"))
    base_env = dict(os.environ)

    def load() -> tuple[Settings, dict[str, Any], dict[str, Any]]:
        """Read the settings and the raw values of the folder and main config files."""
        settings = load_settings(
            env_file=None, backend=args.backend, model=args.model, max_steps=args.max_steps,
            config_file=args.config, debug=args.debug, environ=base_env,
            user_config_file=args.main_config,
        )
        if settings.audit_log_path is None:  # by default each session keeps its own audit log
            settings = dataclasses.replace(settings, audit_log_path=session_folder.audit_log)
        # This session's own tool switches go on top of the settings' list.
        overrides, problem = sessions.read_tool_overrides(session_folder)
        if problem:
            with LIVE.paused():
                print(f"[tools] {problem}")
        tool_defaults.clear()
        tool_defaults.update(settings.tools)
        tool_overrides.clear()
        tool_overrides.update(overrides)
        if overrides:
            settings = dataclasses.replace(settings, tools={**settings.tools, **overrides})
        # This session's own skill switches (skills.json) on top of the settings' list.
        skill_defaults.clear()
        skill_defaults.update(settings.skills)
        skill_overrides.clear()
        skill_overrides.update(sessions.read_skill_overrides(session_folder))
        if skill_overrides:
            settings = dataclasses.replace(settings, skills={**settings.skills, **skill_overrides})
        return settings, read_config_file(args.config), read_config_file(args.main_config, main=True)

    tool_defaults: dict[str, bool] = {}   # the settings' tool list (main + folder file)
    tool_overrides: dict[str, bool] = {}  # this session's own switches (tools.json)
    skill_defaults: dict[str, bool] = {}  # the settings' skill switches (main + folder file)
    skill_overrides: dict[str, bool] = {}  # this session's own skill switches (skills.json)

    def stamps() -> tuple[Any, Any, Any]:
        """Fingerprints of both config files and the session's tools.json, to notice changes."""
        return file_stamp(args.config), file_stamp(args.main_config), file_stamp(session_folder.tools_file)

    # A closed terminal window (SIGHUP) or a polite kill (SIGTERM) ends Orwick like /exit,
    # so the session is saved and its lock given back.
    def stop(signum, frame):  # noqa: ARG001
        raise SystemExit(128 + signum)

    for signum in (signal.SIGHUP, signal.SIGTERM):
        signal.signal(signum, stop)

    # Loading can take seconds (MCP servers start, a long conversation is read), so the
    # live line says so until the banner is printed instead of leaving a bare cursor.
    LIVE.begin(f"Loading session {args.resume}" if args.resume else "Starting Orwick")
    # The session's folder: .orwick/sessions/<id>/ (conversation, meta, status, audit log).
    try:
        session_folder = sessions.open_session(args.resume or args.session_id, resume=bool(args.resume))
    except (ConfigError, OSError) as exc:
        LIVE.end()
        print(f"Session error: {exc}", file=sys.stderr)
        return 2
    feature = args.feature
    feature_note = None   # a feature change made mid-conversation, sent with the next message
    if feature and not sessions.FEATURE_KEY.match(feature):
        sessions.close(session_folder)
        LIVE.end()
        print(f"Session error: {feature!r} doesn't look like a ticket key (e.g. PROJ-16765)", file=sys.stderr)
        return 2
    if args.resume and not feature:
        feature = (sessions.read_meta(session_folder.path) or {}).get("feature")
    try:
        resumed = sessions.load_conversation(session_folder) if args.resume else []
        # Messages compacting summarized for the AI: shown again, not sent.
        earlier = sessions.load_earlier(session_folder) if args.resume else []
    except ConfigError as exc:
        sessions.release(session_folder)
        LIVE.end()
        print(f"Session error: {exc}", file=sys.stderr)
        return 2
    os.environ[SESSION_DOCS_ENV] = str(session_folder.path / "docs")  # for the session-doc tools
    codex_limits_cache = status_file.CodexLimitsCache()

    def write_status(folder: sessions.SessionFolder | None = None, continued_in: str | None = None,
                     state: str = "waiting", activity: str | None = None, whole: bool = True) -> None:
        """Save the session: its conversation and meta, and status.json (the /status numbers).
        A failure is reported, never fatal."""
        folder = folder or session_folder
        try:
            if whole:   # mid-step (a tool call without its answer yet) only status.json is written
                sessions.save(folder, agent.messages, settings.backend, model_label(settings), workspace_root(),
                              earlier=earlier, feature=feature)  # (on /clear and /resume the old session is saved before switching)
                sessions.save_stats(folder, agent.session.to_json())
            limits = codex_limits_cache.get() if settings.backend == "codex" else ([], None)
            status_file.write(folder.status_file, status_file.snapshot(
                agent, settings.backend, model_label(settings), workspace_root(), tracer.enabled, limits,
                session_id=folder.id, continued_in=continued_in,
                budget=settings.history_budget_tokens, warn_percent=settings.history_warn_percent,
                tool_defaults=tool_defaults, tool_overrides=tool_overrides, state=state,
                activity=activity, session_name=sessions.read_name(folder.path),
                skill_settings=skill_defaults, skill_overrides=skill_overrides))
        except (OSError, TypeError, ValueError) as exc:
            print(f"[session] Couldn't save {folder.path}: {exc}", file=sys.stderr)

    try:
        settings, file_values, main_values = load()
    except ConfigError as exc:
        LIVE.end()
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2
    config_stamp = stamps()
    export_for_tools(settings, args.config, args.env_file, args.main_config)
    # Related folders: the saved ones of this session, plus --related.
    related = sessions.read_related(session_folder)
    if args.related:
        added, problems = check_related(args.related)
        for problem in problems:
            print(f"[related] {problem}")
        related += [p for p in added if p not in related]
        sessions.write_related(session_folder, related)
    export_related(related)

    # As in Claude Code: read tools run, write and dangerous tools ask you on every call.
    policy = PermissionPolicy(allow_write=True, allow_dangerous=True)
    tracer = Tracer(enabled=settings.debug)
    try:
        agent = build_agent(settings, policy, tracer, feature=feature)
    except ToolManifestError as exc:
        sessions.close(session_folder)
        LIVE.end()
        print(f"Tool error: {exc}", file=sys.stderr)
        return 2
    agent.messages += resumed
    if args.resume:
        agent.session.restore(sessions.load_stats(session_folder))   # its counts, tokens, tool calls
    # Set by /debug on|off. While set, it beats the -d flag and the config file.
    debug_override: bool | None = None
    # Set by /trace on|off. While set, it beats the "trace" setting.
    trace_override: bool | None = None
    TRACE["on"] = settings.trace
    # Set by /backend. While set, it beats the --backend/--model flags and the config file.
    backend_override: tuple[str, str | None] | None = None

    def effective(loaded: Settings) -> Settings:
        """Apply the /backend choice of this session to freshly loaded settings."""
        if backend_override is None:
            return loaded
        return dataclasses.replace(loaded, backend=backend_override[0], model=backend_override[1])

    style = Style()
    main_note = "loaded" if config_stamp[1] else "not found"
    if not Path(AGENT_DIR).is_dir():
        config_line = f"No {AGENT_DIR}/ folder here: built-in tools, your main settings."
    else:
        config_note = "loaded" if config_stamp[0] else "not found"
        config_line = f"{args.config} ({config_note}) · reloads automatically"
    config_line += f"\n   Main settings: {_home_short_path(args.main_config)} ({main_note})"
    config_line += f"\n   Session: {session_folder.path}"
    if resumed:
        title = sessions.title_of(resumed)
        config_line += f"\n   Resumed: “{title}” · {sum(m['role'] == 'user' for m in resumed)} messages"
    mcp_count = sum(t.name.startswith(mcp.PREFIX) for t in agent.registry.list_tools())
    LIVE.end()
    print(screens.banner(AGENT_NAME, settings.backend, model_label(settings), len(agent.registry) - mcp_count,
                         tracer.enabled, Path.cwd(), config_line, style, own_tools=settings.own_tools(),
                         mcp_count=mcp_count))
    if tracer.enabled:
        print(DEBUG_INTRO)
    for note in getattr(agent, "tool_notes", []):
        print(f"[tools] {note}")
    if resumed:  # come back to the conversation as you left it
        print("\n" + screens.conversation_screen(resumed, style, earlier))

    def summarize(request: list[dict[str, Any]]) -> str:
        """Ask the current backend for a summary, in a client of its own (closed after)."""
        model = build_model(settings, Tracer(enabled=False), agent.session.for_backend(settings.backend))
        LIVE.show("Shortening the conversation: the AI summarizes the older part")
        try:
            return model.complete(request, []).text or ""
        finally:
            LIVE.clear()
            close = getattr(model, "close", None)
            if close:
                close()

    def reload_if_changed() -> None:
        """Live reload: if a settings file or this session's tools.json changed,
        rebuild the agent but keep the chat."""
        nonlocal settings, file_values, main_values, agent, tracer, config_stamp
        stamp = stamps()
        if stamp == config_stamp:
            return
        config_stamp = stamp
        try:
            new_settings, new_file_values, new_main_values = load()
            new_settings = effective(new_settings)
        except ConfigError as exc:
            print(f"[config] {exc}\n[config] Keeping the previous settings.")
        else:
            changes = settings_changes(settings, new_settings)
            file_values, main_values = new_file_values, new_main_values
            if changes:
                was_debug = tracer.enabled
                settings = new_settings
                export_for_tools(settings, args.config, args.env_file, args.main_config)
                debug = settings.debug if debug_override is None else debug_override
                agent, tracer = rebuild_agent(agent, settings, policy, debug)
                print("[config] Reloaded: " + "; ".join(changes))
                if backend_override is not None and (new_file_values.get("backend")
                                                     or new_main_values.get("backend")):
                    print("[config] Note: the backend is set by /backend in this session, "
                          "so the files' backend and model are ignored.")
                if debug_override is not None and any(c.startswith("debug:") for c in changes):
                    print("[config] Note: debug is set by /debug in this session, "
                          "so the file's debug value is ignored.")
                if tracer.enabled and not was_debug:
                    print(DEBUG_INTRO)
                TRACE["on"] = settings.trace if trace_override is None else trace_override

    def apply_session_skills() -> str | None:
        """Pick up this session's skill switches (skills.json: the app's Info panel or
        /skills on|off). Before anything was said the system prompt changes; later the AI
        gets a note with the next message instead, so the conversation isn't sent again.
        Returns that note, or None."""
        nonlocal settings, agent, tracer
        fresh = sessions.read_skill_overrides(session_folder)
        if fresh == skill_overrides:
            return None
        skill_overrides.clear()
        skill_overrides.update(fresh)
        settings = dataclasses.replace(settings, skills={**skill_defaults, **fresh})
        before = list(agent.skills_on)
        after = skills.switched_on(agent.skills, settings.skills)
        if [s.name for s in after] == [s.name for s in before]:
            return None
        if after and agent.registry.get("use_skill") is None:
            # No skill was on, so use_skill isn't offered yet: the tool list must change.
            agent, tracer = rebuild_agent(agent, settings, policy, tracer.enabled)
            return None
        agent.skills_on = after
        skills.export(after)                       # use_skill accepts exactly these now
        if len(agent.messages) <= 1:
            agent.system_prompt = system_prompt(agent.prompt_feature, skills.index(after))
            agent.messages[0] = {"role": "system", "content": agent.system_prompt}
            return None
        return skills.change_note(before, after)

    fill_warning = compaction.FillWarning()

    def warn_if_full() -> None:
        """Tell you when the conversation crosses the warning percent (and every 10% after)."""
        level = compaction.fill(agent.messages, settings.history_budget_tokens)
        if fill_warning.check(level.percent, settings.history_warn_percent):
            print(screens.fill_warning(level.percent, level.tokens, level.budget, style))

    if resumed:
        warn_if_full()

    def compact_history(force: bool = False) -> None:
        """Shorten the conversation sent to the AI when it gets big (or now, with force)."""
        result = compaction.compact(agent.messages, settings.history_budget_tokens, summarize, force=force)
        if result.error:
            print(f"[compact] Couldn't summarize ({result.error}). "
                  + ("Old tool output was trimmed instead." if result.trimmed else "Nothing changed."))
        if not result.changed:
            if result.not_smaller:
                print("[compact] The conversation is already short: a summary wouldn't make it smaller.")
            elif force and not result.error:
                print("[compact] Nothing to compact yet: only your latest messages are in the conversation.")
            return
        agent.messages = result.messages
        earlier.extend(result.removed)
        done = []
        if result.trimmed:
            done.append(f"trimmed {result.trimmed} old tool {'result' if result.trimmed == 1 else 'results'}")
        if result.summarized:
            done.append(f"summarized {result.summarized} earlier messages")
        print(f"[compact] {' and '.join(done).capitalize()}: ~{result.before_tokens:,} → "
              f"~{result.after_tokens:,} tokens. /history still shows everything.")

    line_editor = setup_line_editing()
    try:
        while True:
            write_status()
            try:
                print()
                line = input(readline_prompt(screens.user_prompt(style)) if line_editor
                             else screens.user_prompt(style)).strip()
                remember_line(line_editor, line)
            except (EOFError, KeyboardInterrupt):
                print()
                return 0

            reload_if_changed()  # a settings file (or this session's tools.json) changed
            steps_for_this = None   # a skill action may ask for more steps (/do)

            if not line:
                continue
            if line in ("/exit", "/quit"):
                return 0
            if line == "/help":
                print(screens.help_screen(args.config, style))
                continue
            if line == "/tools":
                print(screens.tools_screen(agent.registry, style, getattr(agent, "disabled_tools", []),
                                           set(tool_overrides)))
                print(screens.own_tools_screen(settings.backend, settings.own_tools(), style))
                continue
            if line.startswith("/tools "):
                words = line.split()[1:]
                every = {tool.name for tool in [*agent.registry.list_tools(), *agent.disabled_tools]}
                if words == ["reset"]:
                    sessions.write_tool_overrides(session_folder, {})
                    reload_if_changed()
                    print("This session uses the tool list from your settings again.")
                elif len(words) == 2 and words[0] in ("on", "off"):
                    name, on = words[1], words[0] == "on"
                    is_mcp = any(name.startswith(mcp.tool_name(server, "")) for server in settings.mcp_servers)
                    if name not in every and not is_mcp:
                        print(f"No tool named {name} here. /tools lists them.")
                        continue
                    changed = dict(tool_overrides)
                    if on == tool_defaults.get(name, not is_mcp):   # MCP tools are off unless switched on
                        changed.pop(name, None)      # same as the settings: no override needed
                    else:
                        changed[name] = on
                    sessions.write_tool_overrides(session_folder, changed)
                    reload_if_changed()
                    print(f"{name} is {'on' if on else 'off'} for this session. "
                          "The tool list in your settings is unchanged.")
                else:
                    print("Usage: /tools, /tools on <name>, /tools off <name>, /tools reset")
                continue
            if line == "/policy":
                print(screens.policy_screen(policy, style))
                continue
            if line == "/config":
                debug_source = "/debug command" if debug_override is not None else None
                backend_source = "/backend command" if backend_override is not None else None
                print(describe_config(settings, args, policy, tracer, file_values, base_env,
                                      debug_source=debug_source, backend_source=backend_source,
                                      style=style, main_values=main_values, trace=TRACE["on"],
                                      trace_source="/trace command" if trace_override is not None else None))
                continue
            if line == "/trace" or line.startswith("/trace "):
                choice = line.split()[1:]
                if not choice:
                    print(f"Tool lines are {'on' if TRACE['on'] else 'off'}. Use /trace on or /trace off.")
                elif choice in (["on"], ["off"]):
                    trace_override = TRACE["on"] = choice[0] == "on"
                    print("Tool calls are shown again." if TRACE["on"] else
                          "Tool calls are hidden; after each answer you'll see how many were made.")
                else:
                    print("Usage: /trace, /trace on, /trace off")
                continue
            if line == "/debug" or line.startswith("/debug "):
                choice = line.split()[1:]
                if not choice:
                    print(f"Debug is {'on' if tracer.enabled else 'off'}. "
                          "Use /debug on or /debug off to switch it.")
                elif choice[0] in ("on", "off") and len(choice) == 1:
                    debug_override = choice[0] == "on"
                    if debug_override == tracer.enabled:
                        print(f"Debug is already {choice[0]}.")
                    else:
                        agent, tracer = rebuild_agent(agent, settings, policy, debug_override)
                        print(f"Debug is now {choice[0]}. The conversation is kept.")
                        if tracer.enabled:
                            print(DEBUG_INTRO)
                else:
                    print("Usage: /debug, /debug on, /debug off")
                continue
            if line == "/backend" or line.startswith("/backend "):
                choice = line.split()[1:]
                if not choice:
                    print(screens.backend_screen(settings.backend, model_label(settings), style))
                elif choice[0] in BACKENDS and len(choice) <= 2:
                    try:
                        candidate = dataclasses.replace(
                            settings, backend=choice[0], model=choice[1] if len(choice) == 2 else None)
                    except ConfigError as exc:
                        print(f"[backend] {exc}. Still using {settings.backend}.")
                        continue
                    backend_override = (candidate.backend, candidate.model)
                    settings = candidate
                    agent, tracer = rebuild_agent(agent, settings, policy, tracer.enabled)
                    print(f"Now using {settings.backend} · {model_label(settings)}. "
                          "The conversation is kept.")
                else:
                    print(f"Usage: /backend, /backend {'|'.join(BACKENDS)} [model]")
                continue
            if line == "/status":
                codex_limits = codex_last_known_limits() if settings.backend == "codex" else ([], None)
                print(screens.status_screen(agent, settings.backend, model_label(settings),
                                            codex_limits, style, budget=settings.history_budget_tokens,
                                            warn_percent=settings.history_warn_percent))
                continue
            if line == "/clear" or line == "/resume" or line.startswith("/resume "):
                # /clear: a new session in this folder, as if Orwick were restarted.
                # /resume <id>: continue a saved session instead.
                target = line.split()[1:] if line != "/clear" else None
                if target is not None and len(target) != 1:
                    print("Usage: /resume <id>   (see /sessions)")
                    continue
                if target and target[0] == session_folder.id:
                    print("That's this session.")
                    continue
                previous = session_folder
                next_folder = None
                try:
                    next_folder = sessions.open_session(target[0] if target else None, resume=bool(target))
                    history = sessions.load_conversation(next_folder) if target else []
                except (ConfigError, OSError) as exc:
                    if next_folder is not None:  # opened but unreadable: let it go again
                        sessions.release(next_folder)
                    print(f"[session] {exc}")
                    continue
                write_status(folder=previous, continued_in=next_folder.id)  # its last state
                earlier.clear()
                if target:
                    try:
                        earlier.extend(sessions.load_earlier(next_folder))
                    except ConfigError:
                        pass
                sessions.release(previous)
                session_folder = next_folder
                os.environ[SESSION_DOCS_ENV] = str(session_folder.path / "docs")
                close_model(agent)
                debug_override = backend_override = trace_override = None
                try:
                    settings, file_values, main_values = load()
                except ConfigError as exc:
                    print(f"[config] {exc}\n[config] Keeping the previous settings.")
                    if settings.audit_log_path == previous.audit_log:
                        settings = dataclasses.replace(settings, audit_log_path=session_folder.audit_log)
                config_stamp = stamps()
                export_for_tools(settings, args.config, args.env_file, args.main_config)
                related = sessions.read_related(session_folder) if target else []
                export_related(related)
                tracer = Tracer(enabled=settings.debug)
                TRACE["on"] = settings.trace
                feature = (sessions.read_meta(session_folder.path) or {}).get("feature") if target else None
                agent = build_agent(settings, policy, tracer, feature=feature)
                feature_note = None
                agent.messages += history
                clear_screen()   # the old conversation's lines go, scrollback included
                if target:
                    agent.session.restore(sessions.load_stats(session_folder))
                    print(f"Resumed “{sessions.title_of(history)}” · "
                          f"{sum(m['role'] == 'user' for m in history)} messages · {session_folder.path}\n"
                          f"Files may have changed since; ask Orwick to read them again if it matters.")
                    print("\n" + screens.conversation_screen(history, style, earlier))
                else:
                    print(f"New session: {session_folder.path}\n"
                          f"The conversation, counts and /backend or /debug choices start fresh "
                          f"({settings.backend} · {model_label(settings)}). "
                          f"The previous session is saved in {previous.path}.")
                fill_warning.last = None
                warn_if_full()
                continue
            if line == "/feature" or line.startswith("/feature "):
                words = line.split()[1:]
                if not words:
                    print(f"This session works on {feature}." if feature else
                          "This session isn't linked to a feature. Link it with /feature PROJ-16765.")
                    continue
                if words == ["off"]:
                    feature = None
                elif len(words) == 1 and sessions.FEATURE_KEY.match(words[0].upper()):
                    feature = words[0].upper()
                else:
                    print("Usage: /feature, /feature <ticket key>, /feature off")
                    continue
                feature_note = link_feature(agent, feature)
                write_status()
                print(f"This session now works on {feature}." if feature else "The feature link is removed.")
                continue
            if line == "/related" or line.startswith("/related "):
                words = line.split()[1:]
                if words[:1] == ["add"] and words[1:]:
                    added, problems = check_related(words[1:])
                    for problem in problems:
                        print(f"[related] {problem}")
                    related += [p for p in added if p not in related]
                elif words[:1] == ["remove"] and words[1:]:
                    gone, _ = check_related(words[1:])
                    related = [p for p in related if p not in gone]
                elif words == ["clear"]:
                    related = []
                elif words:
                    print("Usage: /related [add <folder>… | remove <folder>… | clear]")
                    continue
                sessions.write_related(session_folder, related)
                export_related(related)
                print("Related folders (read only): " + (related_line(related) or "none"))
                continue

            if line == "/map" or line.startswith("/map "):
                # The service map: optional --with <folders> adds related folders first.
                focus, name, folders, rebuild = parse_map(line[len("/map"):])
                if folders:
                    added, problems = check_related(folders)
                    for problem in problems:
                        print(f"[related] {problem}")
                    related += [p for p in added if p not in related]
                    sessions.write_related(session_folder, related)
                    export_related(related)
                if not any(s.name == "service-map" for s in agent.skills_on):
                    print("The service-map skill is off. Switch it on (/skills on service-map) and try again.")
                    continue
                # The services beside this one are read (never changed) so the map finds the
                # dependencies itself; --with adds folders elsewhere.
                export_related(related)
                around = open_code_root()
                line = service_map_request(focus, name, related, session_folder.id, bool(around), rebuild)
                steps_for_this = None if settings.max_steps == 0 else max(80, settings.max_steps)
                print(f"▶ Service map: {line}")

            if line == "/skills":
                print(screens.skills_screen(agent.skills, agent.skills_on, style))
                continue
            if line.startswith("/skills "):
                words = line.split()[1:]
                names = {s.name for s in agent.skills}
                if len(words) != 2 or words[0] not in ("on", "off") or words[1] not in names:
                    print("Usage: /skills on|off <name>   (names are in /skills)")
                    continue
                on = words[0] == "on"
                changed = dict(skill_overrides)
                if on == skill_defaults.get(words[1], skills.default_on(
                        next(s for s in agent.skills if s.name == words[1]))):
                    changed.pop(words[1], None)        # the same as the settings: no switch needed
                else:
                    changed[words[1]] = on
                sessions.write_skill_overrides(session_folder, changed)
                note = apply_session_skills()
                if note:
                    feature_note = "\n\n".join(n for n in (feature_note, note) if n)
                write_status()
                print(f"{words[1]} is {'on' if on else 'off'} for this session"
                      + (" (the AI is told with your next message)." if note else "."))
                continue
            if line == "/history":
                print(screens.conversation_screen(agent.messages, style, earlier))
                continue
            if line == "/compact":
                compact_history(force=True)
                warn_if_full()  # says nothing if it's now below the warning point
                continue
            if line == "/name" or line.startswith("/name "):
                text = line[len("/name"):].strip()
                if not text:
                    current = sessions.read_name(session_folder.path)
                    print(f"This session is called “{current}”." if current else
                          "This session has no name; its first message is shown instead. "
                          "Name it with /name <text>.")
                    continue
                try:
                    saved_name = sessions.write_name(session_folder.path, "" if text == "off" else text)
                except OSError as exc:
                    print(f"Couldn't save the name: {exc}")
                    continue
                write_status()
                print(f"This session is now called “{saved_name}”." if saved_name else "The name is removed.")
                continue
            if line == "/sessions":
                print(screens.sessions_screen(sessions.list_sessions(), session_folder.id, style))
                continue
            if line.startswith("/sessions delete"):
                words = line.split()[2:]
                if len(words) != 1:
                    print("Usage: /sessions delete <id>   (ids are in /sessions)")
                    continue
                target_id = words[0]
                if target_id == session_folder.id:
                    print("That's this session. Start a new one with /clear first, then delete it.")
                    continue
                meta = sessions.read_meta(sessions.SESSIONS_DIR / target_id) if sessions.SESSION_ID.match(target_id) else None
                if meta is None:
                    print(f"No saved session {target_id} here. /sessions lists them.")
                    continue
                docs = sorted(p.stem for p in (sessions.SESSIONS_DIR / target_id / "docs").glob("*.md"))
                print(f"Session {target_id}: “{sessions.display_title(sessions.SESSIONS_DIR / target_id, meta)}” · {meta.get('messages', 0)} messages"
                      + (f" · docs: {', '.join(docs)}" if docs else "")
                      + (f" · {meta['feature']}" if meta.get("feature") else ""))
                try:
                    answer_text = input("Delete it with its conversation, docs and logs? This can't be undone. [y/N]: ")
                except (EOFError, KeyboardInterrupt):
                    answer_text = ""
                if answer_text.strip().lower() not in ("y", "yes"):
                    print("Kept.")
                    continue
                try:
                    gone = sessions.delete_session(target_id)
                except ConfigError as exc:
                    print(f"Not deleted: {exc}")
                    continue
                print(f"Deleted session {gone['id']}.")
                continue
            if line == "/reset":
                agent.reset()
                feature_note = link_feature(agent, agent.feature)   # empty now: back into the system prompt
                print("Conversation cleared.")
                continue

            if line == "/do" or line.startswith("/do "):
                # A skill's UI action (its ui.json): ask for its inputs here, then send its prompt.
                words = line.split()[1:]
                analytics.sync_addon()   # patterns switched on or off in the app since the last run
                # A skill just ticked in the app counts at once (the AI is told with this message).
                skills_note = apply_session_skills()
                if skills_note:
                    feature_note = "\n\n".join(n for n in (feature_note, skills_note) if n)
                offered = {(s.name, a.id): a for s in agent.skills_on for a in s.actions}
                action = offered.get(tuple(words)) if len(words) == 2 else None
                if action is None:
                    print(screens.actions_screen(agent.skills_on, style))
                    continue
                values: dict[str, str] = {}
                for field in action.inputs:
                    if field.unless and values[field.unless] == "yes":
                        values[field.name] = field.default
                        continue
                    for _ in range(3):
                        try:
                            answer_text = input(f"{field.label} [Y/n]: " if field.yes_no else f"{field.label}: ").strip()
                        except (EOFError, KeyboardInterrupt):
                            answer_text = ""
                            break
                        if field.yes_no:
                            answer_text = {"": "yes", "y": "yes", "yes": "yes", "n": "no", "no": "no"}.get(answer_text.lower(), "")
                            if answer_text:
                                break
                            print("Answer y or n.")
                        elif not answer_text or not field.pattern or re.fullmatch(field.pattern, answer_text):
                            break
                        else:
                            print(f"That doesn't look right ({field.pattern}). Try again, or press Enter to cancel.")
                    else:
                        answer_text = ""
                    if not answer_text:
                        break
                    values[field.name] = answer_text.upper() if field.uppercase else answer_text
                if len(values) < len(action.inputs):
                    print("Cancelled.")
                    continue
                if action.feature:
                    key = action.feature.format(**values).upper()
                    if sessions.FEATURE_KEY.match(key) and key != feature:
                        feature = key
                        feature_note = link_feature(agent, feature)
                        print(f"This session now works on {feature}.")
                line = action.prompt.format(**values)
                if words == ["service-map", "map"]:   # same request as /map: the service, a name, the repos
                    export_related(related)
                    line = service_map_request(values["flow"], "", related, session_folder.id, bool(open_code_root()))
                # An action may need more steps, never fewer; with no limit (0) it has none either.
                steps_for_this = (None if settings.max_steps == 0 or action.max_steps is None
                                  else max(action.max_steps, settings.max_steps))
                print(f"▶ {action.label}: {line}")

            compact_history()  # keep a long conversation within its budget
            write_status(state="working", activity="Starting")   # the app greys out its buttons meanwhile
            PROGRESS["fn"] = lambda activity, whole: write_status(state="working", activity=activity, whole=whole)
            calls_before = agent.session.stats.tool_calls
            try:
                skills_note = apply_session_skills()
                notes = [n for n in (feature_note, skills_note) if n]
                if notes:
                    line, feature_note = "\n\n".join([*notes, line]), None
                LIVE.begin()
                try:
                    answer = agent.send(line, max_steps=steps_for_this)
                finally:
                    LIVE.end()
            except KeyboardInterrupt:
                LIVE.clear()
                stopped = commands.stop_all()   # its thread can't be stopped; its command can
                print("\n[interrupted]" + (f" Stopped {stopped} running command{'s' if stopped > 1 else ''}."
                                           if stopped else ""))
                continue
            except StepLimitExceeded as exc:
                print(f"\n[stopped] {exc}")
                continue
            except ModelError as exc:
                print(f"\n[model error] {exc}")
                continue

            print(f"\n{screens.agent_label(style)}{answer}")
            hidden = agent.session.stats.tool_calls - calls_before
            if hidden and not TRACE["on"] and not tracer.enabled:
                print(style(f"({hidden} tool {'call' if hidden == 1 else 'calls'} · /trace on to see them)", "dim"))
            warn_if_full()

    finally:
        # Each step on its own: the terminal may already be gone (closed window).
        for step in (write_status, lambda: close_model(agent), lambda: sessions.close(session_folder),
                     MCP_POOL.close):
            try:
                step()
            except Exception:  # noqa: BLE001 - shutting down; the next step must still run
                pass

if __name__ == "__main__":
    sys.exit(main())
