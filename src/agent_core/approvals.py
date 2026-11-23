"""Human-in-the-loop approval for non-read actions."""

import json
import os
import sys
import unicodedata
from dataclasses import dataclass
from typing import Any, Callable, Protocol

from agent_core.permissions import RiskLevel


def visible(text: str) -> str:
    """Show hidden characters as escapes (\\x1b, \\r...), keeping tabs.

    What you approve must be what you see: a terminal escape code or a
    carriage return inside a command or a file could otherwise hide part of
    it in this prompt.
    """
    out = []
    for ch in text:
        if ch == "\t" or (ch.isprintable() and unicodedata.category(ch) not in ("Cf", "Zl", "Zp")):
            out.append(ch)
        else:
            out.append(ch.encode("unicode_escape").decode("ascii"))
    return "".join(out)


@dataclass(frozen=True)
class ApprovalRequest:
    call_id: str
    tool_name: str
    arguments: dict[str, Any]
    risk_level: RiskLevel
    preview: str | None = None  # e.g. a diff of the change, prepared by the tool


class ApprovalProvider(Protocol):
    def approve(self, request: ApprovalRequest) -> bool:
        """Return True if the action may run."""
        ...


class DenyAllApprovalProvider:
    """Safe default for non-interactive use: nothing is approved."""

    def approve(self, request: ApprovalRequest) -> bool:
        """Always say no."""
        return False


class TerminalApprovalProvider:
    """Ask the person at the terminal. Anything other than 'y' is a denial."""

    LONG_TEXT = 120  # longer argument values are summarized; the preview shows the change

    def __init__(
        self,
        input_fn: Callable[[str], str] = input,
        output_fn: Callable[[str], None] = print,
        color: bool | None = None,
    ) -> None:
        """Use the given input/output functions (the terminal by default)."""
        self._input = input_fn
        self._output = output_fn
        if color is None:
            color = output_fn is print and sys.stdout.isatty() and "NO_COLOR" not in os.environ
        self._color = color

    def approve(self, request: ApprovalRequest) -> bool:
        """Show the action (and its diff, if any) and ask; only 'y' approves."""
        self._output("\n=== APPROVAL REQUIRED ===")
        self._output(f"Tool:       {request.tool_name}")
        self._output(f"Risk level: {request.risk_level}")
        arguments = {key: self._short(value) for key, value in request.arguments.items()}
        shown = json.dumps(arguments, indent=2, default=str, ensure_ascii=False)
        self._output("Arguments:  " + "\n".join(visible(line) for line in shown.splitlines()))
        if request.preview:
            self._output("Preview:")
            for line in request.preview.splitlines():
                self._output("  " + self._paint(visible(line)))
        try:
            answer = self._input("Approve this action? [y/N]: ")
        except (EOFError, KeyboardInterrupt):
            return False
        return answer.strip().lower() == "y"

    def _short(self, value: Any) -> Any:
        """Summarize long text so the prompt stays readable (the diff shows the details)."""
        if isinstance(value, str) and len(value) > self.LONG_TEXT:
            lines = value.count("\n") + 1
            return f"<{len(value):,} characters, {lines} lines>"
        return value

    def _paint(self, line: str) -> str:
        """Green for added lines, red for removed ones (when colors are on)."""
        if not self._color or line.startswith(("+++", "---")):
            return line
        if line.startswith("+"):
            return f"\033[32m{line}\033[0m"
        if line.startswith("-"):
            return f"\033[31m{line}\033[0m"
        return line
