"""File and folder tools, limited to one workspace folder.

Rules every tool here follows (checked in ``resolve_path``):

* **Workspace.** ``AGENT_WORKSPACE`` or, when it is not set, the folder the
  agent was started in. Every path the model gives is relative to it; a path
  that resolves outside it (``..``, an absolute path, a symlink pointing out)
  is refused.
* **Related folders** (``ORWICK_RELATED_DIRS``, set by the CLI from ``--related`` and
  ``/hld --with``): other projects that may be read (``../billing/...`` or an absolute
  path) but never written, so a scan can follow calls into neighbouring services.
* **Protected paths** (``AGENT_PROTECTED_PATHS``, set by the CLI: the agent's
  own ``.orwick`` folder with its config, secrets and tools) are invisible:
  hidden from listings and search, refused for reading and writing.
* **Secret files** (``.env``, keys, ``.ssh/``, ``.git/config``, ...) are
  treated the same way, so their content never reaches the AI.
* **No-write files** (``.git/hooks/``, shell startup files) can be read but
  never changed: writing them would run code later, outside any approval.
* **Read before write.** ``read_file`` returns a ``version`` (a fingerprint of
  the file). Changing an existing file requires that version: it proves the AI
  saw the file, and refuses the change if the file changed on disk since.

All names are compared ignoring letter case (macOS treats ``.ENV`` as
``.env``), after symlinks are resolved.

The functions read their settings from the environment at call time, so they
work the same in the process worker, which inherits the agent's environment.
"""

import difflib
import fnmatch
import hashlib
import os
import re
import time
from pathlib import Path
from typing import Any

from agent_core.errors import PermanentToolError

WORKSPACE_ENV = "AGENT_WORKSPACE"
# Set by the CLI: paths the tools must never see or touch, separated by os.pathsep.
# Otherwise one approved write could change the agent's config or plant a tool,
# and one read could send its API key to the AI.
PROTECTED_PATHS_ENV = "AGENT_PROTECTED_PATHS"
# Set by the CLI: related project folders, readable but never writable, separated by os.pathsep.
RELATED_DIRS_ENV = "ORWICK_RELATED_DIRS"
CODE_ROOT_ENV = "ORWICK_CODE_ROOT"   # the folder that holds all the repos; default: found

MAX_READ_BYTES = 200_000
MAX_LIST_ENTRIES = 500

# --- Secret and no-write rules (all matched case-insensitively) ---------------

# Folders whose whole content is secret, anywhere in the path.
SECRET_FOLDERS = (".ssh", ".aws", ".gnupg", ".config/gh", ".docker")
# File names that are secret.
SECRET_NAMES = (
    ".env", ".env.*", "*.pem", "*.key", "*.p12", "*.pfx", "*.kdbx",
    "id_rsa*", "id_dsa*", "id_ecdsa*", "id_ed25519*",
    ".netrc", ".npmrc", ".pypirc", ".pgpass",
)
# Names that look like secrets but are safe to share (templates, public keys).
NOT_SECRET_NAMES = (".env.example", ".env.sample", ".env.template", "*.pub")
# Exact relative paths that are secret (git config can hold tokens and run commands).
SECRET_PATHS = (".git/config",)

# Readable, but never writable: writing them would run code later, without
# anyone running it on purpose. Each entry maps to the reason shown to the AI.
NO_WRITE_FOLDERS = {
    ".git/hooks": "git runs these scripts on commit, push and checkout",
    ".vscode": "the editor can run tasks and settings from this folder",
    ".github/workflows": "CI runs these with the repository's secrets after a push",
}
NO_WRITE_NAMES = {
    ".zshrc": "shell startup files run automatically",
    ".zshenv": "shell startup files run automatically",
    ".zprofile": "shell startup files run automatically",
    ".zlogin": "shell startup files run automatically",
    ".bashrc": "shell startup files run automatically",
    ".bash_profile": "shell startup files run automatically",
    ".profile": "shell startup files run automatically",
    ".envrc": "direnv runs it when you enter the folder",
}

# --- grep ----------------------------------------------------------------------

GREP_SKIP_FOLDERS = (".git", ".venv", "venv", "node_modules", "__pycache__",
                     ".idea", ".pytest_cache", ".mypy_cache", ".tox")
GREP_MAX_FILE_BYTES = 1_000_000
GREP_DEFAULT_MATCHES = 50
GREP_MAX_MATCHES = 500
GREP_MAX_LINE_CHARS = 200
GREP_TIME_BUDGET = 12.0   # seconds; then return what was found (the tool's hard limit is longer)

PREVIEW_MAX_LINES = 120


def workspace_root() -> Path:
    """The folder every tool path is relative to."""
    return Path(os.getenv(WORKSPACE_ENV) or Path.cwd()).resolve()


def related_roots() -> list[Path]:
    """Related project folders: readable like the workspace, never writable."""
    raw = os.getenv(RELATED_DIRS_ENV, "")
    return [p for p in (Path(x).resolve() for x in raw.split(os.pathsep) if x) if p.is_dir()]


def code_root() -> Path | None:
    """The folder that holds this repo and the repos beside it (e.g. ~/Documents/go):
    the code_root setting (ORWICK_CODE_ROOT), else the workspace's parent when it holds at
    least two other git repos. Never home or /."""
    root = workspace_root()
    named = os.getenv(CODE_ROOT_ENV)
    candidate = Path(named).expanduser().resolve() if named else root.parent
    if candidate in (Path.home().resolve(), Path("/")) or not candidate.is_dir():
        return None
    if named:   # set in the settings (code_root): used as given
        return candidate
    repos = 0
    try:
        for child in candidate.iterdir():
            if child != root and (child / ".git").exists():
                repos += 1
                if repos >= 2:
                    return candidate
    except OSError:
        return None
    return None


def open_code_root() -> Path | None:
    """Let the tools read the code root (never write it), for skills that follow a flow
    into other repos. Returns it, or None when there is none."""
    folder = code_root()
    if folder is None:
        return None
    current = [p for p in os.getenv(RELATED_DIRS_ENV, "").split(os.pathsep) if p]
    if str(folder) not in current:
        os.environ[RELATED_DIRS_ENV] = os.pathsep.join([*current, str(folder)])
    return folder


def readable_root(target: Path) -> Path | None:
    """The workspace or related folder the (resolved) target is in, or None."""
    root = workspace_root()
    if target.is_relative_to(root):
        return root
    return next((r for r in related_roots() if target.is_relative_to(r)), None)


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

    Names alone are not enough: macOS ignores letter case, so ".ORWICK/.env" opens
    the same file as ".orwick/.env". So we compare file identities (device + inode)
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


def _relative_parts(target: Path) -> list[str]:
    """The target's path inside the workspace, as case-folded parts."""
    try:
        relative = target.relative_to(workspace_root())
    except ValueError:
        relative = target
    return [part.casefold() for part in relative.parts]


def _contains(parts: list[str], sequence: str, at_end: bool = False) -> bool:
    """True if the folder sequence (e.g. ".config/gh") appears in parts."""
    wanted = sequence.casefold().split("/")
    size = len(wanted)
    starts = [len(parts) - size] if at_end else range(len(parts) - size + 1)
    return any(start >= 0 and parts[start:start + size] == wanted for start in starts)


def _matches_any(name: str, patterns: tuple[str, ...]) -> bool:
    """True if the (case-folded) name matches one of the wildcard patterns."""
    return any(fnmatch.fnmatchcase(name, pattern.casefold()) for pattern in patterns)


def secret_reason(target: Path) -> str | None:
    """Why this path is secret, or None if it isn't."""
    parts = _relative_parts(target)
    if not parts:
        return None
    for folder in SECRET_FOLDERS:
        if _contains(parts, folder):
            return f"inside the secret folder {folder}/"
    for path in SECRET_PATHS:
        if _contains(parts, path, at_end=True):
            return f"{path} can hold tokens"
    name = parts[-1]
    if _matches_any(name, SECRET_NAMES) and not _matches_any(name, NOT_SECRET_NAMES):
        return "its name marks it as a key, password or token file"
    return None


def no_write_reason(target: Path) -> str | None:
    """Why this path may be read but not written, or None."""
    parts = _relative_parts(target)
    for folder, why in NO_WRITE_FOLDERS.items():
        if _contains(parts, folder):
            return f"it is inside {folder}/ ({why})"
    if parts:
        for name, why in NO_WRITE_NAMES.items():
            if fnmatch.fnmatchcase(parts[-1], name.casefold()):
                return why
    return None


def is_hidden(target: Path) -> bool:
    """True if a tool must not even show this path (protected or secret)."""
    return is_protected(target) or secret_reason(target) is not None


def resolve_path(path: str, for_write: bool = False) -> Path:
    """Turn a model-supplied path into an absolute path inside the workspace, or refuse."""
    root = workspace_root()
    target = (root / path).resolve()
    if not target.is_relative_to(root):
        related = readable_root(target)
        if related is None:
            extra = (" or the related folders (" + ", ".join(_relative(r) for r in related_roots()) + ")"
                     if related_roots() else "")
            raise PermanentToolError(f"Path is outside the workspace{extra}: {path}")
        if for_write:
            raise PermanentToolError(f"{path} is in a related folder ({_relative(related)}); "
                                     "related folders can be read but not changed.")
    if is_protected(target):
        raise PermanentToolError(f"{path} belongs to the agent itself; tools may not use it")
    reason = secret_reason(target)
    if reason:
        raise PermanentToolError(f"{path} is protected as a secret: {reason}. Tools may not open it.")
    if for_write:
        reason = no_write_reason(target)
        if reason:
            raise PermanentToolError(f"{path} can be read but not changed: {reason}.")
    return target


def _relative(target: Path) -> str:
    """The path as the model should see it: relative to the workspace (a related folder's
    files as ../other-project/...)."""
    root = workspace_root()
    if target.is_relative_to(root):
        return target.relative_to(root).as_posix() or "."
    return Path(os.path.relpath(target, root)).as_posix()


def file_version(data: bytes) -> str:
    """A short fingerprint of a file's bytes; changes whenever the content changes."""
    return hashlib.sha256(data).hexdigest()[:16]


# --- list_folder / read_file ------------------------------------------------------

def list_folder(path: str = ".") -> dict[str, Any]:
    """List the files and folders in a workspace folder, folders first."""
    target = resolve_path(path)
    if not target.is_dir():
        raise PermanentToolError(f"Not a folder: {path}")
    children = sorted(
        (child for child in target.iterdir() if not is_hidden(child.resolve())),
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


def _read_text(target: Path, path: str) -> tuple[str, bytes]:
    """Read a UTF-8 text file exactly as stored (line endings kept)."""
    if not target.is_file():
        raise PermanentToolError(f"Not a file: {path}")
    size = target.stat().st_size
    if size > MAX_READ_BYTES:
        raise PermanentToolError(f"File is {size} bytes; the limit is {MAX_READ_BYTES}")
    data = target.read_bytes()
    try:
        return data.decode("utf-8"), data
    except UnicodeDecodeError:
        raise PermanentToolError(f"Not a UTF-8 text file: {path}") from None


READ_MAX_LINES = 2000
READ_WHOLE_MAX_CHARS = 16_000   # a whole-file read above this returns the first lines and a note


def read_file(path: str, start_line: int = 1, line_count: int | None = None) -> dict[str, Any]:
    """Read a UTF-8 text file, whole or just some lines; the version (always of the
    whole file) is needed later to change it."""
    target = resolve_path(path)
    content, data = _read_text(target, path)
    result: dict[str, Any] = {"path": _relative(target), "version": file_version(data)}
    if start_line == 1 and line_count is None and len(content) <= READ_WHOLE_MAX_CHARS:
        result["content"] = content
        return result
    lines = content.splitlines(keepends=True)
    if start_line == 1 and line_count is None:
        # Too big for one result: the lines that fit, and how to get the rest. Cutting
        # mid-line instead would make the AI read it all again in parts.
        shown, size = 0, 0
        while shown < len(lines) and size + len(lines[shown]) <= READ_WHOLE_MAX_CHARS:
            size += len(lines[shown])
            shown += 1
        shown = max(shown, 1)
        result.update({"content": "".join(lines[:shown]), "start_line": 1, "end_line": shown,
                       "total_lines": len(lines),
                       "note": f"Only lines 1-{shown} of {len(lines)} shown (the file is large). Read "
                               "other parts with start_line and line_count, or grep for what you need."})
        return result
    start = max(1, int(start_line))
    count = max(1, min(int(line_count or READ_MAX_LINES), READ_MAX_LINES))
    chosen = lines[start - 1:start - 1 + count]
    result.update({"content": "".join(chosen), "start_line": start,
                   "end_line": start + len(chosen) - 1, "total_lines": len(lines)})
    return result


# --- grep -------------------------------------------------------------------------

def grep(
    pattern: str,
    path: str = ".",
    glob: str | None = None,
    regex: bool = False,
    ignore_case: bool = True,
    max_matches: int = GREP_DEFAULT_MATCHES,
) -> dict[str, Any]:
    """Search text files for a pattern; stops after max_matches and says so."""
    if not pattern:
        raise PermanentToolError("pattern must not be empty")
    max_matches = max(1, min(int(max_matches), GREP_MAX_MATCHES))
    flags = re.IGNORECASE if ignore_case else 0
    try:
        compiled = re.compile(pattern if regex else re.escape(pattern), flags)
    except re.error as exc:
        raise PermanentToolError(f"Invalid regular expression: {exc}") from None

    top = resolve_path(path)
    matches: list[dict[str, Any]] = []
    searched = skipped = 0
    truncated = False

    top_root = readable_root(top) or workspace_root()   # never follow a symlink out of it
    deadline = time.monotonic() + GREP_TIME_BUDGET
    out_of_time = False
    for file_path in _files_to_search(top, glob):
        if time.monotonic() > deadline:
            out_of_time = True
            break
        # A symlink inside the workspace may point outside it: never follow it out.
        if not file_path.resolve().is_relative_to(top_root) or is_hidden(file_path):
            skipped += 1
            continue
        try:
            data = file_path.read_bytes() if file_path.stat().st_size <= GREP_MAX_FILE_BYTES else None
        except OSError:
            data = None
        if data is None or b"\0" in data[:8192]:
            skipped += 1  # too big, unreadable or binary
            continue
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            skipped += 1
            continue
        searched += 1
        for number, line in enumerate(text.splitlines(), start=1):
            if compiled.search(line):
                if len(matches) == max_matches:
                    truncated = True
                    break
                shown = line.strip()
                if len(shown) > GREP_MAX_LINE_CHARS:
                    shown = shown[:GREP_MAX_LINE_CHARS] + "…"
                matches.append({"path": _relative(file_path), "line": number, "text": shown})
        if truncated:
            break

    if out_of_time:
        truncated = True
        note = (f"Stopped after {GREP_TIME_BUDGET:.0f}s ({searched} files searched): the folder is big. "
                "These are the matches so far; narrow the search with path (a sub-folder) or glob.")
    elif truncated:
        note = (f"Stopped after {max_matches} matches; there are more. Narrow the search "
                f"with path or glob, or raise max_matches (up to {GREP_MAX_MATCHES}).")
    elif not matches:
        note = "No matches."
    else:
        note = f"All {len(matches)} matches shown."
    return {
        "pattern": pattern,
        "path": _relative(top),
        "matches": matches,
        "files_searched": searched,
        "files_skipped": skipped,
        "truncated": truncated,
        "note": note,
    }


def _files_to_search(top: Path, glob: str | None):
    """Yield files under top (or top itself), skipping noise and hidden folders."""
    if top.is_file():
        yield top
        return
    if not top.is_dir():
        raise PermanentToolError(f"Not found: {_relative(top)}")
    skip = {name.casefold() for name in GREP_SKIP_FOLDERS}
    for folder, subfolders, filenames in os.walk(top):
        folder_path = Path(folder)
        subfolders[:] = sorted(
            name for name in subfolders
            if name.casefold() not in skip and not is_hidden((folder_path / name).resolve())
        )
        for name in sorted(filenames):
            if glob and not fnmatch.fnmatchcase(name.casefold(), glob.casefold()):
                continue
            yield (folder_path / name).resolve()


# --- write_file / edit_file (+ previews for the approval prompt) ------------------

def _check_version(target: Path, path: str, version: str | None) -> tuple[str, bytes]:
    """For an existing file: require the version from read_file and check it still matches."""
    content, data = _read_text(target, path)
    if not version:
        raise PermanentToolError(
            f"{path} already exists. Read it first with read_file and pass its version."
        )
    if version != file_version(data):
        raise PermanentToolError(
            f"{path} changed since you read it (version mismatch). Read it again."
        )
    return content, data


def _plan_write(path: str, content: str, version: str | None) -> tuple[Path, str | None]:
    """Check a write_file call; return the target and its current content (None if new)."""
    target = resolve_path(path, for_write=True)
    if target.is_dir():
        raise PermanentToolError(f"Path is a folder: {path}")
    if target.exists():
        before, _ = _check_version(target, path, version)
        return target, before
    return target, None


def _plan_edit(
    path: str, old_text: str, new_text: str, version: str, replace_all: bool
) -> tuple[Path, str, str, int]:
    """Check an edit_file call; return target, content before, content after, replacements."""
    target = resolve_path(path, for_write=True)
    if not target.exists():
        raise PermanentToolError(f"{path} does not exist. Use write_file to create it.")
    before, _ = _check_version(target, path, version)
    if not old_text:
        raise PermanentToolError("old_text must not be empty")
    if old_text == new_text:
        raise PermanentToolError("old_text and new_text are the same; nothing would change")
    count = before.count(old_text)
    if count == 0:
        raise PermanentToolError(
            f"old_text was not found in {path}. Copy it exactly from read_file, including spaces."
        )
    if count > 1 and not replace_all:
        raise PermanentToolError(
            f"old_text appears {count} times in {path}. Add surrounding text to make it "
            "unique, or set replace_all to change every occurrence."
        )
    after = before.replace(old_text, new_text) if replace_all else before.replace(old_text, new_text, 1)
    return target, before, after, (count if replace_all else 1)


def _save(target: Path, content: str) -> bytes:
    """Write the content exactly (no line-ending changes) and return the bytes written."""
    data = content.encode("utf-8")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return data


def write_file(path: str, content: str, version: str | None = None) -> dict[str, Any]:
    """Create a file, or replace one you have read (pass its version)."""
    target, before = _plan_write(path, content, version)
    data = _save(target, content)
    return {
        "path": _relative(target),
        "created": before is None,
        "bytes_written": len(data),
        "version": file_version(data),
    }


def edit_file(
    path: str, old_text: str, new_text: str, version: str, replace_all: bool = False
) -> dict[str, Any]:
    """Replace an exact piece of text in a file you have read (pass its version)."""
    target, _, after, replacements = _plan_edit(path, old_text, new_text, version, replace_all)
    data = _save(target, after)
    return {"path": _relative(target), "replacements": replacements, "version": file_version(data)}


def preview_write(path: str, content: str, version: str | None = None) -> str:
    """What write_file would change, as a diff, for the approval prompt."""
    target, before = _plan_write(path, content, version)
    if before is None:
        return _diff(_relative(target), "", content, new_file=True)
    return _diff(_relative(target), before, content)


def preview_edit(
    path: str, old_text: str, new_text: str, version: str, replace_all: bool = False
) -> str:
    """What edit_file would change, as a diff, for the approval prompt."""
    target, before, after, replacements = _plan_edit(path, old_text, new_text, version, replace_all)
    header = f"{replacements} replacement(s)\n" if replacements > 1 else ""
    return header + _diff(_relative(target), before, after)


def _diff(name: str, before: str, after: str, new_file: bool = False) -> str:
    """A unified diff (- removed, + added), cut to a readable length."""
    lines = list(difflib.unified_diff(
        before.splitlines(), after.splitlines(),
        fromfile="(new file)" if new_file else f"{name} (before)",
        tofile=f"{name} (after)", lineterm="", n=2,
    ))
    if not lines:
        return "(no change)"
    if len(lines) > PREVIEW_MAX_LINES:
        hidden = len(lines) - PREVIEW_MAX_LINES
        lines = lines[:PREVIEW_MAX_LINES] + [f"... ({hidden} more diff lines not shown)"]
    return "\n".join(lines)
