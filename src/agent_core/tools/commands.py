"""The run_command tool: run a shell command inside the OS sandbox.

Layers, in order:

1. **Approval** (gateway): you see the full command, the folder and the
   sandbox rules, and must answer y. Every command, every time.
2. **Sandbox** (``agent_core.sandbox``): the OS limits what the command can
   read, write and reach, whatever code it ends up running.
3. **Clean environment:** the command starts with an empty environment plus
   a few harmless variables, so API keys and tokens in Orwick's own
   environment can't be printed. ``HOME`` and ``TMPDIR`` point to a fresh temp
   folder that is deleted afterwards.
4. **Limits:** no input (stdin is empty), a timeout that kills the command and
   everything it started, and only the start and end of long output are kept.

If no sandbox can be applied, the tool refuses to run anything.
"""

import functools
import json
import os
import re
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any

from agent_core import sandbox
from agent_core.approvals import visible
from agent_core.errors import PermanentToolError
from agent_core.tools import files

DEFAULT_TIMEOUT_SECONDS = 60
MAX_TIMEOUT_SECONDS = 300
MAX_COMMAND_CHARS = 4000
# Output kept for the AI: the start (what ran) and, mostly, the end (where
# errors and test summaries are). The middle of long output is dropped.
OUTPUT_HEAD_CHARS = 1500
OUTPUT_TAIL_CHARS = 5000
KILL_GRACE_SECONDS = 1.0

# Variables copied from Orwick's environment. Everything else is left out.
PASSED_ENV = ("PATH", "LANG", "LC_ALL", "LC_CTYPE", "USER", "LOGNAME", "SHELL")


def run_command(command: str, cwd: str = ".", timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS) -> dict[str, Any]:
    """Run command with /bin/sh in the sandbox and return its exit code and output."""
    folder, timeout = _check(command, cwd, timeout_seconds)
    workspace = files.workspace_root()
    temp_dir = Path(tempfile.mkdtemp(prefix="orwick-cmd-")).resolve()
    try:
        profile = sandbox.build_profile(
            workspace, temp_dir, files.protected_paths(),
            readable_exceptions=_clean_git_configs(workspace, folder),
            readable_dirs=[*_skill_roots(), *files.related_roots()],   # related projects: read only
        )
        return _execute(command, folder, timeout, temp_dir, profile)
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def _skill_roots() -> list[Path]:
    """The folders that hold the switched-on skills (e.g. ~/.claude/skills), so a skill's
    scripts and the shared files beside it (../golang-shared) can be read and run."""
    from agent_core.tools.knowledge import SKILLS_ENV
    try:
        folders = json.loads(os.environ.get(SKILLS_ENV) or "{}").values()
    except ValueError:
        return []
    return sorted({Path(folder).parent for folder in folders}, key=str)


def preview_command(command: str, cwd: str = ".", timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS) -> str:
    """What will run, shown in the approval prompt. Nothing is executed here."""
    folder, timeout = _check(command, cwd, timeout_seconds)
    shown = [visible(line) for line in command.splitlines()] or [""]
    lines = ["Command:", *("  $ " + line if i == 0 else "    " + line for i, line in enumerate(shown))]
    lines += [
        f"Folder:   {files._relative(folder)}",
        f"Timeout:  {timeout}s",
        "Sandbox:  writes only inside the workspace · no network",
        "          can't read your home folder, secret files or .orwick",
        "          can't change .git/hooks, .vscode, .github/workflows, .envrc",
    ]
    return "\n".join(lines)



def _check(command: str, cwd: str, timeout_seconds: Any) -> tuple[Path, int]:
    """Validate the arguments; return the folder to run in and the timeout."""
    sandbox_error = _sandbox_error()
    if sandbox_error:
        raise PermanentToolError(sandbox_error)
    if not isinstance(command, str) or not command.strip():
        raise PermanentToolError("command is empty")
    if len(command) > MAX_COMMAND_CHARS:
        raise PermanentToolError(f"command is longer than {MAX_COMMAND_CHARS} characters")
    if "\x00" in command:
        raise PermanentToolError("command contains a NUL character")
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, int):
        raise PermanentToolError("timeout_seconds must be a whole number")
    if not 1 <= timeout_seconds <= MAX_TIMEOUT_SECONDS:
        raise PermanentToolError(f"timeout_seconds must be between 1 and {MAX_TIMEOUT_SECONDS}")
    folder = files.resolve_path(cwd)
    if not folder.is_dir():
        raise PermanentToolError(f"{cwd} is not a folder")
    return folder, timeout_seconds


# Signs that a git config holds a credential (token in a URL, auth header, known token formats).
GIT_CREDENTIAL_PATTERNS = (
    re.compile(r"://[^/\s@]+@"),                     # https://user:token@host or https://token@host
    re.compile(r"extraheader", re.IGNORECASE),        # http.extraHeader = Authorization: ...
    re.compile(r"(ghp_|gho_|github_pat_|glpat-|xox[abp]-|ATBB)[A-Za-z0-9_\-]{8,}"),
)


def _clean_git_configs(workspace: Path, folder: Path | None = None) -> list[Path]:
    """The .git/config of the workspace and of the repo the command runs in (a
    workspace often holds several repos), each only if it holds no credentials,
    so git can work.

    git can't run at all without reading it. One with a token in it stays
    blocked: then git doesn't work through Orwick until the token is removed.
    """
    candidates = [workspace / ".git" / "config"]
    if folder is not None:
        here = folder.resolve()
        root = workspace.resolve()
        while here != root and root in here.parents:   # the nearest repo above the command's folder
            if (here / ".git").is_dir():
                candidates.append(here / ".git" / "config")
                break
            here = here.parent
    clean = []
    for config in candidates:
        try:
            text = config.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if not any(p.search(text) for p in GIT_CREDENTIAL_PATTERNS):
            clean.append(config)
    return clean


def _sandbox_error() -> str | None:
    try:
        sandbox.check_available()
    except sandbox.SandboxUnavailable as exc:
        return str(exc)
    return None


def _environment(temp_dir: Path) -> dict[str, str]:
    """A minimal environment: no keys or tokens from Orwick's own environment."""
    env = {name: os.environ[name] for name in PASSED_ENV if name in os.environ}
    env.setdefault("PATH", "/usr/bin:/bin:/usr/sbin:/sbin")
    developer_bin = _developer_bin()
    if developer_bin:
        env["PATH"] = developer_bin + os.pathsep + env["PATH"]
    home = temp_dir / "home"
    home.mkdir()
    env.update({
        "HOME": str(home),
        "TMPDIR": str(temp_dir) + "/",
        "TERM": "dumb",
        "NO_COLOR": "1",
        "GIT_TERMINAL_PROMPT": "0",
        "GOCACHE": str(temp_dir / "go-cache"),
        "PIP_NO_INPUT": "1",
    })
    return env


@functools.lru_cache(maxsize=1)
def _developer_bin() -> str | None:
    """The real folder of git, clang... on macOS.

    /usr/bin/git is a small wrapper that asks Xcode where git is, writing a
    cache outside the workspace; in the sandbox that fails slowly and prints
    noise. Putting the real folder first on PATH skips the wrapper.
    """
    try:
        found = subprocess.run(["/usr/bin/xcode-select", "-p"], capture_output=True,
                               text=True, timeout=5, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        return None
    folder = Path(found.stdout.strip()) / "usr" / "bin"
    return str(folder) if found.returncode == 0 and (folder / "git").is_file() else None


# Commands running now, so Ctrl+C can stop them (a tool thread can't be stopped; its
# command can).
_RUNNING: set[subprocess.Popen[bytes]] = set()
_STOPPED: set[int] = set()
_RUNNING_LOCK = threading.Lock()


def stop_all() -> int:
    """Kill every running command and what it started; returns how many were running."""
    with _RUNNING_LOCK:
        running = list(_RUNNING)
        _STOPPED.update(p.pid for p in running)
    for process in running:
        _kill_group(process)
    return len(running)


def _execute(command: str, folder: Path, timeout: int, temp_dir: Path, profile: str) -> dict[str, Any]:
    started = time.monotonic()
    try:
        process = subprocess.Popen(
            sandbox.command_line(profile, command),
            cwd=folder,
            env=_environment(temp_dir),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=True,  # its own process group, so a timeout kills everything
        )
    except OSError as exc:
        raise PermanentToolError(f"could not start the command: {exc}") from exc

    with _RUNNING_LOCK:
        _RUNNING.add(process)
    output = _OutputCollector(process.stdout)
    timed_out = False
    try:
        exit_code = process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        _kill_group(process)
        exit_code = process.returncode
    except BaseException:
        _kill_group(process)
        raise
    finally:
        with _RUNNING_LOCK:
            _RUNNING.discard(process)
            stopped = process.pid in _STOPPED
            _STOPPED.discard(process.pid)
    output.finish()
    if stopped:
        raise PermanentToolError("The user stopped this command (Ctrl+C).")

    text, truncated = output.text()
    return {
        "command": command,
        "cwd": files._relative(folder),
        "exit_code": exit_code,
        "timed_out": timed_out,
        "duration_seconds": round(time.monotonic() - started, 2),
        "output": text,
        "output_truncated": truncated,
        "note": _note(exit_code, timed_out, timeout),
    }


def _note(exit_code: int | None, timed_out: bool, timeout: int) -> str:
    if timed_out:
        return f"Stopped after {timeout}s. Output so far is shown."
    if exit_code == 0:
        return "Finished successfully."
    return ("Finished with an error. 'Operation not permitted' means the sandbox "
            "blocked it (no network, writes only in the workspace, no secret files).")


def _kill_group(process: subprocess.Popen[bytes]) -> None:
    """SIGTERM, then SIGKILL, the command and everything it started."""
    # SIGKILL is always sent too: children may outlive the shell after SIGTERM.
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(process.pid, sig)
        except (ProcessLookupError, PermissionError):
            pass  # already gone (macOS says PermissionError when only zombies remain)
        try:
            process.wait(timeout=KILL_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            pass


class _OutputCollector:
    """Read output in the background, keeping only its start and end in memory."""

    def __init__(self, stream: Any) -> None:
        self._stream = stream
        self._head = bytearray()
        self._tail: deque[bytes] = deque()
        self._tail_size = 0
        self._dropped = 0
        self._thread = threading.Thread(target=self._read, daemon=True)
        self._thread.start()

    def _read(self) -> None:
        try:
            while chunk := self._stream.read1(65536):
                room = OUTPUT_HEAD_CHARS - len(self._head)
                if room > 0:
                    self._head += chunk[:room]
                    chunk = chunk[room:]
                if chunk:
                    self._tail.append(chunk)
                    self._tail_size += len(chunk)
                    while self._tail_size - len(self._tail[0]) >= OUTPUT_TAIL_CHARS:
                        self._dropped += len(self._tail[0])
                        self._tail_size -= len(self._tail.popleft())
        except (OSError, ValueError):
            pass

    def finish(self) -> None:
        # A process that escaped the group may keep the pipe open; don't wait forever.
        self._thread.join(timeout=2)
        try:
            self._stream.close()
        except OSError:
            pass

    def text(self) -> tuple[str, bool]:
        tail = b"".join(self._tail)
        if len(tail) > OUTPUT_TAIL_CHARS:
            self._dropped += len(tail) - OUTPUT_TAIL_CHARS
            tail = tail[-OUTPUT_TAIL_CHARS:]
        head = self._head.decode("utf-8", errors="replace")
        tail_text = tail.decode("utf-8", errors="replace")
        if self._dropped:
            return f"{head}\n... [{self._dropped:,} bytes of output not shown] ...\n{tail_text}", True
        return head + tail_text, False
