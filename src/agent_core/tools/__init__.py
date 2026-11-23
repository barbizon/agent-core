"""Agent tools.

Tools are described by ``tool.json`` files (no code needed to describe a tool;
see ``manifest.py`` for the format). The Python functions they run live in
modules under ``agent_core/tools/``, such as ``files.py``.

Orwick loads tools from three places, in this order:

1. **Built-in** (``agent_core/tools/builtin/``): the standard tools, shipped with
   Orwick, so every folder has them.
2. **Your tools** (``~/.orwick/tools/``): extra tools for every folder.
3. **The folder's tools** (``<folder>/.orwick/tools/``): extra tools for one project.

A built-in tool can't be replaced by one of the same name (that one is skipped
with a note), and a folder's tool wins over one of yours with the same name.
Whatever the source, a tool.json may only run Orwick's own tool functions at the
risk the code sets for them (``manifest.TOOL_FUNCTIONS``).
"""

from dataclasses import dataclass, field
from pathlib import Path

from agent_core.config import AGENT_DIR
from agent_core.tools.manifest import load_tools
from agent_core.tools.registry import ExecutionMode, ToolDefinition, ToolRegistry

__all__ = ["ExecutionMode", "ToolDefinition", "ToolRegistry", "build_registry", "load_all_tools"]

# Module prefix tool functions may come from (also enforced by the process worker).
TOOL_MODULE_PREFIX = "agent_core.tools."
BUILTIN_TOOLS_DIR = Path(__file__).parent / "builtin"
USER_TOOLS_DIR = Path.home() / AGENT_DIR / "tools"
DEFAULT_TOOLS_DIR = Path(AGENT_DIR) / "tools"

BUILTIN, YOURS, FOLDER = "built-in", f"~/{AGENT_DIR}/tools", f"{AGENT_DIR}/tools"


@dataclass
class LoadedTools:
    registry: ToolRegistry
    origins: dict[str, str] = field(default_factory=dict)  # tool name -> BUILTIN / YOURS / FOLDER
    notes: list[str] = field(default_factory=list)          # tools skipped or replaced, for the user


def build_registry(tools_dir: str | Path | None = None) -> ToolRegistry:
    """The tools in one folder only (default: the built-in ones)."""
    folder = Path(tools_dir) if tools_dir is not None else BUILTIN_TOOLS_DIR
    return ToolRegistry(load_tools(folder, (TOOL_MODULE_PREFIX,)))


def load_all_tools(folder_dir: str | Path = DEFAULT_TOOLS_DIR,
                   user_dir: str | Path | None = USER_TOOLS_DIR,
                   builtin_dir: str | Path = BUILTIN_TOOLS_DIR) -> LoadedTools:
    """Built-in tools, then yours, then the folder's (see the module docstring)."""
    chosen: dict[str, ToolDefinition] = {}
    origins: dict[str, str] = {}
    notes: list[str] = []
    layers = [(BUILTIN, builtin_dir), (YOURS, user_dir), (FOLDER, folder_dir)]
    for origin, directory in layers:
        if directory is None:
            continue
        if origin != BUILTIN and Path(directory).resolve() == Path(builtin_dir).resolve():
            continue
        for tool in load_tools(directory, (TOOL_MODULE_PREFIX,)):
            if origins.get(tool.name) == BUILTIN:
                notes.append(f"{origin}/{tool.name} skipped: a built-in tool has that name")
                continue
            if tool.name in origins:
                notes.append(f"{origin}/{tool.name} replaces the one in {origins[tool.name]}")
            chosen[tool.name] = tool
            origins[tool.name] = origin
    return LoadedTools(ToolRegistry(list(chosen.values())), origins, notes)
