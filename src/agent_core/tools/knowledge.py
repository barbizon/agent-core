"""Skills and session docs, as tools.

* ``use_skill`` (read): a switched-on skill's instructions (see skills.py).
* ``read_session_doc`` (read) and ``write_session_doc`` (write, with a preview
  you approve): Markdown notes kept with the session, in
  ``.orwick/sessions/<id>/docs/``. They resume with the session and are deleted
  with it. These two may only touch that one folder, by a plain file name.
"""

import difflib
import json
import os
import re
from pathlib import Path
from typing import Any

from agent_core.errors import PermanentToolError

SKILLS_ENV = "ORWICK_SKILLS"            # set by the CLI (skills.export)
SESSION_DOCS_ENV = "ORWICK_SESSION_DOCS"  # set by the CLI: this session's docs folder
INSTRUCTIONS_LIMIT = 12_000
DOC_LIMIT = 100_000
DOC_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,60}$")
FILES_LISTED = 50


SKILL_FILE_LIMIT = 40_000   # characters of one skill file returned at once


def use_skill(name: str, file: str | None = None) -> dict[str, Any]:
    """Load a skill's instructions (everything after its header) and list its other files;
    with ``file``, read one of those files instead (``references/x.md``, or a shared
    folder beside it such as ``../golang-shared/rules.md``)."""
    skills = json.loads(os.environ.get(SKILLS_ENV) or "{}")
    if name not in skills:
        raise PermanentToolError(f"No skill named {name!r}. Skills that are on: {sorted(skills) or 'none'}")
    folder = Path(skills[name])
    if file:
        return _skill_file(name, folder, file)
    body = _body(folder)
    cut = len(body) > INSTRUCTIONS_LIMIT
    files = sorted(str(p.relative_to(folder)) for p in folder.rglob("*")
                   if p.is_file() and p.name != "SKILL.md" and not p.is_symlink())
    result = {"name": name, "instructions": body[:INSTRUCTIONS_LIMIT].strip(), "cut_short": cut,
              "other_files": files[:FILES_LISTED]}
    # Skills that extend this one (their header says so) load with it.
    from agent_core.skills import header_values
    additions = [{"name": other, "instructions": _body(Path(path))[:INSTRUCTIONS_LIMIT].strip()}
                 for other, path in sorted(skills.items())
                 if other != name and header_values(Path(path) / "SKILL.md").get("extends") == name]
    if additions:
        result["additions"] = additions
        result["additions_note"] = "Follow these too. Where one differs from the instructions above, it wins."
    if _is_claude_skill(folder):
        result["note"] = CLAUDE_SKILL_NOTE
    if _reads_code_root(folder):
        from agent_core.tools.files import open_code_root, workspace_root
        code = open_code_root()
        result["code_root"] = (
            f"../ ({code}): every repo beside this one, readable (never changed) — "
            "reach them as ../<repo>/…" if code else
            "none found: no folder of repos around this workspace. Other repos can be read "
            "only when the user adds them (/related add <folder>).")
        if code:
            result["code_root_repos"] = sorted(p.name for p in code.iterdir()
                                               if p.is_dir() and not p.name.startswith(".")
                                               and p != workspace_root())[:200]
    return result


def _body(folder: Path) -> str:
    """A skill's instructions: its SKILL.md after the header."""
    text = (folder / "SKILL.md").read_text(encoding="utf-8", errors="replace")
    return text.split("\n---", 1)[1].split("\n", 1)[1] if text.startswith("---") and "\n---" in text else text


def _reads_code_root(folder: Path) -> bool:
    """The skill's contract asks to read the repos around the workspace."""
    from agent_core import contracts
    try:
        rules = contracts.load(folder)
    except (OSError, ValueError):
        return False
    return bool(rules and rules.get("reads_code_root"))


# Claude Code's skills name Claude's tools; here the AI has Orwick's.
CLAUDE_SKILL_NOTE = (
    "This skill was written for Claude Code. Its tools map to yours: Read → read_file, "
    "Grep → grep, Glob → grep with glob or list_folder, Edit/MultiEdit → edit_file, "
    "Write → write_file, Bash → run_command (sandboxed: no network; the skill's own "
    "scripts can run), Agent/Task (subagents) → run_subagent, TodoWrite → keep the list "
    "in your reply. Its linked files (references/…, ../shared/…) → use_skill with file. "
    "Skip steps that need something you don't have (another MCP server, the network, "
    "Claude-only commands) and say so.")


def _is_claude_skill(folder: Path) -> bool:
    from agent_core.skills import CLAUDE_SKILLS_DIR
    try:
        return folder.resolve().is_relative_to(Path(CLAUDE_SKILLS_DIR).resolve())
    except OSError:
        return False


def _skill_file(name: str, folder: Path, file: str) -> dict[str, Any]:
    """A text file of a switched-on skill. It must stay inside the skills folder that
    holds the skill (no way out through ``..`` or links), and is never a secret file."""
    from agent_core.tools.files import SECRET_NAMES, NOT_SECRET_NAMES, _matches_any
    root = folder.parent.resolve()
    target = (folder / file).resolve()
    if not target.is_relative_to(root):
        raise PermanentToolError(f"{file} is outside the skills folder")
    if _matches_any(target.name.casefold(), SECRET_NAMES) and not _matches_any(target.name.casefold(), NOT_SECRET_NAMES):
        raise PermanentToolError(f"{file} looks like a key, password or token file")
    if not target.is_file():
        raise PermanentToolError(f"No file {file} in skill {name}")
    try:
        text = target.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raise PermanentToolError(f"{file} isn't a text file") from None
    result: dict[str, Any] = {"name": name, "file": file, "content": text[:SKILL_FILE_LIMIT]}
    if len(text) > SKILL_FILE_LIMIT:
        result["note"] = f"Only the first {SKILL_FILE_LIMIT:,} of {len(text):,} characters."
    return result


def _docs_folder() -> Path:
    folder = os.environ.get(SESSION_DOCS_ENV)
    if not folder:
        raise PermanentToolError("This session has no docs folder")
    return Path(folder)


def _doc_path(name: str) -> Path:
    stem = name.removesuffix(".md")
    if not DOC_NAME.match(stem):
        raise PermanentToolError("A doc name is a plain lowercase name like product-requirement "
                                 "(letters, digits, - and _)")
    path = _docs_folder() / f"{stem}.md"
    if path.is_symlink():
        raise PermanentToolError("That doc is a link; it can't be used")
    return path


def read_session_doc(name: str = "") -> dict[str, Any]:
    """One of this session's docs, or (no name) the list of them."""
    folder = _docs_folder()
    if not name:
        docs = sorted(p.stem for p in folder.glob("*.md") if p.is_file() and not p.is_symlink()) \
            if folder.is_dir() else []
        return {"docs": docs}
    path = _doc_path(name)
    if not path.is_file():
        raise PermanentToolError(f"No doc {path.stem!r} in this session yet")
    text = path.read_text(encoding="utf-8", errors="replace")
    return {"name": path.stem, "content": text[:DOC_LIMIT], "cut_short": len(text) > DOC_LIMIT}


def _planned(name: str, content: str) -> tuple[Path, str]:
    if len(content) > DOC_LIMIT:
        raise PermanentToolError(f"A doc can be at most {DOC_LIMIT:,} characters")
    path = _doc_path(name)
    before = path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""
    return path, before


def preview_session_doc(name: str, content: str) -> str:
    """What write_session_doc would change, as a diff, for your approval."""
    path, before = _planned(name, content)
    lines = list(difflib.unified_diff(before.splitlines(), content.splitlines(),
                                      fromfile=f"docs/{path.name} (before)" if before else "(new doc)",
                                      tofile=f"docs/{path.name} (after)", lineterm="", n=2))
    if not lines:
        return "(no change)"
    return "\n".join(lines[:200]) + ("\n… (diff cut)" if len(lines) > 200 else "")


def _many(docs: Any) -> list[tuple[Path, str, str]]:
    if not isinstance(docs, dict) or not docs or not all(isinstance(v, str) for v in docs.values()):
        raise PermanentToolError('docs must be an object {"doc-name": "markdown", ...}')
    if len(docs) > 10:
        raise PermanentToolError("At most 10 docs at once")
    return [(*_planned(name, content), content) for name, content in docs.items()]


def preview_session_docs(docs: dict[str, str]) -> str:
    """What write_session_docs would change, every doc's diff, for one approval."""
    parts = []
    for path, before, content in _many(docs):
        parts.append(preview_session_doc(path.stem, content) if before != content else f"docs/{path.name}: (no change)")
    return "\n\n".join(parts)


def write_session_docs(docs: dict[str, str]) -> dict[str, Any]:
    """Create or replace several of this session's docs at once (one approval)."""
    planned = _many(docs)
    saved = [write_session_doc(path.stem, content)["name"] for path, before, content in planned if before != content]
    return {"saved": saved, "unchanged": [p.stem for p, before, content in planned if before == content]}


def write_session_doc(name: str, content: str) -> dict[str, Any]:
    """Create or replace one of this session's docs (Markdown)."""
    path, before = _planned(name, content)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".md.tmp")
    temp.write_text(content, encoding="utf-8")
    os.replace(temp, path)
    return {"name": path.stem, "saved": True, "created": not before, "characters": len(content)}
