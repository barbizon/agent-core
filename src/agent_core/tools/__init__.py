"""Agent tools.

Tools are described in ``.orvik/tools/<name>/tool.json`` (no code needed to
describe a tool; see ``manifest.py`` for the format). The Python functions they
run live in modules under ``agent_core/tools/``, such as ``files.py``.
"""

from pathlib import Path

from agent_core.config import AGENT_DIR
from agent_core.tools.manifest import load_tools
from agent_core.tools.registry import ExecutionMode, ToolDefinition, ToolRegistry

__all__ = ["ExecutionMode", "ToolDefinition", "ToolRegistry", "build_registry"]

# Module prefix tool functions may come from (also enforced by the process worker).
TOOL_MODULE_PREFIX = "agent_core.tools."
DEFAULT_TOOLS_DIR = Path(AGENT_DIR) / "tools"


def build_registry(tools_dir: str | Path | None = None) -> ToolRegistry:
    """Create the registry from the tool.json files in tools_dir (default .orvik/tools)."""
    folder = Path(tools_dir) if tools_dir is not None else DEFAULT_TOOLS_DIR
    return ToolRegistry(load_tools(folder, (TOOL_MODULE_PREFIX,)))
