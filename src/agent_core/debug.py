"""Debug tracing: print every step of the agent flow inside a frame.

Enabled with ``agent-core -d``. When disabled (the default), every method
returns immediately, so the rest of the code can call the tracer freely.

Frame kinds (border color):
    input  - what is sent to the model (blue)
    output - what the model replied (green)
    tool   - a tool result sent back to the model (yellow)
    event  - a pipeline step, e.g. permission check (gray)
    error  - a failed / denied / rejected step (red)
"""

import json
import os
import shutil
import sys
from typing import Any, TextIO

from agent_core.audit import redact

COLORS = {"input": "34", "output": "32", "tool": "33", "event": "90", "error": "31"}
ERROR_WORDS = ("failed", "denied", "rejected", "invalid")


class Tracer:
    def __init__(
        self,
        enabled: bool = False,
        stream: TextIO | None = None,
        width: int | None = None,
        color: bool | None = None,
    ) -> None:
        """Create a tracer; it prints only when enabled."""
        self.enabled = enabled
        self.stream = stream or sys.stdout
        self.width = max(40, width or min(shutil.get_terminal_size((100, 20)).columns, 120))
        if color is None:
            color = self.stream.isatty() and "NO_COLOR" not in os.environ
        self.color = color

    def event(self, event_type: str, details: dict[str, Any]) -> None:
        """Show one pipeline step (the same events the audit log records)."""
        if not self.enabled:
            return
        kind = "error" if any(word in event_type for word in ERROR_WORDS) else "event"
        body = "\n".join(_field(key, value) for key, value in redact(details).items())
        self.frame(event_type, body or "(no details)", kind)

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


def pretty_json(text: str) -> str:
    """Indent JSON text for reading; return it unchanged if it is not JSON."""
    try:
        return json.dumps(json.loads(text), indent=2, ensure_ascii=False)
    except (TypeError, ValueError):
        return text


def _field(key: str, value: Any) -> str:
    """Format one 'key: value' line; multi-line values go on indented lines below."""
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    if "\n" in text:
        return f"{key}:\n" + "\n".join("  " + line for line in text.splitlines())
    return f"{key}: {text}"
