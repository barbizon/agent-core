"""``run_subagent``: a helper agent for a self-contained side task.

Claude Code's skills often say "dispatch a subagent" (Claude's Agent/Task tool).
Orwick's version starts a second agent in this process with a fresh, empty
conversation, the same AI backend, and Orwick's tools behind the same gateway:
approvals, sandbox and audit log apply to every call it makes. By default it
gets only the read-only tools. Only its final report comes back, so the files it
reads never enter the main conversation, which keeps that one small.

The CLI sets ``RUNNER`` when it builds the agent (it holds the settings, the
gateway and the model factory); without it the tool says it isn't available.
A helper can't start helpers of its own.
"""

from typing import Any, Callable

from agent_core.errors import PermanentToolError

MAX_STEPS = 60
DEFAULT_STEPS = 30

# (task, allow_changes, max_steps) -> the helper's final answer. Set by the CLI.
RUNNER: Callable[[str, bool, int], str] | None = None


def run_subagent(task: str, allow_changes: bool = False, max_steps: int = DEFAULT_STEPS) -> dict[str, Any]:
    if RUNNER is None:
        raise PermanentToolError("Helper agents aren't available here.")
    task = str(task or "").strip()
    if not task:
        raise PermanentToolError("task is empty: say what the helper should do and report")
    steps = max(1, min(int(max_steps or DEFAULT_STEPS), MAX_STEPS))
    return {"report": RUNNER(task, bool(allow_changes), steps)}
