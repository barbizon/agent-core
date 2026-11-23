"""One folder per Orwick run: ``.orwick/sessions/<id>/``.

A session is one conversation. Its records live together, next to the work:

* ``conversation.json``: the conversation (your messages, answers, tool calls
  and their results), so you can come back to it with ``--resume <id>``.
* ``meta.json``: title (your first message, shortened), times, message count,
  and the ticket the session works on (``/feature``); what session lists show.
* ``status.json``: the /status numbers and the tool list (see status_file.py).
  Apps such as the Orwick Mac app read it.
* ``audit.jsonl``: the audit log (unless ``audit_log`` points elsewhere).
* ``tools.json``: tools switched on or off for this session only, on top of the
  settings (``/tools on|off <name>``, or the Mac app's Info panel). Kept on resume.
* ``lock``: held with an operating-system lock by the Orwick that has the
  session open, so it is never open in two places at once. The system frees
  the lock when that Orwick ends, however it ends (even a crash), so a stale
  lock can't block you. The file also notes the process id, for messages.

All files are saved before each prompt and are readable only by you. The folder
sits inside ``.orwick/``, which Orwick's tools can't see, so the AI can't read or
change its own records. A ``.gitignore`` keeps sessions out of git. Only the
``KEEP_SESSIONS`` most recently used folders are kept; a session you never sent
a message in is removed when Orwick exits.
"""

import fcntl
import json
import os
import re
import secrets
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent_core.config import AGENT_DIR, TOOL_NAME
from agent_core.errors import ConfigError
from agent_core.status_file import write as write_json

SESSIONS_DIR = Path(AGENT_DIR) / "sessions"
KEEP_SESSIONS = 50
# Date and time first, so names sort oldest → newest; a random part avoids clashes.
SESSION_ID = re.compile(r"^\d{4}-\d{2}-\d{2}_\d{6}_[0-9a-f]{4,8}$")
FEATURE_KEY = re.compile(r"^[A-Z][A-Z0-9]{1,14}-\d{1,7}$")  # a ticket key as Jira, Linear and others write it, e.g. PROJ-16765
FORMAT = 1
TITLE_LENGTH = 80
NAME_FILE = "name.txt"   # the name you gave the session (/name, or the app's Info panel)
NAME_LENGTH = 80
ROLES = {"user", "assistant", "tool"}


class SessionBusy(ConfigError):
    """The session is open in another Orwick."""


@dataclass(frozen=True)
class SessionFolder:
    id: str
    path: Path

    @property
    def status_file(self) -> Path:
        return self.path / "status.json"

    @property
    def audit_log(self) -> Path:
        return self.path / "audit.jsonl"

    @property
    def conversation_file(self) -> Path:
        return self.path / "conversation.json"

    @property
    def meta_file(self) -> Path:
        return self.path / "meta.json"

    @property
    def stats_file(self) -> Path:
        return self.path / "stats.json"

    @property
    def skills_file(self) -> Path:
        return self.path / "skills.json"

    @property
    def name_file(self) -> Path:
        return self.path / NAME_FILE

    @property
    def lock_file(self) -> Path:
        return self.path / "lock"

    @property
    def tools_file(self) -> Path:
        return self.path / "tools.json"

    @property
    def related_file(self) -> Path:
        return self.path / "related.json"


def new_id(now: float | None = None) -> str:
    """e.g. 2026-11-12_114105_a3f9"""
    stamp = time.strftime("%Y-%m-%d_%H%M%S", time.localtime(time.time() if now is None else now))
    return f"{stamp}_{secrets.token_hex(2)}"


def open_session(session_id: str | None = None, root: Path = SESSIONS_DIR,
                 keep: int = KEEP_SESSIONS, resume: bool = False) -> SessionFolder:
    """Open a session: a new one (a new id unless one is given), or with ``resume``
    an existing one that has a saved conversation. Takes the session's lock and
    removes old sessions."""
    session_id = session_id or new_id()
    if not SESSION_ID.match(session_id):
        raise ConfigError(f"session id must look like 2026-11-12_114105_a3f9, got {session_id!r}")
    folder = SessionFolder(session_id, root / session_id)
    if resume:
        if folder.path.is_symlink() or not folder.conversation_file.is_file():
            raise ConfigError(f"no saved session {session_id} in {root}")
    else:
        root.mkdir(parents=True, exist_ok=True)
        ignore = root / ".gitignore"
        if not ignore.exists():
            ignore.write_text("# Orwick session records: keep them out of git.\n*\n", encoding="utf-8")
        folder.path.mkdir(exist_ok=True)
    acquire(folder)
    remove_old_sessions(root, keep, current=session_id)
    return folder


# --- One Orwick per session ------------------------------------------------------------

_held: dict[Path, int] = {}  # lock file -> open file descriptor holding its lock
# A session that was just closed may need a moment to save and let go of its lock.
LOCK_WAIT_SECONDS = 3.0


def acquire(folder: SessionFolder) -> None:
    """Take the session's lock, or raise SessionBusy if another Orwick holds it."""
    path = folder.lock_file.resolve()
    if path in _held:
        return
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    deadline = time.monotonic() + LOCK_WAIT_SECONDS
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except BlockingIOError:
            if time.monotonic() < deadline:
                time.sleep(0.1)
                continue
        owner = _read_owner(fd)
        os.close(fd)
        raise SessionBusy(f"session {folder.id} is open in another Orwick"
                          + (f" (process {owner})" if owner else "") + "; close it there first") from None
    os.ftruncate(fd, 0)
    os.write(fd, str(os.getpid()).encode())
    _held[path] = fd


def release(folder: SessionFolder) -> None:
    """Give the lock back (only if this Orwick holds it)."""
    path = folder.lock_file.resolve()
    fd = _held.pop(path, None)
    if fd is None:
        return
    path.unlink(missing_ok=True)
    fcntl.flock(fd, fcntl.LOCK_UN)
    os.close(fd)


def is_open_elsewhere(folder: Path) -> bool:
    """True if another running Orwick holds this session's lock."""
    path = (folder / "lock").resolve()
    if path in _held or not path.exists():
        return False
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return False
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return True
    else:
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    finally:
        os.close(fd)


def _read_owner(fd: int) -> str:
    try:
        return os.pread(fd, 32, 0).decode(errors="replace").strip()
    except OSError:
        return ""


# --- The conversation -----------------------------------------------------------------

def save(folder: SessionFolder, messages: list[dict[str, Any]], backend: str, model: str,
         workspace: Path, now: float | None = None,
         earlier: list[dict[str, Any]] | None = None, feature: str | None = None) -> None:
    """Save the conversation (without the system prompt) and the session's meta.

    ``earlier`` holds messages that compacting replaced with a summary: no longer
    sent to the AI, but kept so the whole conversation can be shown again.
    """
    now = time.time() if now is None else now
    history = [m for m in messages if m.get("role") in ROLES]
    earlier = [m for m in earlier or [] if m.get("role") in ROLES]
    write_json(folder.conversation_file, {"format": FORMAT, "messages": history, "earlier": earlier})
    previous = read_meta(folder.path) or {}
    everything = earlier + history
    changed = len(everything) != previous.get("history_length")
    write_json(folder.meta_file, {
        "format": FORMAT,
        "id": folder.id,
        "title": previous.get("title") or title_of(everything),
        "started_at": previous.get("started_at", now),
        "updated_at": now if changed else previous.get("updated_at", now),
        "messages": sum(1 for m in everything if m["role"] == "user" and not _is_summary(m)),
        "history_length": len(everything),
        "backend": backend,
        "model": model,
        "workspace": str(workspace),
        "feature": feature,
    })


def save_stats(folder: SessionFolder, data: dict[str, Any]) -> None:
    """The session's counts, token usage and tool calls (usage.SessionUsage)."""
    write_json(folder.stats_file, data)


def load_stats(folder: SessionFolder) -> dict[str, Any] | None:
    try:
        data = json.loads(folder.stats_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def load_conversation(folder: SessionFolder) -> list[dict[str, Any]]:
    """The saved conversation as sent to the AI, checked: user/assistant/tool messages."""
    return _load(folder)[0]


def load_earlier(folder: SessionFolder) -> list[dict[str, Any]]:
    """Messages that compacting summarized away (shown again, not sent to the AI)."""
    return _load(folder)[1]


def _load(folder: SessionFolder) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    try:
        data = json.loads(folder.conversation_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"can't read session {folder.id}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"session {folder.id} has a damaged conversation file")
    lists = data.get("messages"), data.get("earlier", [])
    for messages in lists:
        if not isinstance(messages, list) or not all(
                isinstance(m, dict) and m.get("role") in ROLES for m in messages):
            raise ConfigError(f"session {folder.id} has a damaged conversation file")
    return lists


def read_tool_overrides(folder: SessionFolder) -> tuple[dict[str, bool], str | None]:
    """This session's tool switches, and a problem with the file if there is one
    (a broken file is ignored, never fatal)."""
    try:
        data = json.loads(folder.tools_file.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}, None
    except (OSError, json.JSONDecodeError) as exc:
        return {}, f"{folder.tools_file}: can't read it ({exc}); ignoring it"
    if not isinstance(data, dict) or not all(
            isinstance(name, str) and TOOL_NAME.match(name) and isinstance(on, bool) for name, on in data.items()):
        return {}, f'{folder.tools_file}: must look like {{"run_command": false}}; ignoring it'
    return data, None


def write_tool_overrides(folder: SessionFolder, overrides: dict[str, bool]) -> None:
    """Save this session's tool switches (no file when there are none)."""
    if overrides:
        write_json(folder.tools_file, dict(sorted(overrides.items())))
    else:
        folder.tools_file.unlink(missing_ok=True)


def title_of(messages: list[dict[str, Any]]) -> str:
    """The first thing you wrote, on one line, shortened."""
    for message in messages:
        if message.get("role") == "user" and str(message.get("content") or "").strip() \
                and not _is_summary(message):
            line = " ".join(str(message["content"]).split())
            return line if len(line) <= TITLE_LENGTH else line[:TITLE_LENGTH - 1] + "…"
    return ""


def _is_summary(message: dict[str, Any]) -> bool:
    from agent_core.compaction import is_summary
    return is_summary(message)


def read_name(folder: Path) -> str:
    """The name you gave the session, or "" (no name: its title is shown instead).

    Kept in its own file, not in meta.json: Orwick rewrites meta.json after every
    message, and the app writes the name while Orwick runs."""
    try:
        return clean_name((folder / NAME_FILE).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        return ""


def clean_name(text: str) -> str:
    line = " ".join(str(text).split())
    return line[:NAME_LENGTH]


def write_name(folder: Path, name: str) -> str:
    """Set (or with "" remove) the session's name; returns the name as saved."""
    name = clean_name(name)
    target = folder / NAME_FILE
    if not name:
        target.unlink(missing_ok=True)
        return ""
    folder.mkdir(parents=True, exist_ok=True)
    target.write_text(name + "\n", encoding="utf-8")
    return name


def display_title(folder: Path, meta: dict[str, Any] | None) -> str:
    """What to call a session: its name, else its title (your first message)."""
    return read_name(folder) or str((meta or {}).get("title") or "")


def read_meta(folder: Path) -> dict[str, Any] | None:
    try:
        data = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def list_sessions(root: Path = SESSIONS_DIR) -> list[dict[str, Any]]:
    """Saved sessions with at least one message, most recently used first."""
    if not root.is_dir():
        return []
    found = []
    for folder in root.iterdir():
        if folder.is_dir() and not folder.is_symlink() and SESSION_ID.match(folder.name):
            meta = read_meta(folder)
            if meta and meta.get("messages"):
                found.append({**meta, "id": folder.name, "name": read_name(folder),
                              "open_elsewhere": is_open_elsewhere(folder)})
    return sorted(found, key=lambda m: m.get("updated_at") or 0, reverse=True)


def delete_session(session_id: str, root: Path = SESSIONS_DIR) -> dict[str, Any]:
    """Delete a saved session and everything in it (conversation, docs, logs).

    Refuses ids that aren't session ids, links, and a session open in a running
    Orwick (after waiting a moment for one that is just closing). Returns what was
    deleted (title, messages, docs) for the message.
    """
    if not SESSION_ID.match(session_id or ""):
        raise ConfigError(f"session id must look like 2026-11-12_114105_a3f9, got {session_id!r}")
    folder = SessionFolder(session_id, root / session_id)
    if folder.path.is_symlink() or not folder.path.is_dir():
        raise ConfigError(f"no session {session_id} in {root}")
    acquire(folder)                       # waits briefly; SessionBusy if another Orwick has it open
    meta = read_meta(folder.path) or {}
    title = display_title(folder.path, meta)
    docs = sorted(p.stem for p in (folder.path / "docs").glob("*.md")) if (folder.path / "docs").is_dir() else []
    release(folder)
    shutil.rmtree(folder.path)
    return {"id": session_id, "title": title, "messages": meta.get("messages", 0), "docs": docs}


def close(folder: SessionFolder) -> None:
    """When Orwick exits: give the lock back, and remove the session if nothing was said in it."""
    release(folder)
    meta = read_meta(folder.path)
    if not (meta and meta.get("messages")) and not folder.path.is_symlink():
        shutil.rmtree(folder.path, ignore_errors=True)


def _age_key(folder: Path) -> tuple[float, str, float]:
    """Least recently used first: when it was last used (saved), then the time in its
    name, then (same second) when the folder was made."""
    meta = read_meta(folder) or {}
    used = meta.get("updated_at") if isinstance(meta.get("updated_at"), (int, float)) else 0.0
    try:
        info = folder.stat()
        made = getattr(info, "st_birthtime", info.st_ctime)  # birth time on macOS
    except OSError:
        made = 0.0
    return used, folder.name[:17], made


def remove_old_sessions(root: Path, keep: int, current: str) -> list[str]:
    """Delete sessions in which nothing was said, and all but the ``keep`` most recently
    used ones; never the current one, and never one that another running Orwick has open.

    Only real folders named like a session id are touched (no symlinks, nothing else).
    """
    folders = sorted((p for p in root.iterdir()
                      if p.is_dir() and not p.is_symlink() and SESSION_ID.match(p.name)),
                     key=_age_key)
    removed = []
    # Empty sessions left behind by an Orwick that was killed before it could tidy up.
    for folder in list(folders):
        if (folder.name != current and not (read_meta(folder) or {}).get("messages")
                and SessionFolder(folder.name, folder).lock_file.resolve() not in _held
                and not is_open_elsewhere(folder)):
            shutil.rmtree(folder, ignore_errors=True)
            removed.append(folder.name)
            folders.remove(folder)
    for folder in folders[:max(0, len(folders) - keep)]:
        if folder.name != current and not is_open_elsewhere(folder):
            shutil.rmtree(folder, ignore_errors=True)
            removed.append(folder.name)
    return removed


def read_skill_overrides(folder: SessionFolder) -> dict[str, bool]:
    """This session's own skill switches (skills.json, written by the app or /skills on|off);
    a broken file counts as none."""
    try:
        data = json.loads(folder.skills_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {name: on for name, on in data.items() if isinstance(name, str) and isinstance(on, bool)}


def read_related(folder: SessionFolder) -> list[str]:
    """This session's related folders (related.json: other projects it may read, never
    write), as absolute paths; a broken file counts as none."""
    try:
        data = json.loads(folder.related_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return [p for p in data if isinstance(p, str)] if isinstance(data, list) else []


def write_related(folder: SessionFolder, paths: list[str]) -> None:
    """Save this session's related folders (no file when there are none)."""
    if paths:
        write_json(folder.related_file, paths)
    else:
        folder.related_file.unlink(missing_ok=True)


def write_skill_overrides(folder: SessionFolder, overrides: dict[str, bool]) -> None:
    """Save this session's skill switches (no file when there are none)."""
    if overrides:
        write_json(folder.skills_file, dict(sorted(overrides.items())))
    else:
        folder.skills_file.unlink(missing_ok=True)

