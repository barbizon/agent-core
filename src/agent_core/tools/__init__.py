"""Agent tools.

No tools are registered yet. To add one:

1. Write a plain function in a module under ``agent_core/tools/`` (process-mode
   tools must be module-level functions that return JSON-serializable data).
2. Describe it with a ``ToolDefinition`` in ``build_registry`` below.

Example::

    ToolDefinition(
        name="read_file",
        description="Read a UTF-8 text file from the workspace.",
        function=files.read_file,
        risk_level=RiskLevel.READ,
        parameters={"path": {"type": "string", "description": "Relative path"}},
        execution_mode=ExecutionMode.PROCESS,
    )
"""

from agent_core.tools.registry import ExecutionMode, ToolDefinition, ToolRegistry

__all__ = ["ExecutionMode", "ToolDefinition", "ToolRegistry", "build_registry"]

# Module prefix the process worker is allowed to import tool functions from.
TOOL_MODULE_PREFIX = "agent_core.tools."


def build_registry() -> ToolRegistry:
    """Create the registry of available tools (empty for now)."""
    return ToolRegistry([])
