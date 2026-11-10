"""One-time action authorization.

After a human approves an action, the gateway issues a single-use token bound
to the *exact* (tool, arguments) pair. The token is consumed immediately
before execution. This guarantees:

* the executed action is byte-for-byte the approved action;
* an approval cannot be replayed;
* no code path can execute a non-read tool without passing the approval step.

The store is trusted infrastructure and must never be exposed to the model.
"""

import json
import secrets
import threading
from dataclasses import dataclass
from typing import Any

from agent_core.errors import ActionAuthorizationError


def canonical_action(tool_name: str, arguments: dict[str, Any]) -> str:
    """Stable representation of an action; key ordering does not matter."""
    try:
        return json.dumps(
            {"tool": tool_name, "arguments": arguments},
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ActionAuthorizationError(
            "Action arguments must be JSON serializable"
        ) from exc


@dataclass(frozen=True)
class ActionAuthorization:
    token: str


class ActionAuthorizationStore:
    def __init__(self) -> None:
        """Start with no pending authorizations."""
        self._lock = threading.Lock()
        self._pending: dict[str, str] = {}

    def issue(self, tool_name: str, arguments: dict[str, Any]) -> ActionAuthorization:
        """Create a one-time token for exactly this tool call."""
        action = canonical_action(tool_name, arguments)
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._pending[token] = action
        return ActionAuthorization(token=token)

    def consume(
        self,
        authorization: ActionAuthorization | None,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> None:
        """Use up a token; fail if it is missing, already used, or for a different action."""
        if authorization is None:
            raise ActionAuthorizationError("Action requires authorization")

        action = canonical_action(tool_name, arguments)

        with self._lock:
            approved_action = self._pending.pop(authorization.token, None)

        if approved_action is None:
            raise ActionAuthorizationError("Authorization is invalid or already used")
        if approved_action != action:
            raise ActionAuthorizationError("Authorization does not match the action")

    @property
    def pending_count(self) -> int:
        """How many tokens were issued but not used yet."""
        with self._lock:
            return len(self._pending)
