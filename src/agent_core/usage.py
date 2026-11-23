"""Usage numbers for /status: what this session did, and what each AI backend used.

* ``SessionStats`` counts messages, AI steps, tool calls and approvals. It is
  fed by ``CountingAuditLogger``, which sees every audit event on its way to
  the log file, so no other code has to remember to count.
* ``UsageStats`` holds one backend's numbers (requests, tokens, cost, context
  size, subscription limits). Each model adapter fills its own as replies
  arrive. The CLI keeps one per backend, so switching with /backend and back
  doesn't lose them.

Nothing here is ever sent to the AI.
"""

import json
import time
from collections import Counter
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

from agent_core.audit import AuditLogger


@dataclass
class LimitWindow:
    """One subscription limit, e.g. "5-hour: 18% used, resets at 17:30"."""
    name: str
    used_percent: float
    resets_at: float | None = None  # Unix time
    status: str | None = None        # "allowed", "allowed_warning", "rejected"...


@dataclass
class UsageStats:
    """What one backend used in this session."""
    requests: int = 0
    seconds: float = 0.0
    input_tokens: int = 0         # all input, cached included
    cached_tokens: int = 0        # part of input_tokens read from the cache (cheap re-reads)
    cache_write_tokens: int = 0   # part of input_tokens written to the cache (new input; Claude reports it)
    output_tokens: int = 0
    cost_usd: float | None = None  # only when the backend reports it
    model: str | None = None       # the model that really answered, if reported
    context_tokens: int | None = None  # input size of the latest request = how full the context is
    context_window: int | None = None
    limits: list[LimitWindow] = field(default_factory=list)
    limits_note: str = ""          # where the limits come from ("live", "last known ...")

    @property
    def new_input_tokens(self) -> int:
        """Input that wasn't a cache re-read: what each request really added (cache writes included)."""
        return max(self.input_tokens - self.cached_tokens, 0)

    def add_request(self, seconds: float, input_tokens: int = 0, cached_tokens: int = 0,
                    output_tokens: int = 0, cost_usd: float | None = None,
                    cache_write_tokens: int = 0) -> None:
        self.requests += 1
        self.seconds += seconds
        self.input_tokens += input_tokens
        self.cached_tokens += cached_tokens
        self.cache_write_tokens += cache_write_tokens
        self.output_tokens += output_tokens
        if cost_usd is not None:
            self.cost_usd = (self.cost_usd or 0.0) + cost_usd
        self.context_tokens = input_tokens or self.context_tokens


@dataclass
class SessionStats:
    """What happened in this chat session, whatever backend answered."""
    started: float = field(default_factory=time.time)
    events: Counter = field(default_factory=Counter)
    tools: Counter = field(default_factory=Counter)

    def count(self, event_type: str, details: dict[str, Any]) -> None:
        self.events[event_type] += 1
        if event_type == "tool.requested" and details.get("tool"):
            self.tools[str(details["tool"])] += 1

    # Friendly names for the counters shown by /status.
    @property
    def messages(self) -> int:
        return self.events["agent.started"]

    @property
    def steps(self) -> int:
        return self.events["model.requested"]

    @property
    def tool_calls(self) -> int:
        return self.events["tool.requested"]

    @property
    def tools_ok(self) -> int:
        return self.events["tool.execution_succeeded"]

    @property
    def tools_failed(self) -> int:
        return self.events["tool.execution_failed"] + self.events["tool.invalid_arguments"] \
            + self.events["tool.preview_failed"] + self.events["tool.rejected"]

    @property
    def approvals_asked(self) -> int:
        return self.events["tool.approval_requested"]

    @property
    def approvals_yes(self) -> int:
        return self.events["tool.approval_granted"]

    @property
    def approvals_no(self) -> int:
        return self.events["tool.approval_denied"] + self.events["tool.approval_failed"]


MAX_CALLS = 300   # tool calls kept for the app's list (the counts keep counting)


@dataclass
class SessionUsage:
    """Everything /status shows, kept for the whole chat (across /backend switches),
    saved with the session (stats.json) so a resumed session shows it again."""
    stats: SessionStats = field(default_factory=SessionStats)
    backends: dict[str, UsageStats] = field(default_factory=dict)
    # Each tool call: title ("Read Jira issue PROJ-1"), tool, ok (None while it runs), at.
    calls: list[dict[str, Any]] = field(default_factory=list)

    def for_backend(self, name: str) -> UsageStats:
        return self.backends.setdefault(name, UsageStats())

    def add_call(self, title: str, tool: str, now: float | None = None) -> None:
        self.calls.append({"title": title, "tool": tool, "ok": None,
                           "at": time.time() if now is None else now})
        del self.calls[:-MAX_CALLS]

    def finish_call(self, tool: str | None, ok: bool) -> None:
        if self.calls and self.calls[-1]["ok"] is None and tool in (None, self.calls[-1]["tool"]):
            self.calls[-1]["ok"] = ok

    def to_json(self) -> dict[str, Any]:
        return {"format": 1, "started": self.stats.started, "events": dict(self.stats.events),
                "tools": dict(self.stats.tools),
                "backends": {name: asdict(usage) for name, usage in self.backends.items()},
                "calls": self.calls}

    def restore(self, data: dict[str, Any] | None) -> None:
        """Take back what a saved session had. Changes objects in place: the model
        already holds its backend's UsageStats."""
        if not isinstance(data, dict):
            return
        try:
            self.stats.started = float(data.get("started", self.stats.started))
            self.stats.events.update({str(k): int(v) for k, v in (data.get("events") or {}).items()})
            self.stats.tools.update({str(k): int(v) for k, v in (data.get("tools") or {}).items()})
            for name, saved in (data.get("backends") or {}).items():
                usage = self.for_backend(str(name))
                for f in fields(UsageStats):
                    if f.name in saved and f.name != "limits":
                        setattr(usage, f.name, saved[f.name])
                usage.limits = [LimitWindow(**w) for w in saved.get("limits") or []]
                if usage.limits and usage.limits_note.startswith("live"):
                    usage.limits_note = "last known"
            self.calls[:0] = [c for c in data.get("calls") or []
                              if isinstance(c, dict) and {"title", "tool", "at"} <= set(c)][-MAX_CALLS:]
        except (TypeError, ValueError):
            pass   # an unreadable stats.json only costs the old numbers


class CountingAuditLogger:
    """Passes every audit event on to the real logger, counting it on the way."""

    def __init__(self, inner: AuditLogger, stats: SessionStats) -> None:
        self.inner = inner
        self.stats = stats

    def record(self, run_id: str, event_type: str, details: dict[str, Any],
               call_id: str | None = None) -> None:
        self.stats.count(event_type, details)
        self.inner.record(run_id, event_type, details, call_id)


# --- Codex: last known limits ----------------------------------------------------------

CODEX_SESSIONS = Path.home() / ".codex" / "sessions"
CODEX_TAIL_BYTES = 512_000
CODEX_FILES_CHECKED = 5


def codex_last_known_limits(sessions_dir: Path = CODEX_SESSIONS) -> tuple[list[LimitWindow], float | None]:
    """Your Codex limits as last recorded in a Codex session you ran yourself.

    ``codex exec --json`` (what Orwick uses) doesn't report limits, and Orwick's
    own calls are not saved. Codex's interactive sessions record a
    ``rate_limits`` snapshot, so the newest one is the best we know. Only that
    snapshot is read (from the end of the newest files); nothing else in them.
    Returns the limits and the time of the snapshot's file.
    """
    try:
        files = sorted(sessions_dir.rglob("rollout-*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        return [], None
    for path in files[:CODEX_FILES_CHECKED]:
        snapshot = _last_rate_limits(path)
        if snapshot:
            return _codex_windows(snapshot), path.stat().st_mtime
    return [], None


def _last_rate_limits(path: Path) -> dict[str, Any] | None:
    try:
        with path.open("rb") as file:
            file.seek(0, 2)
            size = file.tell()
            file.seek(max(0, size - CODEX_TAIL_BYTES))
            lines = file.read().decode("utf-8", errors="replace").splitlines()
    except OSError:
        return None
    for line in reversed(lines):
        if '"rate_limits"' not in line:
            continue
        try:
            found = _find_key(json.loads(line), "rate_limits")
        except json.JSONDecodeError:
            continue
        if isinstance(found, dict):
            return found
    return None


def _find_key(data: Any, key: str) -> Any:
    if isinstance(data, dict):
        if key in data:
            return data[key]
        for value in data.values():
            found = _find_key(value, key)
            if found is not None:
                return found
    elif isinstance(data, list):
        for value in data:
            found = _find_key(value, key)
            if found is not None:
                return found
    return None


def _codex_windows(snapshot: dict[str, Any]) -> list[LimitWindow]:
    windows = []
    for key in ("primary", "secondary"):
        window = snapshot.get(key)
        if not isinstance(window, dict) or "used_percent" not in window:
            continue
        minutes = window.get("window_minutes") or 0
        windows.append(LimitWindow(
            name=_window_name(minutes),
            used_percent=float(window["used_percent"]),
            resets_at=window.get("resets_at"),
        ))
    return windows


def _window_name(minutes: int) -> str:
    if minutes == 300:
        return "5-hour"
    if minutes == 10080:
        return "weekly"
    if minutes and minutes % 1440 == 0:
        return f"{minutes // 1440}-day"
    if minutes and minutes % 60 == 0:
        return f"{minutes // 60}-hour"
    return f"{minutes}-minute" if minutes else "limit"


# --- Claude: limits from the stream -----------------------------------------------------

CLAUDE_WINDOW_NAMES = {"five_hour": "5-hour", "seven_day": "7-day",
                       "seven_day_opus": "7-day (Opus)", "seven_day_sonnet": "7-day (Sonnet)"}


def claude_windows(info: dict[str, Any]) -> list[LimitWindow]:
    """Limits from a claude ``rate_limit_event`` (utilization is 0..1)."""
    windows = []
    for key, window in (info.get("unifiedWindows") or {}).items():
        if isinstance(window, dict) and "utilization" in window:
            windows.append(LimitWindow(
                name=CLAUDE_WINDOW_NAMES.get(key, key.replace("_", " ")),
                used_percent=float(window["utilization"]) * 100,
                resets_at=window.get("resetsAt"),
                status=window.get("status"),
            ))
    if not windows and "resetsAt" in info:
        windows.append(LimitWindow(
            name=CLAUDE_WINDOW_NAMES.get(info.get("rateLimitType", ""), "limit"),
            used_percent=float(info.get("utilization", 0)) * 100,
            resets_at=info.get("resetsAt"), status=info.get("status"),
        ))
    return windows
