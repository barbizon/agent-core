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
        """Serialize for the model, capping size so a tool cannot flood the context."""
        if self.ok:
            payload: dict[str, Any] = {"ok": True, "data": self.data}
        else:
            assert self.error is not None
            payload = {
                "ok": False,
                "error": {"type": self.error.type, "message": self.error.message},
            }

        text = json.dumps(payload, default=str, ensure_ascii=False)

        if len(text) > max_chars:
            text = text[: max_chars - len(TRUNCATION_MARKER)] + TRUNCATION_MARKER

        return text
