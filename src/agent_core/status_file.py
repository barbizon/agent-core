"""A machine-readable copy of /status, for apps that show Orwick's state.

It lives in the session's folder, ``.orwick/sessions/<id>/status.json`` (see
sessions.py). The CLI rewrites the file before each prompt and after every step of an answer,
so it is never more than one step old. It holds counts, the tool list and token usage only: never the
conversation, file contents or secrets. It is written atomically (a reader never
sees half a file) and readable only by you.
"""

import json
import os
import tempfile
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from agent_core.usage import LimitWindow, UsageStats, codex_last_known_limits

FORMAT = 1
CODEX_LIMITS_EVERY_SECONDS = 300  # reading Codex's own session files is not free


def snapshot(agent: Any, backend: str, model: str, workspace: Path, debug: bool,
             codex_limits: tuple[list[LimitWindow], float | None] = ([], None),
             now: float | None = None, session_id: str | None = None,
             continued_in: str | None = None, budget: int | None = None,
             warn_percent: int | None = None, tool_defaults: dict[str, bool] | None = None,
             tool_overrides: dict[str, bool] | None = None, state: str = "waiting",
             activity: str | None = None, session_name: str = "",
             skill_settings: dict[str, bool] | None = None,
             skill_overrides: dict[str, bool] | None = None) -> dict[str, Any]:
    """Everything /status shows, plus the tool list, as plain JSON data.

    ``continued_in`` is set on a session's last update when /clear started a new
    one, so an app watching this folder can follow to the new session.
    """
    now = time.time() if now is None else now
    stats = agent.session.stats
    usage = {}
    for name, stats_for_backend in agent.session.backends.items():
        usage[name] = _usage(stats_for_backend)
    if backend == "codex" and codex_limits[0]:  # Codex doesn't report limits to Orwick
        usage.setdefault(backend, _usage(UsageStats()))
        usage[backend]["limits"] = [asdict(w) for w in codex_limits[0]]
        usage[backend]["limits_note"] = "last known"
        usage[backend]["limits_seen_at"] = codex_limits[1]
    return {
        "format": FORMAT,
        "updated_at": now,
        "pid": os.getpid(),
        "session_id": session_id,
        "name": session_name or None,   # the name you gave the session (name.txt); null: none
        "state": state,   # "waiting": at the prompt, ready for you; "working": answering
        "activity": activity if state == "working" else None,   # while working: what it does now
        # This session's tool calls, oldest first: title, tool, ok (null: still running), at
        "calls": getattr(agent.session, "calls", []),
        # Every skill's buttons, also of skills that are off: the app shows those whose skill
        # is on, and follows its own session checkboxes at once ("on" is Orwick's view).
        # An analytics skill has no buttons here: it is run from the app's Analytics screen.
        "actions": [{"skill": s.name, "id": a.id, "label": a.label, "icon": a.icon, "command": a.command(s.name),
                     "inputs": [i.label for i in a.inputs], "hint": a.hint, "on": s.name in _names_on(agent)}
                    for s in getattr(agent, "skills", []) if s.kind != "analytics" for a in s.actions],
        # The button groups, in the order of "actions": one per skill with actions.
        "action_groups": [{"skill": s.name, "title": s.title or s.name, "hint": s.hint}
                          for s in getattr(agent, "skills", []) if s.actions and s.kind != "analytics"],
        "feature": getattr(agent, "feature", None),
        "continued_in": continued_in,
        "workspace": str(workspace),
        "backend": backend,
        "model": model,
        "model_choice": getattr(agent, "model_choice", None),   # the chosen model name; null = backend default
        "debug": debug,
        # enabled: what this session uses; default: the settings' list; session: this
        # session's own switch (null when it follows the settings).
        "own_tools": _own_tools(backend, getattr(agent, "own_tools", [])),
        "tools": [{"name": tool.name, "risk": str(tool.risk_level), "description": tool.description,
                   "uses": stats.tools.get(tool.name, 0), "enabled": enabled,
                   "default": (tool_defaults or {}).get(tool.name, not tool.name.startswith("mcp__")),
                   "session": (tool_overrides or {}).get(tool.name),
                   "origin": getattr(agent, "tool_origins", {}).get(tool.name)}
                  for enabled, tools in ((True, agent.registry.list_tools()),
                                         (False, getattr(agent, "disabled_tools", [])))
                  for tool in tools],
        "skills": [s.name for s in getattr(agent, "skills_on", [])],
        # Every skill found (built-in, Claude's, yours, the folder's), with on/off for this session.
        "skills_all": _skills_all(agent, skill_settings, skill_overrides),
        "docs": _docs(),
        "session": {
            "started_at": stats.started,
            "messages": stats.messages,
            "steps": stats.steps,
            "tool_calls": stats.tool_calls,
            "tools_ok": stats.tools_ok,
            "tools_failed": stats.tools_failed,
            "approvals_asked": stats.approvals_asked,
            "approvals_yes": stats.approvals_yes,
            "approvals_no": stats.approvals_no,
            "history_messages": len(agent.messages),
            "history_chars": sum(len(str(m.get("content") or "")) for m in agent.messages),
        },
        "usage": usage,
        "conversation": _conversation(agent.messages, budget, warn_percent),
    }


def _names_on(agent: Any) -> set[str]:
    return {s.name for s in getattr(agent, "skills_on", [])}


def _skills_all(agent: Any, settings: dict[str, bool] | None,
                overrides: dict[str, bool] | None) -> list[dict[str, Any]]:
    from agent_core.skills import describe
    return describe(getattr(agent, "skills", []), getattr(agent, "skills_on", []), settings, overrides)


def _docs() -> list[str]:
    """The names of this session's docs (Markdown notes in its docs folder)."""
    from agent_core.tools.knowledge import SESSION_DOCS_ENV
    folder = Path(os.environ.get(SESSION_DOCS_ENV) or "/nonexistent")
    return sorted(p.stem for p in folder.glob("*.md") if p.is_file() and not p.is_symlink()) if folder.is_dir() else []


def _own_tools(backend: str, names: list[str]) -> list[dict[str, Any]]:
    """The answering AI's own built-in tools that are switched on, with their risk."""
    from agent_core.llm.own_tools import CATALOG
    tools = CATALOG.get(backend, {})
    return [{"name": n, "risk": tools[n].risk, "what": tools[n].what} for n in names if n in tools]


def _conversation(messages: list[dict[str, Any]], budget: int | None,
                  warn_percent: int | None) -> dict[str, Any] | None:
    """How full the conversation is, for the app's badge and banner."""
    if not budget:
        return None
    from agent_core.compaction import SUMMARIZE_AT, TRIM_AT, fill
    level = fill(messages, budget)
    return {"tokens": level.tokens, "budget": budget, "percent": level.percent,
            "warn_percent": warn_percent, "trim_percent": round(TRIM_AT * 100),
            "summarize_percent": round(SUMMARIZE_AT * 100)}


def _usage(stats: UsageStats) -> dict[str, Any]:
    data = asdict(stats)
    data["new_input_tokens"] = stats.new_input_tokens
    data["limits"] = [asdict(w) for w in stats.limits]
    return data


def write(path: str | Path, data: dict[str, Any]) -> None:
    """Replace the file in one step, readable only by you."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp = tempfile.mkstemp(dir=path.parent, prefix=".status-", suffix=".json")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as file:
            json.dump(data, file)
        os.chmod(temp, 0o600)
        os.replace(temp, path)
    except BaseException:
        Path(temp).unlink(missing_ok=True)
        raise


class CodexLimitsCache:
    """Codex's last known limits, re-read at most every few minutes."""

    def __init__(self, reader=codex_last_known_limits, every: float = CODEX_LIMITS_EVERY_SECONDS) -> None:
        self.reader, self.every = reader, every
        self.value: tuple[list[LimitWindow], float | None] = ([], None)
        self.read_at: float | None = None

    def get(self, now: float | None = None) -> tuple[list[LimitWindow], float | None]:
        now = time.time() if now is None else now
        if self.read_at is None or now - self.read_at >= self.every:
            self.value, self.read_at = self.reader(), now
        return self.value
