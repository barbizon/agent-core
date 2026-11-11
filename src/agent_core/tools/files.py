"""File and folder tools, limited to one workspace folder.

The workspace is ``AGENT_WORKSPACE`` or, when it is not set, the folder the
agent was started in. Every path the model gives is relative to it; a path
that resolves outside it (``..``, an absolute path, a symlink pointing out)
is refused.

Protected paths (``AGENT_PROTECTED_PATHS``, set by the CLI: the agent's own
``.orvik`` folder with its config, secrets and tools) are invisible to the
tools: hidden from listings, and refused for reading and writing.

The functions read these settings at call time, so they work the same in the
process worker, which inherits the agent's environment.
"""

import os
from pathlib import Path
from typing import Any

from agent_core.errors import PermanentToolError

WORKSPACE_ENV = "AGENT_WORKSPACE"
# Set by the CLI: paths the tools must never see or touch, separated by os.pathsep.
# Otherwise one approved write could change the agent's config or plant a tool,
# and one read could send its API key to the AI.
PROTECTED_PATHS_ENV = "AGENT_PROTECTED_PATHS"
MAX_READ_BYTES = 200_000
MAX_LIST_ENTRIES = 500


def workspace_root() -> Path:
    """The folder every tool path is relative to."""
    return Path(os.getenv(WORKSPACE_ENV) or Path.cwd()).resolve()


def protected_paths() -> list[Path]:
    """Paths the tools may not see or touch (the agent's own folder and files)."""
    raw = os.getenv(PROTECTED_PATHS_ENV, "")
    return [Path(p).resolve() for p in raw.split(os.pathsep) if p]


def _file_id(path: Path) -> tuple[int, int] | None:
    """The file's identity (device, inode), the same however its name is spelled."""
    try:
        stat = path.stat()
    except OSError:
        return None
    return stat.st_dev, stat.st_ino


def is_protected(target: Path) -> bool:
    """True if target is a protected path or inside one.

    Names alone are not enough: macOS ignores letter case, so ".ORVIK/.env" opens
    the same file as ".orvik/.env". So we compare file identities (device + inode)
    of the target and each of its parent folders, plus case-folded names for paths
    that don't exist yet.
    """
    protected = protected_paths()
    if not protected:
        return False
    names = {str(p).casefold() for p in protected}
    ids = {file_id for p in protected if (file_id := _file_id(p)) is not None}
    for candidate in (target, *target.parents):
        if str(candidate).casefold() in names:
            return True
        file_id = _file_id(candidate)
        if file_id is not None and file_id in ids:
            return True
    return False


def resolve_path(path: str) -> Path:
    """Turn a model-supplied path into an absolute path inside the workspace."""
    root = workspace_root()
    target = (root / path).resolve()
    if not target.is_relative_to(root):
        raise PermanentToolError(f"Path is outside the workspace: {path}")
    if is_protected(target):
        raise PermanentToolError(f"{path} belongs to the agent itself; tools may not use it")
    return target


def _relative(target: Path) -> str:
    """The path as the model should see it: relative to the workspace."""
    return target.relative_to(workspace_root()).as_posix() or "."


def list_folder(path: str = ".") -> dict[str, Any]:
    """List the files and folders in a workspace folder, folders first."""
    target = resolve_path(path)
    if not target.is_dir():
        raise PermanentToolError(f"Not a folder: {path}")
    children = sorted(
        (child for child in target.iterdir() if not is_protected(child.resolve())),
        key=lambda child: (not child.is_dir(), child.name),
    )
    entries = []
    for child in children[:MAX_LIST_ENTRIES]:
        if child.is_dir():
            entries.append({"name": child.name, "type": "folder"})
        else:
            entries.append({"name": child.name, "type": "file", "size": child.stat().st_size})
    return {
        "path": _relative(target),
        "entries": entries,
        "truncated": len(children) > MAX_LIST_ENTRIES,
    }


def read_file(path: str) -> dict[str, Any]:
    """Read a UTF-8 text file from the workspace."""
    target = resolve_path(path)
    if not target.is_file():
        raise PermanentToolError(f"Not a file: {path}")
    size = target.stat().st_size
    if size > MAX_READ_BYTES:
        raise PermanentToolError(f"File is {size} bytes; the limit is {MAX_READ_BYTES}")
    try:
        content = target.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raise PermanentToolError(f"Not a UTF-8 text file: {path}") from None
    return {"path": _relative(target), "content": content}


def write_file(path: str, content: str) -> dict[str, Any]:
    """Create or overwrite a UTF-8 text file in the workspace, creating its folders."""
    target = resolve_path(path)
    if target.is_dir():
        raise PermanentToolError(f"Path is a folder: {path}")
    created = not target.exists()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return {
        "path": _relative(target),
        "created": created,
        "bytes_written": len(content.encode("utf-8")),
    }
