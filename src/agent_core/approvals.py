"""Human-in-the-loop approval for non-read actions."""

import json
from dataclasses import dataclass
from typing import Any, Callable, Protocol

from agent_core.permissions import RiskLevel


@dataclass(frozen=True)
class ApprovalRequest:
    call_id: str
    tool_name: str
    arguments: dict[str, Any]
    risk_level: RiskLevel


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

    def __init__(
        self,
        input_fn: Callable[[str], str] = input,
        output_fn: Callable[[str], None] = print,
    ) -> None:
        """Use the given input/output functions (the terminal by default)."""
        self._input = input_fn
        self._output = output_fn

    def approve(self, request: ApprovalRequest) -> bool:
        """Show the action and ask the user; only 'y' approves."""
        self._output("\n=== APPROVAL REQUIRED ===")
        self._output(f"Tool:       {request.tool_name}")
        self._output(f"Risk level: {request.risk_level}")
        self._output(
            "Arguments:  "
            + json.dumps(request.arguments, indent=2, default=str)
        )
        try:
            answer = self._input("Approve this action? [y/N]: ")
        except (EOFError, KeyboardInterrupt):
            return False
        return answer.strip().lower() == "y"
