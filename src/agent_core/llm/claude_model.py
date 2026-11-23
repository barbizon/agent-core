"""Claude backend: uses your Claude subscription through the local ``claude`` CLI.

How it works:

* One long-running ``claude -p`` process in **stream-json** mode: Orwick writes
  one JSON line per turn on its stdin and reads events from its stdout. The
  process keeps the conversation, so each step sends only what is new (your
  message, or a tool result) instead of the whole history. That's faster than
  the Codex backend, which starts a fresh process every step.
* ``--json-schema`` makes every reply the same JSON "decision" the Codex backend
  uses: a final answer or exactly one tool call, turned into a ModelResponse.
* If the history Orwick holds no longer matches what the process saw (``/reset``,
  a new system prompt or tool list), the process is restarted and the whole
  conversation is replayed once.

Isolation (a clean agent): Claude Code is a full agent with its own tools,
settings, skills, memory, hooks and plugins. None of that is wanted here:

* ``--tools ""``: no built-in tools at all (no shell, no file access). It can
  only *propose* Orwick tools; Orwick's gateway runs them, with approval and the
  sandbox.
* ``--safe-mode`` and ``--setting-sources ""``: no CLAUDE.md, skills, memory,
  hooks, plugins, custom commands or your settings files.
* ``--strict-mcp-config`` (with no MCP config): no MCP servers.
* ``--no-session-persistence``: nothing saved to disk.
* Runs in an empty temporary folder, so it can't pick up a project.
* ``ANTHROPIC_API_KEY`` / ``ANTHROPIC_AUTH_TOKEN`` are removed from its
  environment, so it uses your subscription login and never pay-per-token.
"""

import json
import os
import queue
import shlex
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Callable

from agent_core.debug import Tracer
from agent_core.errors import ModelError
from agent_core.llm import ModelResponse
from agent_core.usage import UsageStats, claude_windows
from agent_core.llm.codex_model import (
    CLAUDE_DECISION_SCHEMA,
    CLAUDE_REPLY_FORMAT,
    runtime_contract,
    _describe_tools,
    _transcript,
    parse_decision,
)

# Variables that would switch Claude Code to pay-per-token API billing, or make
# it think it runs inside another Claude Code session.
REMOVED_ENV = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT")

NEXT_STEP = "Return the JSON decision for the next step."

Popen = Callable[..., subprocess.Popen]


class ClaudeCLIModel:
    label = "Claude adapter"

    def __init__(
        self,
        model: str | None = None,
        timeout_seconds: float = 120.0,
        claude_bin: str = "claude",
        popen: Popen = subprocess.Popen,
        tracer: Tracer | None = None,
        usage: UsageStats | None = None,
        own_tools: list[str] | None = None,
        workspace: Path | None = None,
    ) -> None:
        """Set the model, timeout and how to start claude. Nothing starts until the first call.

        ``own_tools``: Claude Code's own tools to switch on (none by default). With a
        dangerous one (files, commands) Claude works in ``workspace``; otherwise in an
        empty folder.
        """
        self.usage = usage or UsageStats()
        self.own_tools = list(own_tools or [])
        self.workspace = workspace
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.claude_bin = claude_bin
        self._popen = popen
        self.tracer = tracer or Tracer()
        self._process: subprocess.Popen | None = None
        self._workdir: str | None = None
        self._events: queue.Queue = queue.Queue()
        self._stderr: deque[str] = deque(maxlen=20)
        self._system: str | None = None
        self._seen: list[dict[str, Any]] = []  # history the process already knows
        self._cost_so_far = 0.0  # the process's running cost total, as last reported

    # --- the ChatModel protocol ---------------------------------------------------

    def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> ModelResponse:
        """Send what's new since the last call and return Claude's decision."""
        system = build_system_prompt(messages, tools, self.own_tools)
        body = [m for m in messages if m["role"] != "system"]
        if not self._can_continue(system, body):
            self._start(system)
            text = replay_text(body)
        else:
            text = new_turn_text(body[len(self._seen):])

        self.tracer.point(
            f"Orwick → Claude · message ({len(text):,} chars)", text, "input",
        )
        started = time.monotonic()
        self._write({"type": "user", "message": {"role": "user", "content": text}})
        result, log = self._read_result()
        elapsed = time.monotonic() - started
        self.tracer.point(
            f"Claude → Orwick · raw reply ({elapsed:.1f}s)", "\n".join(log), "output",
        )
        self._seen = list(body)
        self._record_usage(result, elapsed)
        return parse_decision(decision_text(result), source="claude")

    def close(self) -> None:
        """Stop the claude process and remove its temporary folder."""
        process, self._process = self._process, None
        if process is not None:
            try:
                if process.stdin:
                    process.stdin.close()
            except OSError:
                pass
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                for sig in (signal.SIGTERM, signal.SIGKILL):
                    try:
                        os.killpg(process.pid, sig)
                    except (ProcessLookupError, PermissionError):
                        pass
                    try:
                        process.wait(timeout=1)
                        break
                    except subprocess.TimeoutExpired:
                        pass
        if self._workdir:
            shutil.rmtree(self._workdir, ignore_errors=True)
            self._workdir = None
        self._seen = []
        self._system = None
        self._cost_so_far = 0.0   # a new process starts its total again

    # --- the process ----------------------------------------------------------------

    def command(self, system: str) -> list[str]:
        """The claude command line (also shown in debug mode)."""
        command = [
            self.claude_bin, "-p",
            "--input-format", "stream-json",
            "--output-format", "stream-json",
            "--verbose",                       # required by stream-json output
            "--tools", ",".join(self.own_tools),  # "" = no built-in tools at all
            "--safe-mode",                     # no CLAUDE.md, skills, memory, hooks, plugins
            "--setting-sources", "",           # no user/project/local settings files
            "--strict-mcp-config",             # no MCP servers
            "--disable-slash-commands",
            "--no-session-persistence",
            *(["--allowedTools", ",".join(self.own_tools)] if self.own_tools else []),
            "--permission-prompts", "none",    # anything else that would ask is refused
            "--system-prompt", system,
            "--json-schema", json.dumps(CLAUDE_DECISION_SCHEMA),
        ]
        if self.model:
            command += ["--model", self.model]
        return command

    def _can_continue(self, system: str, body: list[dict[str, Any]]) -> bool:
        """True if the running process already saw the start of this history."""
        return (
            self._process is not None
            and self._process.poll() is None
            and system == self._system
            and len(body) > len(self._seen)
            and body[: len(self._seen)] == self._seen
        )

    def _start(self, system: str) -> None:
        self.close()
        self._workdir = tempfile.mkdtemp(prefix="orwick-claude-")
        from agent_core.llm.own_tools import any_dangerous
        cwd = str(self.workspace) if self.workspace and any_dangerous("claude", self.own_tools) else self._workdir
        env = {k: v for k, v in os.environ.items() if k not in REMOVED_ENV}
        command = self.command(system)
        shown = [*command[: command.index("--system-prompt") + 1], f"<{len(system):,} chars>",
                 *command[command.index("--system-prompt") + 2:]]
        self.tracer.point("Orwick · starting the claude process", f"$ {shlex.join(shown)}", "internal")
        self.tracer.point(f"Orwick → Claude · system prompt ({len(system):,} chars)", system, "input")
        try:
            self._process = self._popen(
                command, cwd=cwd, env=env,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, bufsize=1, start_new_session=True,
            )
        except FileNotFoundError as exc:
            self.close()
            raise ModelError(
                f"'{self.claude_bin}' not found. Install Claude Code and log in with `claude`."
            ) from exc
        self._system = system
        self._events = queue.Queue()
        self._stderr.clear()
        threading.Thread(target=self._pump_stdout, args=(self._process, self._events), daemon=True).start()
        threading.Thread(target=self._pump_stderr, args=(self._process,), daemon=True).start()

    @staticmethod
    def _pump_stdout(process: subprocess.Popen, events: queue.Queue) -> None:
        for line in process.stdout:
            events.put(line)
        events.put(None)  # end of output: the process exited

    def _pump_stderr(self, process: subprocess.Popen) -> None:
        for line in process.stderr:
            self._stderr.append(line.rstrip())

    def _write(self, event: dict[str, Any]) -> None:
        try:
            self._process.stdin.write(json.dumps(event) + "\n")
            self._process.stdin.flush()
        except (OSError, ValueError) as exc:
            self._fail(f"claude stopped accepting input ({exc})")

    def _read_result(self) -> tuple[dict[str, Any], list[str]]:
        """Read events until this turn's result; return it and a short event log."""
        deadline = time.monotonic() + self.timeout_seconds if self.timeout_seconds else None   # 0 = wait
        log: list[str] = []
        while True:
            remaining = None if deadline is None else deadline - time.monotonic()
            if remaining is not None and remaining <= 0:
                self._fail(f"claude timed out after {self.timeout_seconds:g}s")
            try:
                line = self._events.get(timeout=remaining)
            except queue.Empty:
                self._fail(f"claude timed out after {self.timeout_seconds:g}s")
            if line is None:
                self._fail("claude exited unexpectedly")
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                log.append(f"(not JSON) {line.strip()}")
                continue
            log.append(describe_event(event))
            if event.get("type") == "rate_limit_event":
                windows = claude_windows(event.get("rate_limit_info") or {})
                if windows:
                    self.usage.limits = windows
                    self.usage.limits_note = "live, from Claude"
            if event.get("type") == "result":
                if event.get("is_error") or event.get("subtype") != "success":
                    self._fail(f"claude reported an error: {str(event.get('result') or event.get('subtype'))[:300]}")
                return event, log

    def _record_usage(self, result: dict[str, Any], seconds: float) -> None:
        """Add one reply's tokens, cost and context size to the usage numbers."""
        usage = result.get("usage") or {}
        cached = usage.get("cache_read_input_tokens", 0) or 0
        written = usage.get("cache_creation_input_tokens", 0) or 0
        total_input = (usage.get("input_tokens", 0) or 0) + cached + written
        # claude reports tokens per reply but total_cost_usd as the running total of
        # the whole process: count only what this reply added.
        cost = result.get("total_cost_usd")
        if isinstance(cost, (int, float)):
            cost, self._cost_so_far = max(cost - self._cost_so_far, 0.0), cost
        self.usage.add_request(seconds, total_input, cached, usage.get("output_tokens", 0) or 0, cost,
                               cache_write_tokens=written)
        for name, info in (result.get("modelUsage") or {}).items():
            self.usage.model = name
            self.usage.context_window = info.get("contextWindow") or self.usage.context_window

    def _fail(self, message: str) -> None:
        tail = "\n".join(list(self._stderr)[-5:])
        self.close()  # the next call starts fresh and replays the conversation
        raise ModelError(message + (f"\n{tail}" if tail else ""))


def build_system_prompt(messages: list[dict[str, Any]], tools: list[dict[str, Any]],
                        own_tools: list[str] | None = None) -> str:
    """Orwick's system prompt, the decision rules and the tool list."""
    from agent_core.llm.own_tools import contract_note
    system = [m["content"] for m in messages if m["role"] == "system" and m.get("content")]
    return "\n\n".join([*system, runtime_contract(contract_note("claude", own_tools or []), CLAUDE_REPLY_FORMAT),
                         "## Available tools", _describe_tools(tools)])


def replay_text(body: list[dict[str, Any]]) -> str:
    """The whole conversation, for a freshly started process."""
    if len(body) == 1 and body[0]["role"] == "user":
        return f"{body[0]['content']}\n\n{NEXT_STEP}"
    return f"## Conversation so far\n\n{_transcript(body)}\n\n## Your turn\n{NEXT_STEP}"


def new_turn_text(new: list[dict[str, Any]]) -> str:
    """Only what happened since the last reply (Claude already knows its own replies)."""
    unseen = [m for m in new if m["role"] != "assistant"]
    if len(unseen) == 1 and unseen[0]["role"] == "user":
        return f"{unseen[0]['content']}\n\n{NEXT_STEP}"
    return f"{_transcript(unseen)}\n\n{NEXT_STEP}"


def decision_text(result: dict[str, Any]) -> str:
    """The JSON decision from a result event (structured output, or the result text)."""
    structured = result.get("structured_output")
    if isinstance(structured, dict):
        return json.dumps(structured)
    return str(result.get("result") or "")


def describe_event(event: dict[str, Any]) -> str:
    """One debug line per stream-json event."""
    kind = event.get("type", "?")
    if kind == "system":
        if event.get("subtype") == "init":
            return (f"system init: model={event.get('model')}, tools={event.get('tools')}, "
                    f"skills={event.get('skills')}, auth={event.get('apiKeySource')}")
        return f"system {event.get('subtype')}"
    if kind == "assistant":
        parts = []
        for block in event.get("message", {}).get("content", []):
            if block.get("type") == "tool_use":
                parts.append(f"decision {json.dumps(block.get('input'), ensure_ascii=False)}")
            elif block.get("type") == "text" and block.get("text"):
                parts.append(f"text {block['text']}")
            elif block.get("type") == "thinking":
                parts.append("thinking")
        return "assistant: " + (", ".join(parts) or "(empty)")
    if kind == "result":
        usage = event.get("usage") or {}
        return (f"result {event.get('subtype')} in {event.get('duration_ms', 0) / 1000:.1f}s: "
                f"{usage.get('input_tokens', 0):,} input, "
                f"{usage.get('cache_creation_input_tokens', 0):,} cache write, "
                f"{usage.get('cache_read_input_tokens', 0):,} cached, "
                f"{usage.get('output_tokens', 0):,} output tokens")
    return kind
