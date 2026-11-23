"""The service map library: which messages and calls connect a service with the others,
kept outside any session.

``~/.orwick/maps/<service>/`` holds one Markdown file per doc (map, messages, ...)
and ``meta.json``: the service's folder, the related folders scanned with it, the
question asked, when it changed and the docs in reading order. Any session can read
and update a map, and the app lists them all (Service maps in the sidebar).

* ``read_service_map`` (read): the list of maps, one map's docs, or one doc.
* ``write_service_map`` (write, with a diff you approve): create or update one map.

Like the session docs, these tools only touch that one folder, by plain names.
"""

import difflib
import json
import os
import re
import time
from pathlib import Path
from typing import Any

from agent_core import contracts
from agent_core.errors import PermanentToolError
from agent_core.tools.files import related_roots, workspace_root
from agent_core.tools.knowledge import DOC_LIMIT, DOC_NAME

MAPS_DIR_ENV = "ORWICK_MAPS_DIR"   # tests point it elsewhere; default ~/.orwick/maps
META = "meta.json"
FORMAT = 1
MAX_DOCS = 10


SKILL = "service-map"
BUILTIN_SKILL_DIR = Path(__file__).resolve().parent.parent / "skills_builtin" / SKILL


def contract() -> dict[str, Any] | None:
    """The service-map skill's contract: the switched-on skill's own (yours may replace
    the built-in one), else the built-in one."""
    from agent_core.tools.knowledge import SKILLS_ENV
    try:
        folder = Path(json.loads(os.environ.get(SKILLS_ENV) or "{}").get(SKILL) or BUILTIN_SKILL_DIR)
    except ValueError:
        folder = BUILTIN_SKILL_DIR
    try:
        return contracts.load(folder)
    except (OSError, ValueError) as exc:
        raise PermanentToolError(f"The {SKILL} skill's contract can't be read: {exc}") from None


def maps_dir() -> Path:
    return Path(os.environ.get(MAPS_DIR_ENV) or Path.home() / ".orwick" / "maps")


def auto_name(session_id: str) -> str:
    """The next free name for a map made in this session: <session_id>_map1, _map2, …"""
    base = re.sub(r"[^a-z0-9_-]+", "-", session_id.lower()).strip("-_") or "session"
    number = 1
    while (maps_dir() / f"{base}_map{number}").exists():
        number += 1
    return f"{base}_map{number}"


def _name(value: str, what: str) -> str:
    stem = (value or "").strip().removesuffix(".md")
    if not DOC_NAME.match(stem):
        raise PermanentToolError(f"{what} must be a plain lowercase name like orders or "
                                 "messages (letters, digits, - and _)")
    return stem


def _folder(service: str) -> Path:
    if not (service or "").strip():   # no name given: the next free <session_id>_mapN
        from agent_core.tools.knowledge import SESSION_DOCS_ENV
        docs = os.environ.get(SESSION_DOCS_ENV)
        service = auto_name(Path(docs).parent.name if docs else "session")
    folder = maps_dir() / _name(service, "The service name")
    if folder.is_symlink():
        raise PermanentToolError("That map folder is a link; it can't be used")
    return folder


def read_meta(folder: Path) -> dict[str, Any]:
    try:
        meta = json.loads((folder / META).read_text(encoding="utf-8"))
        return meta if isinstance(meta, dict) else {}
    except (OSError, ValueError):
        return {}


def _docs_in(folder: Path) -> list[str]:
    found = {p.stem for p in folder.glob("*.md") if p.is_file() and not p.is_symlink()}
    ordered = [d for d in read_meta(folder).get("docs", []) if d in found]
    return ordered + sorted(found - set(ordered))


def read_service_map(service: str = "", name: str = "") -> dict[str, Any]:
    """No service: every map in the library. A service: its docs and what was scanned.
    A service and a name: that doc's Markdown."""
    if not service:
        root = maps_dir()
        items = []
        for folder in sorted(root.iterdir() if root.is_dir() else []):
            if folder.is_dir() and not folder.is_symlink() and DOC_NAME.match(folder.name):
                meta = read_meta(folder)
                items.append({"service": folder.name, "folder": meta.get("folder"),
                              "question": meta.get("question"), "docs": _docs_in(folder)})
        return {"maps": items, "library": str(root)}
    folder = _folder(service)
    if not name:
        if not folder.is_dir():
            return {"service": folder.name, "docs": [], "note": "No map for this service yet."}
        meta = read_meta(folder)
        return {"service": folder.name, "docs": _docs_in(folder), "folder": meta.get("folder"),
                "related": meta.get("related", []), "question": meta.get("question")}
    path = folder / f"{_name(name, 'A doc name')}.md"
    if path.is_symlink() or not path.is_file():
        raise PermanentToolError(f"No doc {path.stem!r} in the {folder.name} map yet")
    text = path.read_text(encoding="utf-8", errors="replace")
    return {"service": folder.name, "name": path.stem, "content": text[:DOC_LIMIT],
            "cut_short": len(text) > DOC_LIMIT}


def _planned(service: str, docs: Any, rebuild: bool = False) -> tuple[Path, list[tuple[Path, str, str]]]:
    if not isinstance(docs, dict) or not docs or not all(isinstance(v, str) for v in docs.values()):
        raise PermanentToolError('docs must be an object {"doc-name": "markdown", ...}')
    if len(docs) > MAX_DOCS:
        raise PermanentToolError(f"At most {MAX_DOCS} docs at once")
    folder = _folder(service)
    planned = []
    for name, content in docs.items():
        if len(content) > DOC_LIMIT:
            raise PermanentToolError(f"A doc can be at most {DOC_LIMIT:,} characters")
        path = folder / f"{_name(name, 'A doc name')}.md"
        if path.is_symlink():
            raise PermanentToolError("That doc is a link; it can't be used")
        before = path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""
        planned.append((path, before, content))
    # The skill's contract decides what may be created: checked before the approval prompt,
    # so a doc in the wrong shape is never shown nor saved.
    rules = contract()
    if rules:
        existing = set(_docs_in(folder)) if folder.is_dir() and not rebuild else set()
        problems = contracts.check(rules, {p.stem: c for p, _, c in planned}, existing)
        if problems:
            raise PermanentToolError("The service map breaks the skill's contract; nothing was saved. "
                                     "Fix these and call again:\n- " + "\n- ".join(problems))
    return folder, planned


def preview_service_map(service: str, docs: dict[str, str], question: str = "",
                        services: list[str] | None = None, rebuild: bool = False) -> str:
    """What write_service_map would change, every doc's diff, for one approval."""
    folder, planned = _planned(service, docs, rebuild)
    parts = [f"Service map: {folder}" + (" (rebuilt: docs not written now are removed)" if rebuild else "")]
    if services:
        parts.append("Services that take part: " + ", ".join(Path(p).name for p in _involved(services)))
    for path, before, content in planned:
        if before == content:
            parts.append(f"{path.name}: (no change)")
            continue
        lines = list(difflib.unified_diff(before.splitlines(), content.splitlines(),
                                          fromfile=f"{path.name} (before)" if before else "(new doc)",
                                          tofile=f"{path.name} (after)", lineterm="", n=2))
        parts.append("\n".join(lines[:200]) + ("\n… (diff cut)" if len(lines) > 200 else ""))
    return "\n\n".join(parts)


def _involved(services: list[str] | None) -> list[str]:
    """The services that take part, as absolute folders: the ones named (../billing or
    billing, beside the workspace) that exist, else the related folders given."""
    root = workspace_root()
    found = []
    for name in services or []:
        text = str(name).strip()
        if not text:
            continue
        folder = (root / text).resolve() if "/" in text else (root.parent / text).resolve()
        if folder.is_dir() and folder != root and str(folder) not in found:
            found.append(str(folder))
    if services:
        return found
    return [str(r) for r in related_roots() if not root.is_relative_to(r)]


def map_question(service: str) -> str | None:
    """What the map was made for (its question), or None."""
    try:
        folder = _folder(service)
    except PermanentToolError:
        return None
    return read_meta(folder).get("question") if folder.is_dir() else None


def write_service_map(service: str, docs: dict[str, str], question: str = "",
                      services: list[str] | None = None, rebuild: bool = False) -> dict[str, Any]:
    """Create or update a service's map in the library (one approval). Also records the
    service's folder, the services that take part and the question in meta.json."""
    folder, planned = _planned(service, docs, rebuild)
    folder.mkdir(parents=True, exist_ok=True)
    saved = []
    for path, before, content in planned:
        if before != content:
            temp = path.with_suffix(".md.tmp")
            temp.write_text(content, encoding="utf-8")
            os.replace(temp, path)
            saved.append(path.stem)
    removed = []
    if rebuild:   # a rebuilt map has only the docs written now
        written = {p.stem for p, _, _ in planned}
        for old in _docs_in(folder):
            if old not in written:
                (folder / f"{old}.md").unlink(missing_ok=True)
                removed.append(old)
    meta = read_meta(folder)
    rules = contract()
    present = set(_docs_in(folder)) | {p.stem for p, _, _ in planned}
    order = [d for d in (contracts.doc_order(rules) if rules else []) if d in present]
    order += [d for d in meta.get("docs", []) if isinstance(d, str) and d in present and d not in order]
    order += [p.stem for p, _, _ in planned if p.stem not in order]
    if rules:
        meta["titles"] = {name: (r or {}).get("title", name) for name, r in rules["docs"].items()}
    meta.update({"format": FORMAT, "folder": str(workspace_root()),
                 "related": _involved(services), "docs": order,
                 "updated_at": time.time()})
    if question:
        meta["question"] = question
    meta.setdefault("created_at", meta["updated_at"])
    temp = folder / (META + ".tmp")
    temp.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(temp, folder / META)
    return {"service": folder.name, "folder": str(folder), "saved": saved, "removed": removed,
            "unchanged": [p.stem for p, before, content in planned if before == content]}
