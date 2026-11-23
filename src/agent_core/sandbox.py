"""An operating-system sandbox for commands the agent runs (macOS Seatbelt).

Approval alone can't make a command safe: ``pytest`` runs whatever code the
project contains, and nobody can see that code in the approval prompt. So every
command also runs inside a sandbox enforced by the operating system, which
holds even if the command (or the code it runs) misbehaves.

The profile starts from **deny everything** and allows only what a build or
test command needs:

* **Read:** the system (``/usr``, ``/System``, ``/opt/homebrew``...), the
  workspace, the command's own temp folder, a few read-only toolchain
  folders in your home (Python/Go/Rust/Node installs), and the folders of the
  skills that are on (so a skill's scripts run; never writable). The rest of your home
  folder, other users, external disks and other temp folders are unreadable.
* **Write:** only the workspace and the command's own temp folder.
* **Never, even inside the workspace:** read secret files (``.env``, keys,
  ``.ssh/``, ``.git/config``...) or the agent's ``.orwick`` folder; write
  files that run code on their own (``.git/hooks/``, ``.vscode/``,
  ``.github/workflows/``, ``.envrc``, shell startup files).
* **No network** at all, and no talking to other apps (it can't ask macOS to
  open an app or a URL outside the sandbox).

Children inherit the sandbox, so nothing a command starts can leave it.
Rules use the same lists as the file tools (``agent_core.tools.files``), so
the two walls agree on what is secret.

Later rules win in a Seatbelt profile, which is how the exceptions
("allow the workspace", then "but not .env inside it") are layered.
"""

import functools
import os
import subprocess
import sys
from pathlib import Path

SANDBOX_EXEC = "/usr/bin/sandbox-exec"
# What "can't touch a secret" means: no reading its content, no writing.
# Metadata (exists, size) stays allowed: programs like pytest stat every entry
# of a folder and fail outright if even that is refused.
CONTENT = "file-read-data file-read-xattr file-write*"

# Toolchains installed in the home folder that commands may *read* (never write).
# Only folders that hold programs and libraries, never credentials
# (e.g. ~/.cargo/bin, not ~/.cargo, which can hold a registry token).
HOME_TOOLCHAIN_DIRS = (
    ".local/share/uv",      # Python installed by uv
    ".pyenv/versions",      # Python installed by pyenv
    ".rustup/toolchains",   # Rust
    ".cargo/bin",
    ".nvm/versions",        # Node
    "go/pkg/mod",           # Go module cache
    "sdk",                  # Go versions installed by `go install golang.org/dl/...`
)

# Top-level folders that hold personal data; unreadable except where re-allowed.
PRIVATE_ROOTS = ("/Users", "/Volumes", "/private/tmp", "/private/var/folders")


class SandboxUnavailable(Exception):
    """Commands can't run safely here, so they don't run at all."""


@functools.lru_cache(maxsize=1)
def check_available() -> None:
    """Raise SandboxUnavailable unless a sandbox can really be applied here.

    A real test run, not just "the file exists": inside another sandbox (e.g.
    when Orwick itself is started by a sandboxed tool) applying a new one fails.
    """
    if sys.platform != "darwin":
        raise SandboxUnavailable(
            "Commands are disabled: the command sandbox is only built for macOS so far."
        )
    if not os.access(SANDBOX_EXEC, os.X_OK):
        raise SandboxUnavailable(f"Commands are disabled: {SANDBOX_EXEC} was not found.")
    try:
        probe = subprocess.run(
            [SANDBOX_EXEC, "-p", "(version 1)(allow default)", "/usr/bin/true"],
            stdin=subprocess.DEVNULL, capture_output=True, timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SandboxUnavailable(f"Commands are disabled: the sandbox failed to start ({exc}).")
    if probe.returncode != 0:
        detail = probe.stderr.decode(errors="replace").strip() or f"exit code {probe.returncode}"
        raise SandboxUnavailable(f"Commands are disabled: the sandbox can't be applied here ({detail}).")


def build_profile(
    workspace: Path,
    temp_dir: Path,
    protected: list[Path],
    home: Path | None = None,
    readable_exceptions: list[Path] = (),
    readable_dirs: list[Path] = (),
) -> str:
    """The Seatbelt profile (SBPL text) for one command.

    readable_exceptions: secret-listed files that may be read (not written)
    anyway, e.g. a .git/config checked to hold no credentials, so git works.
    readable_dirs: folders outside the workspace that may be read and run from,
    never written (the folders of the skills that are on, so their scripts run).
    Secret files inside them stay unreadable.
    """
    # Imported here: files.py is a tool module; keep this module importable on its own.
    from agent_core.tools import files

    home = (home or Path.home()).resolve()
    ws = workspace.resolve()
    tmp = temp_dir.resolve()
    lines = [
        "(version 1)",
        "; Start closed: anything not allowed below is refused.",
        "(deny default)",
        "",
        "; Run programs. Children inherit this same sandbox.",
        "(allow process-exec process-fork)",
        "(allow signal (target same-sandbox))",
        "(allow process-info* (target same-sandbox))",
        "(allow sysctl-read)",
        "(allow ipc-posix-sem)",
        "; Look up user names (needed by many programs). No other system services:",
        "; that blocks opening apps or URLs outside the sandbox.",
        '(allow mach-lookup (global-name "com.apple.system.opendirectoryd.libinfo"))',
        "",
        "; READ: the system, but not personal folders.",
        "(allow file-read*)",
        "(deny file-read* " + " ".join(_subpath(Path(p)) for p in (*PRIVATE_ROOTS, home)) + ")",
        "; Folders above the workspace: only their names, not their content.",
        "(allow file-read-metadata " + " ".join(
            _literal(p) for p in _ancestors(ws, tmp, *(d.resolve() for d in readable_dirs))) + ")",
        "; The workspace, the command's own temp folder, and toolchains.",
        f"(allow file-read* {_subpath(ws)} {_subpath(tmp)})",
    ]
    toolchains = [home / d for d in HOME_TOOLCHAIN_DIRS if (home / d).is_dir()]
    if toolchains:
        lines.append("(allow file-read* " + " ".join(_subpath(p.resolve()) for p in toolchains) + ")")
    extra = [d.resolve() for d in readable_dirs if d.is_dir()]
    if extra:
        lines.append("; Skill folders: their scripts and files may be read and run, never written.")
        lines.append("(allow file-read* " + " ".join(_subpath(d) for d in extra) + ")")
    lines += [
        "",
        "; WRITE: only the workspace and the temp folder.",
        f"(allow file-write* {_subpath(ws)} {_subpath(tmp)})",
        '(allow file-write-data (literal "/dev/null") (literal "/dev/stdout") (literal "/dev/stderr")',
        '  (regex #"^/dev/fd/[0-9]+$"))',
        "",
        "; NEVER, even inside the workspace: the agent's own folder and secrets.",
        "; (Their names and sizes stay visible, so tools can skip them; content can't be read.)",
    ]
    secret_filters = [_subpath(p.resolve()) for p in protected]
    secret_filters += [_folder_regex(f) for f in files.SECRET_FOLDERS]
    secret_filters += [_name_regex(n) for n in files.SECRET_NAMES]
    secret_filters += [_path_regex(p) for p in files.SECRET_PATHS]
    lines.append(f"(deny {CONTENT} " + " ".join(secret_filters) + ")")
    lines.append("; ...except templates and public keys inside the workspace.")
    for name in files.NOT_SECRET_NAMES:
        lines.append(
            f"(allow {CONTENT} (require-all {_subpath(ws)} {_name_regex(name)}))"
        )
    if readable_exceptions:
        lines.append("; Checked to hold no credentials: readable, still not writable.")
        lines.append("(allow file-read-data "
                     + " ".join(_literal(p.resolve()) for p in readable_exceptions) + ")")
    lines.append("; Protected paths stay protected even if a name above matches.")
    if protected:
        lines.append(f"(deny {CONTENT} "
                     + " ".join(_subpath(p.resolve()) for p in protected) + ")")
    lines += [
        "; Files that run code on their own: readable, never writable.",
        "(deny file-write* "
        + " ".join([_folder_regex(f) for f in files.NO_WRITE_FOLDERS]
                   + [_name_regex(n) for n in files.NO_WRITE_NAMES])
        + ")",
        "; The command's own temp folder holds only what it created there and is",
        "; deleted afterwards, so name rules don't apply in it. (A symlink to a real",
        "; secret is still refused: the sandbox checks the file it points to.)",
        f"(allow {CONTENT} {_subpath(tmp)})",
        "",
        "; No network: nothing above allows it, so it stays denied.",
    ]
    return "\n".join(lines) + "\n"


def command_line(profile: str, shell_command: str) -> list[str]:
    """The argv that runs shell_command under the profile."""
    return [SANDBOX_EXEC, "-p", profile, "/bin/sh", "-c", shell_command]


# --- SBPL helpers -------------------------------------------------------------

def _quote(text: str) -> str:
    """A string literal for SBPL."""
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _subpath(path: Path) -> str:
    return f"(subpath {_quote(str(path))})"


def _literal(path: Path) -> str:
    return f"(literal {_quote(str(path))})"


def _ancestors(*paths: Path) -> list[Path]:
    """Every parent folder of the given paths (so programs can walk down to them)."""
    seen: dict[str, Path] = {}
    for path in paths:
        for parent in (path, *path.parents):
            seen[str(parent)] = parent
    return list(seen.values())


def _ci(text: str) -> str:
    """A regex for text, ignoring letter case (macOS file names ignore case)."""
    out = []
    for ch in text:
        if ch == "*":
            out.append("[^/]*")
        elif ch.isalpha():
            out.append(f"[{ch.lower()}{ch.upper()}]")
        elif ch.isalnum() or ch in "_/":
            out.append(ch)
        else:
            out.append("\\" + ch)
    return "".join(out)


def _sbpl_regex(pattern: str) -> str:
    return f'(regex #"{pattern}")'


def _name_regex(name: str) -> str:
    """Any file or folder with this name (wildcards allowed), anywhere."""
    return _sbpl_regex(f"/{_ci(name)}$")


def _folder_regex(folder: str) -> str:
    """This folder (e.g. ".config/gh") and everything inside it, anywhere."""
    return _sbpl_regex(f"/{_ci(folder)}(/|$)")


def _path_regex(path: str) -> str:
    """A path ending (e.g. ".git/config"), anywhere."""
    return _sbpl_regex(f"/{_ci(path)}$")
