"""Skills: instruction folders that teach Orwick a specific job, loaded only when needed.

A skill is a folder with a ``SKILL.md``: a short header, then the instructions::

    ---
    name: feature-docs
    description: Read the session's ticket and keep a short brief of it in the session docs.
    ---
    # Feature brief
    ...

Built for small context and speed:

* At start Orwick reads **only each header** (the first few KB of ``SKILL.md``).
* The AI always sees just a short **index** (``name: description``, cut short),
  with a hard size limit; past it, names only.
* The instructions are read only when the AI calls ``use_skill(name)``, a read
  tool. Old ``use_skill`` results are trimmed like any old tool output when the
  conversation gets long, and the AI can load the skill again.

**UI actions** (optional ``ui.json`` next to ``SKILL.md``): buttons a skill
offers, e.g. "Read ticket". ``/do <skill> <action>`` runs one in any terminal:
Orwick asks for the action's inputs (e.g. "Ticket ID:"), checks them, and sends
the action's prompt as if you had typed it. The Mac app shows them as buttons
that type that command. An input with ``"yes_no": true`` is a question answered
y or n (Enter = yes; its value is "yes" or "no"); an input with ``"unless": "<that
question>"`` is asked only after a no, and is its ``default`` otherwise.
``ui.json`` is never sent to the AI::

    {"title": "Feature docs", "hint": "Docs for the session's ticket.",
     "actions": [{"id": "read-ticket", "label": "Read ticket", "icon": "ticket",
                  "hint": "Reads the ticket and writes the feature docs.",
                  "inputs": [{"name": "ticket", "label": "Ticket ID", "pattern": "^[A-Z]+-[0-9]+$"}],
                  "feature": "{ticket}",
                  "prompt": "Make the feature docs for {ticket}."}]}

Where skills come from, in order: built in (``agent_core/skills_builtin/``),
Claude Code's (``~/.claude/skills/``), yours (``~/.orwick/skills/``), the folder's
(``.orwick/skills/``). A built-in name can't be replaced; yours win over Claude's,
a folder's over yours.

**Additions.** A skill whose header says ``extends: <skill>`` adds to that skill: when the
AI loads ``<skill>``, the additions that are on come with it (``use_skill`` returns them
under "additions"), and they win where they differ. An addition is not listed to the AI by
itself. So a general built-in skill (``code-check``) can stay free of one team's rules,
which live in ``~/.orwick/skills/code-check-myteam/SKILL.md`` with ``extends: code-check``.

The ``skills`` setting switches them on or off by name:
built-in and yours are on unless switched off; Claude's and a folder's are **off
unless switched on** (you decide what goes into the context, and a downloaded
project's skill is text that tells the AI what to do). Claude's skills may mention
Claude Code's own tools (Read, Bash…); here the AI does that work with Orwick's. Skills add knowledge, not powers: whatever they
suggest still goes through Orwick's tools and your approval.
"""

import json
import os
import re
import string
from dataclasses import dataclass, field
from pathlib import Path

from agent_core.config import AGENT_DIR

BUILTIN_SKILLS_DIR = Path(__file__).parent / "skills_builtin"
USER_SKILLS_DIR = Path.home() / AGENT_DIR / "skills"
FOLDER_SKILLS_DIR = Path(AGENT_DIR) / "skills"
CLAUDE_SKILLS_DIR = Path.home() / ".claude" / "skills"
BUILTIN, YOURS, FOLDER = "built-in", f"~/{AGENT_DIR}/skills", f"{AGENT_DIR}/skills"
CLAUDE = "~/.claude/skills"
OFF_UNLESS_SWITCHED_ON = (FOLDER, CLAUDE)

SKILLS_ENV = "ORWICK_SKILLS"     # {name: folder} of the switched-on skills, for use_skill
HEADER_BYTES = 4096
DESCRIPTION_LIMIT = 100
INDEX_LIMIT = 1600              # characters of the always-sent index (~400 tokens)
SKILL_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


ACTION_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$")
INPUT_NAME = re.compile(r"^[a-z][a-z0-9_]{0,30}$")


@dataclass(frozen=True)
class ActionInput:
    name: str
    label: str
    pattern: str | None = None
    uppercase: bool = False          # e.g. a ticket key typed as proj-16765 becomes PROJ-16765
    yes_no: bool = False             # a question answered y or n (Enter = yes); its value is "yes" or "no"
    unless: str | None = None        # asked only when that yes/no question was answered no
    default: str = ""                # its value when it isn't asked


@dataclass(frozen=True)
class Action:
    id: str
    label: str
    prompt: str
    icon: str = "sparkles"
    inputs: tuple[ActionInput, ...] = ()
    feature: str | None = None     # e.g. "{ticket}": link the session to that ticket first
    max_steps: int | None = None   # more AI steps than the default, for actions that research a lot
    hint: str = ""                 # one or two plain sentences: what the button does (the app's tooltip)

    def command(self, skill: str) -> str:
        return f"/do {skill} {self.id}"


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    folder: Path
    origin: str
    actions: tuple[Action, ...] = ()
    title: str = ""   # the group's caption over its buttons (ui.json "title"); "" = the name
    hint: str = ""    # the group's tooltip (ui.json "hint")
    kind: str = ""     # "analytics": run from the app's Analytics screen, no buttons over the terminal
    extends: str = ""  # the skill this one adds to: it loads with that skill, never by itself
    source: str = ""  # for Claude's skills: CUSTOM, "anthropic", "atlassian" or "open-source" (see source_of)


CUSTOM = "custom"


def source_of(skill_dir: Path) -> str:
    """Who made one of Claude Code's skills, from its license file. Your company's and your
    own skills carry none: they are CUSTOM. Outside skills ship one naming their maker
    (Anthropic, Atlassian), or an open-source license."""
    licenses = [p for p in skill_dir.iterdir() if p.is_file() and p.name.lower().startswith(("license", "licence"))]
    if not licenses:
        return CUSTOM
    try:
        text = licenses[0].read_text(encoding="utf-8", errors="replace")[:4000].lower()
    except OSError:
        return "open-source"
    for maker in ("anthropic", "atlassian"):
        if maker in text:
            return maker
    return "open-source"


@dataclass
class Found:
    skills: list[Skill] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def read_header(skill_md: Path) -> tuple[str, str] | None:
    """(name, description) from the ``---`` header; None when either is missing."""
    values = header_values(skill_md)
    if not values.get("name") or not values.get("description"):
        return None
    return values["name"], values["description"]


def header_values(skill_md: Path) -> dict[str, str]:
    """Every ``key: value`` of the ``---`` header; only the start of the file is read.

    Understands the YAML forms Claude's skills use: plain or quoted values, and
    multi-line ones (``description: |`` / ``>`` / ``>-`` with indented lines below).
    """
    try:
        with skill_md.open("rb") as file:
            head = file.read(HEADER_BYTES).decode("utf-8", errors="replace")
    except OSError:
        return {}
    if not head.startswith("---"):
        return {}
    values: dict[str, str] = {}
    block_key, block_lines = None, []

    def end_block() -> None:
        if block_key is not None:
            values[block_key] = " ".join(line.strip() for line in block_lines if line.strip())

    for line in head.splitlines()[1:]:
        if line.strip() == "---":
            break
        if block_key is not None and (line.startswith((" ", "\t")) or not line.strip()):
            block_lines.append(line)
            continue
        end_block()
        block_key, block_lines = None, []
        key, _, value = line.partition(":")
        value = value.strip()
        if value in ("|", ">", "|-", ">-", "|+", ">+"):
            block_key = key.strip()
            continue
        values[key.strip()] = value.strip('"').strip("'")
    end_block()
    return values


def discover(folder_dir: Path | None = FOLDER_SKILLS_DIR, user_dir: Path | None = ...,
             builtin_dir: Path = BUILTIN_SKILLS_DIR, claude_dir: Path | None = ...) -> Found:
    """Every skill: built-in, then Claude's, then yours, then the folder's (see the module docstring)."""
    if claude_dir is ...:
        claude_dir = CLAUDE_SKILLS_DIR   # read now, so tests can point it elsewhere
    if user_dir is ...:
        user_dir = USER_SKILLS_DIR
    chosen: dict[str, Skill] = {}
    found = Found()
    for origin, directory in ((BUILTIN, builtin_dir), (CLAUDE, claude_dir), (YOURS, user_dir),
                              (FOLDER, folder_dir)):
        if directory is None or not Path(directory).is_dir():
            continue
        for skill_dir in sorted(p for p in Path(directory).iterdir() if p.is_dir() and not p.is_symlink()):
            header = read_header(skill_dir / "SKILL.md")
            if header is None:
                continue
            name, description = header
            if origin == CLAUDE and SKILL_NAME.match(skill_dir.name):
                name = skill_dir.name   # Claude Code goes by the folder; its header name may differ ("tm")
            if not SKILL_NAME.match(name) or name != skill_dir.name:
                found.notes.append(f"{origin}/{skill_dir.name} skipped: its name must match the folder "
                                   "(lowercase letters, digits and -)")
                continue
            if name in chosen and chosen[name].origin == BUILTIN:
                found.notes.append(f"{origin}/{name} skipped: a built-in skill has that name")
                continue
            try:
                title, hint, actions = read_ui(skill_dir / "ui.json")
            except ValueError as exc:
                found.notes.append(f"{origin}/{name}/ui.json ignored: {exc}")
                title, hint, actions = "", "", ()
            values = header_values(skill_dir / "SKILL.md")
            chosen[name] = Skill(name, description, skill_dir, origin, actions, title, hint,
                                 values.get("kind", ""), values.get("extends", ""),
                                 source_of(skill_dir) if origin == CLAUDE else "")
    found.skills = sorted(chosen.values(), key=lambda s: s.name)
    return found


def read_actions(path: Path) -> tuple[Action, ...]:
    """A skill's UI actions from its ui.json (none if there is no file); ValueError if it's wrong."""
    return read_ui(path)[2]


def read_ui(path: Path) -> tuple[str, str, tuple[Action, ...]]:
    """A skill's ui.json: the group's title and hint, and its actions (all empty if there is
    no file); ValueError if it's wrong."""
    if not path.is_file():
        return "", "", ()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"can't read it ({exc})") from exc
    actions = []
    for raw in (data.get("actions") if isinstance(data, dict) else None) or []:
        if not isinstance(raw, dict):
            raise ValueError("each action must be an object")
        action_id, label, prompt = raw.get("id"), raw.get("label"), raw.get("prompt")
        if not isinstance(action_id, str) or not ACTION_ID.match(action_id):
            raise ValueError(f"action id {action_id!r} must be lowercase letters, digits and -")
        if not isinstance(label, str) or not 0 < len(label) <= 40:
            raise ValueError(f"{action_id}: label must be 1-40 characters")
        if not isinstance(prompt, str) or not prompt.strip() or prompt.lstrip().startswith("/") or len(prompt) > 2000:
            raise ValueError(f"{action_id}: prompt must be text (not a /command), at most 2000 characters")
        inputs = []
        for item in raw.get("inputs") or []:
            name = item.get("name") if isinstance(item, dict) else None
            if not isinstance(name, str) or not INPUT_NAME.match(name):
                raise ValueError(f"{action_id}: input name {name!r} is invalid")
            pattern = item.get("pattern")
            if pattern is not None:
                try:
                    re.compile(pattern)
                except re.error as exc:
                    raise ValueError(f"{action_id}: input {name} pattern is invalid ({exc})") from None
            unless = item.get("unless")
            if unless is not None and not any(i.name == unless and i.yes_no for i in inputs):
                raise ValueError(f"{action_id}: input {name}: unless must name a yes_no input before it")
            inputs.append(ActionInput(name, str(item.get("label") or name)[:40], pattern,
                                      item.get("uppercase") is True, item.get("yes_no") is True,
                                      unless, str(item.get("default") or "")))
        declared = {i.name for i in inputs}
        for text in (prompt, raw.get("feature") or ""):
            used = {field for _, field, _, _ in string.Formatter().parse(text) if field}
            if used - declared:
                raise ValueError(f"{action_id}: uses {sorted(used - declared)}, which aren't inputs")
        steps = raw.get("max_steps")
        if steps is not None and (not isinstance(steps, int) or isinstance(steps, bool) or not 1 <= steps <= 100):
            raise ValueError(f"{action_id}: max_steps must be a whole number from 1 to 100")
        hint = raw.get("hint") or ""
        if not isinstance(hint, str) or len(hint) > 300:
            raise ValueError(f"{action_id}: hint must be text, at most 300 characters")
        actions.append(Action(action_id, label, prompt, str(raw.get("icon") or "sparkles")[:40],
                              tuple(inputs), raw.get("feature") or None, steps, hint.strip()))
    if len(actions) > 6:
        raise ValueError("at most 6 actions per skill")
    title = data.get("title") if isinstance(data, dict) else None
    hint = data.get("hint") if isinstance(data, dict) else None
    if title is not None and (not isinstance(title, str) or len(title) > 40):
        raise ValueError("title must be text, at most 40 characters")
    if hint is not None and (not isinstance(hint, str) or len(hint) > 300):
        raise ValueError("hint must be text, at most 300 characters")
    return (title or "").strip(), (hint or "").strip(), tuple(actions)


def default_on(skill: Skill) -> bool:
    """Built-in and yours: on unless switched off. A folder's and Claude's: off unless
    switched on (you decide what goes into the AI's context)."""
    return skill.origin not in OFF_UNLESS_SWITCHED_ON


def describe(skills: list[Skill], on: list[Skill] | None = None, settings: dict[str, bool] | None = None,
             session: dict[str, bool] | None = None) -> list[dict]:
    """Every skill as plain data for the app: where it's from, whether it's on by default,
    and (with ``on``) whether this session uses it; ``setting``: on in the settings (before
    this session's own switch), ``session``: this session's switch (null: none)."""
    names_on = None if on is None else {s.name for s in on}
    rows = []
    for s in skills:
        row = {"name": s.name, "origin": s.origin, "source": s.source or None, "title": s.title or None,
               "description": " ".join(s.description.split())[:300], "default": default_on(s),
               "actions": len(s.actions)}
        if names_on is not None:
            row["on"] = s.name in names_on
            row["setting"] = (settings or {}).get(s.name, default_on(s))
            row["session"] = (session or {}).get(s.name)
        rows.append(row)
    return rows


def change_note(before: list[Skill], after: list[Skill]) -> str:
    """What the AI is told when this session's skills change mid-conversation (the system
    prompt stays as it was, so nothing is sent again); "" when nothing changed."""
    old, new = {s.name for s in before}, {s.name for s in after}
    added = [s for s in after if s.name not in old]
    removed = sorted(old - new)
    if not added and not removed:
        return ""
    lines = ["[Orwick] The skills for this session changed."]
    if added:
        lines.append("Now available (load one with use_skill when a task matches it):")
        lines += [f"- {s.name}: {_short(s.description)}" for s in added]
    if removed:
        lines.append(f"No longer available, don't use: {', '.join(removed)}.")
    return "\n".join(lines)


def switched_on(skills: list[Skill], switches: dict[str, bool]) -> list[Skill]:
    """The skills to use: each one's switch in the settings, else its default."""
    return [s for s in skills if switches.get(s.name, default_on(s))]


def index(skills: list[Skill], limit: int = INDEX_LIMIT) -> str:
    """The short index the AI always sees (empty when there are no skills)."""
    skills = [s for s in skills if not s.extends]   # an addition loads with the skill it extends
    if not skills:
        return ""
    head = ("## Skills\nWhen a task matches a skill, first call use_skill with its name to load its "
            "instructions, then follow them.\n")
    lines = [f"- {s.name}: {_short(s.description)}" for s in skills]
    text = head + "\n".join(lines)
    if len(text) <= limit:
        return text
    names = ", ".join(s.name for s in skills)
    text = head + f"Skills: {names}"
    return text if len(text) <= limit else text[:limit - 12].rsplit(",", 1)[0] + ", … (more)"


def export(skills: list[Skill]) -> None:
    """Tell the use_skill tool which skills are on, and where they are."""
    os.environ[SKILLS_ENV] = json.dumps({s.name: str(s.folder) for s in skills})


def _short(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= DESCRIPTION_LIMIT else text[:DESCRIPTION_LIMIT - 1] + "…"
