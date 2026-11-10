"""Run a tool in a daemon thread with a deadline.

Python cannot kill a thread. On timeout the thread is *abandoned*: the agent
moves on, but the work may still be running. A daemon thread at least won't
block interpreter exit. Use process mode for tools that must really stop.
"""

import threading
from typing import Any, Callable

from agent_core.errors import ToolExecutionError, ToolOperationError


def run_in_thread(
    function: Callable[..., Any],
    arguments: dict[str, Any],
    timeout_seconds: float,
    tool_name: str,
) -> Any:
    """Run a tool in a background thread; give up waiting after the timeout."""
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")

    outcome: dict[str, Any] = {}

    def target() -> None:
        """Run the tool and remember its result or error."""
        try:
            outcome["data"] = function(**arguments)
        except BaseException as exc:  # reported to the caller below
            outcome["error"] = exc

    thread = threading.Thread(target=target, name=f"tool-{tool_name}", daemon=True)
    thread.start()
    thread.join(timeout_seconds)

    if thread.is_alive():
        raise ToolExecutionError(
            f"Tool {tool_name!r} timed out after {timeout_seconds}s",
            error_type="timeout",
        )

    error = outcome.get("error")
    if error is None:
        return outcome.get("data")
    if isinstance(error, ToolExecutionError):
        raise error
    if isinstance(error, ToolOperationError):
        raise ToolExecutionError(str(error), error_type=error.error_type) from error
    raise ToolExecutionError(
        f"Tool {tool_name!r} failed: {type(error).__name__}: {error}",
        error_type="execution_error",
    ) from error
