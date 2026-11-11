"""Debug tracing: follow one request through the whole pipeline (``orvik -d``).

Each user message starts a new REQUEST banner. Every hop in the pipeline is
printed as a numbered point, showing who hands what to whom:

    ① CLI → Agent              your message
    ② Agent                    the history that will be sent to the model
    ③ Agent → adapter          which step, which tools are offered
    ④ adapter → model          the exact request (codex command + prompt / API JSON)
    ⑤ model → adapter          the raw reply, token usage and time
    ⑥ adapter → Agent          the parsed decision: final answer or tool call
       (tool call?)  Agent → Gateway → safety checks → tool result → back to ②
    ⑦ Agent → CLI              the final answer

When disabled (the default), every method returns immediately, so the rest
of the code can call the tracer freely.
"""

import json
import os
import shutil
import sys
from contextlib import contextmanager
from typing import Any, Iterator, TextIO

from agent_core.audit import redact

# Border colors per frame kind (ANSI codes).
COLORS = {
    "user": "36",    # cyan    - your input
    "event": "90",   # gray    - internal state, safety checks
    "input": "34",   # blue    - going to the model
    "output": "32",  # green   - coming from the model
    "tool": "33",    # yellow  - tool call / tool result
    "answer": "35",  # magenta - final answer
    "error": "31",   # red     - anything that failed
}
CIRCLED = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳"


class Tracer:
    def __init__(
        self,
        enabled: bool = False,
        stream: TextIO | None = None,
        width: int | None = None,
        color: bool | None = None,
        request_count: int = 0,
    ) -> None:
        """Create a tracer; it prints only when enabled. request_count continues numbering."""
        self.enabled = enabled
        self.stream = stream or sys.stdout
        self.width = max(40, width or min(shutil.get_terminal_size((100, 20)).columns, 120))
        if color is None:
            color = self.stream.isatty() and "NO_COLOR" not in os.environ
        self.color = color
        self._request_no = request_count
        self._point_no = 0
        self._checks: list[tuple[str, bool]] | None = None  # set while collecting

    @property
    def request_count(self) -> int:
        """How many user messages this session has had (counted even while disabled)."""
        return self._request_no

    def begin_request(self, text: str) -> None:
        """Print a banner for a new user message and restart the numbering at ①."""
        self._request_no += 1  # counted even when disabled, so numbers stay right after /debug on
        if not self.enabled:
            return
        self._point_no = 0
        label = " ".join(text.split())
        title = f" REQUEST #{self._request_no} · \"{label}\" "
        if len(title) > self.width - 8:
            title = title[: self.width - 11] + "…\" "
        line = "━" * 4 + title + "━" * max(0, self.width - 4 - len(title))
        self.stream.write("\n" + self._paint(line, "user") + "\n")
        self.stream.flush()

    def point(self, title: str, body: str, kind: str = "event") -> None:
        """Print the next numbered pipeline point."""
        if not self.enabled:
            return
        self._point_no += 1
        number = (
            CIRCLED[self._point_no - 1] if self._point_no <= len(CIRCLED) else f"({self._point_no})"
        )
        self.frame(f"{number} {title}", body, kind)

    @contextmanager
    def collect_checks(self, title: str) -> Iterator[None]:
        """Gather the gateway's steps and print them as one numbered point at the end."""
        if not self.enabled:
            yield
            return
        self._checks = []
        try:
            yield
        finally:
            checks, self._checks = self._checks, None
            failed = any(not ok for _, ok in checks)
            body = "\n".join(line for line, _ in checks) or "(no steps)"
            self.point(title, body, "error" if failed else "event")

    def event(self, event_type: str, details: dict[str, Any]) -> None:
        """Record one gateway step while collecting (see collect_checks)."""
        if not self.enabled or self._checks is None:
            return
        self._checks.append(describe_event(event_type, redact(details)))

    def frame(self, title: str, body: str, kind: str = "event") -> None:
        """Print text inside a box, with the title in the top border."""
        if not self.enabled:
            return
        inner = self.width - 4  # room for "│ " and " │"

        lines: list[str] = []
        for line in str(body).expandtabs(4).splitlines() or [""]:
            # Hard-wrap long lines so indentation (e.g. in JSON) is kept exactly.
            lines += [line[i:i + inner] for i in range(0, len(line), inner)] or [""]

        title = f" {title} "
        if len(title) > self.width - 4:
            title = title[: self.width - 6] + "… "

        top = "┌─" + title + "─" * (self.width - 3 - len(title)) + "┐"
        bottom = "└" + "─" * (self.width - 2) + "┘"
        side = self._paint("│", kind)

        out = [self._paint(top, kind)]
        out += [f"{side} {line.ljust(inner)} {side}" for line in lines]
        out.append(self._paint(bottom, kind))
        self.stream.write("\n".join(out) + "\n")
        self.stream.flush()

    def _paint(self, text: str, kind: str) -> str:
        """Color the text for this frame kind (if colors are on)."""
        if not self.color:
            return text
        return f"\033[{COLORS.get(kind, '0')}m{text}\033[0m"


def describe_event(event_type: str, d: dict[str, Any]) -> tuple[str, bool]:
    """Turn one gateway audit event into a readable line; the flag is False on failure."""
    j = lambda value: json.dumps(value, ensure_ascii=False, default=str)  # noqa: E731
    match event_type:
        case "tool.requested":
            return f"→ requested: {d.get('tool')} {j(d.get('arguments'))}", True
        case "tool.rejected":
            return f"✗ rejected ({d.get('error_type')}): {d.get('error')}", False
        case "tool.permission_checked":
            if d.get("permitted"):
                return f"✓ permission: risk '{d.get('risk_level')}' is allowed", True
            return f"✗ permission: risk '{d.get('risk_level')}' is NOT allowed", False
        case "tool.approval_requested":
            return "? approval: asking the human", True
        case "tool.approval_granted":
            return "✓ approval: granted", True
        case "tool.approval_denied":
            return "✗ approval: denied", False
        case "tool.approval_failed":
            return f"✗ approval: failed ({d.get('error')})", False
        case "tool.authorization_issued":
            return "✓ one-time token issued for exactly this call", True
        case "tool.authorization_failed":
            return f"✗ token: {d.get('error')}", False
        case "tool.execution_started":
            return f"▶ executing (mode: {d.get('mode')})", True
        case "tool.attempt_started":
            return f"    attempt {d.get('attempt')} started", True
        case "tool.attempt_succeeded":
            return f"    attempt {d.get('attempt')} ok ({d.get('duration_ms')} ms)", True
        case "tool.attempt_failed":
            return f"    attempt {d.get('attempt')} failed ({d.get('error_type')}): {d.get('error')}", False
        case "tool.attempt_retry_scheduled":
            return f"    retrying in {d.get('delay_seconds')}s", True
        case "tool.execution_succeeded":
            return f"✓ done: {d.get('attempts')} attempt(s), {d.get('duration_ms')} ms", True
        case "tool.execution_failed":
            return f"✗ failed ({d.get('error_type')}): {d.get('error')}", False
    return f"• {event_type}: {j(d)}", True


def summarize_messages(messages: list[dict[str, Any]], limit: int = 300) -> str:
    """One line per history message: who said it and (the start of) what was said."""
    lines = []
    for m in messages:
        role = m.get("role", "?")
        if role == "tool":
            lines.append(f"[tool result {m.get('tool_call_id')}] {_short(m.get('content'), limit)}")
            continue
        if m.get("content"):
            lines.append(f"[{role}] {_short(m['content'], limit)}")
        for call in m.get("tool_calls", []):
            fn = call["function"]
            lines.append(f"[{role} → tool call {call['id']}] {fn['name']} {fn['arguments']}")
    return "\n".join(lines)


def pretty_json(text: str) -> str:
    """Indent JSON text for reading; return it unchanged if it is not JSON."""
    try:
        return json.dumps(json.loads(text), indent=2, ensure_ascii=False)
    except (TypeError, ValueError):
        return text


def _short(value: Any, limit: int) -> str:
    """Squash whitespace and cut long text with an ellipsis."""
    text = " ".join(str(value).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"
