"""Structured tool results, as returned to the model."""

import json
from dataclasses import dataclass
from typing import Any

TRUNCATION_MARKER = "...[truncated]"


@dataclass(frozen=True)
class ToolError:
    type: str
    message: str


@dataclass(frozen=True)
class ToolResult:
    ok: bool
    data: Any = None
    error: ToolError | None = None

    @classmethod
    def success(cls, data: Any) -> "ToolResult":
        """Make a successful result."""
        return cls(ok=True, data=data)

    @classmethod
    def failure(cls, error_type: str, message: str) -> "ToolResult":
        """Make a failed result with an error type and message."""
        return cls(ok=False, error=ToolError(type=error_type, message=message))

    def to_model_content(self, max_chars: int) -> str:
        """Serialize for the model, capping size so a tool cannot flood the context.

        Long text fields (file contents, command output, Jira text) are not escaped
        into the JSON: the JSON line says ``"<below: content>"`` and the raw text
        follows. Escaped code (\\n, \\t, \\") costs noticeably more tokens than
        the same code as plain text.
        """
        if self.ok:
            data, blocks = _lift_long_text(self.data)
            payload: dict[str, Any] = {"ok": True, "data": data}
        else:
            assert self.error is not None
            blocks = []
            payload = {
                "ok": False,
                "error": {"type": self.error.type, "message": self.error.message},
            }

        text = json.dumps(payload, default=str, ensure_ascii=False)
        for name, value in blocks:
            text += f"\n{BLOCK_START.format(name=name)}\n{value}\n{BLOCK_END.format(name=name)}"

        if max_chars and len(text) > max_chars:   # 0 = no limit
            # Reserve room for the marker (its number has at most as many digits as len(text)).
            room = max(max_chars - len(_marker(len(text))), 0)
            text = text[:room] + _marker(len(text) - room)

        return text


def _marker(left: int) -> str:
    return f"{TRUNCATION_MARKER} ({left:,} more characters not shown: ask for a smaller part)"


BLOCK_START = "--- {name} ---"
BLOCK_END = "--- end of {name} ---"
LIFT_MIN_CHARS = 200   # shorter strings stay inside the JSON


def _lift_long_text(data: Any) -> tuple[Any, list[tuple[str, str]]]:
    """Move long strings of a result dict out of the JSON (top level only)."""
    if not isinstance(data, dict):
        return data, []
    lifted, blocks = {}, []
    for key, value in data.items():
        if isinstance(value, str) and len(value) >= LIFT_MIN_CHARS:
            lifted[key] = f"<below: {key}>"
            blocks.append((str(key), value))
        else:
            lifted[key] = value
    return lifted, blocks
