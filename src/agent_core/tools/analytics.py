"""Review analytics: what reviewers say again and again in pull requests.

Three steps, kept in ``~/.orwick/analytics/`` (outside any project and any session):

1. ``collect_review_comments`` reads the review comments of pull requests from Bitbucket
   (the comment, the code it was written on, the replies) into ``comments.json``. Each
   run reads only pull requests changed since the last one.
2. The AI groups them (the review-analytics skill): ``read_review_comments`` hands out
   the comments not grouped yet with the patterns known so far, and
   ``save_review_patterns`` records which pattern each belongs to. The counts are
   worked out here, never by the AI.
3. You decide in the app which patterns count (``decisions.json``: on / muted). The ones
   that are on are written out as a skill of yours that extends ``code-check``
   (``code-check-known-issues``), so every review also looks for them.

**Your own analytics.** Review issues are one analytics; you can make more. Each is a skill
of yours (``~/.orwick/skills/<name>/``, header ``kind: analytics``) that tells the AI how to
work one question out, with the questions to ask at each run. ``create_analytics`` writes
that skill straight from the title and the text you give (``orwick --create-analytics``,
used by the app: no AI, nothing is worked out yet); a run ends with ``save_analytics_report``, which keeps the
latest report in ``~/.orwick/analytics/<name>/report.md`` for the app's Analytics screen.

Nothing here changes a project, posts to Bitbucket or touches code. Reviewers are kept
as a short hash, only to count how many different people raised a pattern.
"""

import hashlib
import json
import os
import re
import shutil
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from agent_core.errors import PermanentToolError
from agent_core.tools import pull_request
from agent_core.tools.files import open_code_root, workspace_root

ANALYTICS_DIR_ENV = "ORWICK_ANALYTICS_DIR"   # tests point it elsewhere; default ~/.orwick/analytics
FORMAT = 1
ADDON = "code-check-known-issues"
GENERATED = "<!-- written by Orwick's review analytics; your changes here are replaced -->"
PATTERN_ID = re.compile(r"^[a-z0-9][a-z0-9-]{1,48}$")
REPO = re.compile(r"^[\w.-]+/[\w.-]+$")
ORIGIN = re.compile(r"bitbucket\.org[:/]([\w.-]+)/([\w.-]+?)(?:\.git)?\s*$", re.MULTILINE)
DEFAULT_MONTHS = 6
PRS_PER_RUN = 150          # pull requests with comments read in one call; the rest next call
TEXT_LIMIT, ANSWER_LIMIT, CODE_LINES = 700, 300, 3
BATCH = 40                 # comments handed to the AI at once
ADDON_LIMIT = 9_000        # characters: it shares use_skill's room with the team's own rules
SKIP = "skip"
MAX_PATTERNS = 30          # a short list of general rules; past it: reuse, merge or skip


def analytics_dir() -> Path:
    return Path(os.environ.get(ANALYTICS_DIR_ENV) or Path.home() / ".orwick" / "analytics")


def _load(name: str, default: Any) -> Any:
    try:
        return json.loads((analytics_dir() / name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _save(name: str, data: Any) -> None:
    target = analytics_dir() / name
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    temporary.replace(target)


def _comments() -> list[dict[str, Any]]:
    return _load("comments.json", {}).get("comments", [])


def _patterns() -> list[dict[str, Any]]:
    return _load("patterns.json", {}).get("patterns", [])


# --- 1. collect --------------------------------------------------------------------------

def _slug(repo: str) -> str:
    """workspace/repo for a repo given that way, or as a folder (here or in the code root)
    whose origin is on Bitbucket."""
    repo = repo.strip()
    if REPO.match(repo) and not Path(repo).exists():
        return repo
    roots = [workspace_root(), *( [open_code_root()] if open_code_root() else [] )]
    for root in roots:
        config = (root / repo / ".git" / "config") if repo not in (".", "") else root / ".git" / "config"
        try:
            found = ORIGIN.search(config.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
        if found:
            return f"{found[1]}/{found[2]}"
    raise PermanentToolError(f"{repo!r} is neither workspace/repo nor a folder whose origin is on Bitbucket")


def _code_at(diff: str, path: str, line: int) -> str:
    """The lines of ``path`` around new-file line ``line``, from a unified diff."""
    in_file, number, rows = False, 0, []
    for text in diff.splitlines():
        if text.startswith("diff --git "):
            in_file = text.endswith(" b/" + path)
            continue
        if not in_file or text.startswith(("+++", "---", "index ")):
            continue
        hunk = re.match(r"@@ -\d+(?:,\d+)? \+(\d+)", text)
        if hunk:
            number = int(hunk[1]) - 1
            continue
        if text.startswith("-"):
            continue
        number += 1
        if abs(number - line) <= CODE_LINES:
            rows.append(text)
    return "\n".join(rows)


def _pages(url: str) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    while url:
        page = pull_request._json(url)
        values += page.get("values", [])
        url = page.get("next")
    return values


def _read_pull_request(repo: str, number: int, author: str, known: dict[int, dict[str, Any]]) -> list[dict[str, Any]]:
    """The new review comments of one pull request: written on a line, by someone else than
    its author, each with the replies under it. Replies to comments read earlier are added
    to those."""
    raw = _pages(f"{pull_request.API}/{repo}/pullrequests/{number}/comments?pagelen=100")
    replies: dict[int, list[str]] = {}
    for item in raw:
        parent = (item.get("parent") or {}).get("id")
        if parent and not item.get("deleted"):
            replies.setdefault(parent, []).append(" ".join(item["content"]["raw"].split()))
    fresh = []
    for item in raw:
        answer = " / ".join(replies.get(item["id"], []))[:ANSWER_LIMIT]
        if item["id"] in known:
            known[item["id"]]["answer"] = answer or known[item["id"]].get("answer", "")
            continue
        inline, user = item.get("inline"), item.get("user") or {}
        if (item.get("parent") or item.get("deleted") or not inline or not inline.get("path")
                or user.get("account_id") == author or not item["content"]["raw"].strip()):
            continue
        fresh.append({
            "id": item["id"], "repo": repo, "pr": number, "path": inline["path"],
            "line": inline.get("to") or inline.get("from") or 0,
            "text": item["content"]["raw"].strip()[:TEXT_LIMIT], "code": "", "answer": answer,
            "reviewer": hashlib.sha1((user.get("account_id") or "?").encode()).hexdigest()[:6],
            "at": item["created_on"][:10], "pattern": None,
        })
    if fresh:
        diff = pull_request._get(f"{pull_request.API}/{repo}/pullrequests/{number}/diff").decode("utf-8", "replace")
        for comment in fresh:
            comment["code"] = _code_at(diff, comment["path"], comment["line"])
    return fresh


def collect_review_comments(repos: list[str] | None = None, months: int | None = None) -> dict[str, Any]:
    """Read new review comments from Bitbucket. ``repos`` (workspace/repo, or folder names)
    are remembered; with none given, the remembered ones are read again."""
    state = _load("state.json", {"repos": {}})
    for repo in repos or []:
        state["repos"].setdefault(_slug(repo), {})
    if not state["repos"]:
        raise PermanentToolError("No repos chosen yet: ask the user which repos to learn from "
                                 "(workspace/repo, or folder names), and for how many months back.")
    start = time.strftime("%Y-%m-%d", time.gmtime(time.time() - (months or DEFAULT_MONTHS) * 30 * 86400))
    comments = _comments()
    known = {c["id"]: c for c in comments}
    report: dict[str, Any] = {}
    budget, more = PRS_PER_RUN, False
    for repo, memory in state["repos"].items():
        since = memory.get("since") if months is None else start
        query = urllib.parse.urlencode({
            "q": f'updated_on >= {since or start}', "sort": "updated_on", "pagelen": 50,
            "fields": "next,values.id,values.comment_count,values.updated_on,values.author.account_id",
        }) + "&state=OPEN&state=MERGED&state=DECLINED"
        found = [p for p in _pages(f"{pull_request.API}/{repo}/pullrequests?{query}") if p.get("comment_count")]
        take, budget = found[:budget], budget - len(found[:budget])
        more = more or len(take) < len(found)
        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(
                lambda p: _read_pull_request(repo, p["id"], (p.get("author") or {}).get("account_id", ""), known),
                take))
        fresh = [comment for batch in results for comment in batch]
        comments += fresh
        known.update({c["id"]: c for c in fresh})
        if take:
            memory["since"] = take[-1]["updated_on"][:19]
        elif not found:
            memory["since"] = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
        report[repo] = {"pull_requests_read": len(take), "new_comments": len(fresh)}
    _save("comments.json", {"format": FORMAT, "comments": comments})
    _save("state.json", state)
    return {"repos": report, "waiting_to_be_grouped": sum(c["pattern"] is None for c in comments),
            "more_pull_requests_left": more,
            "next": "collect again" if more else "read_review_comments, then save_review_patterns, until none wait"}


# --- 2. group ----------------------------------------------------------------------------

def read_review_comments(limit: int = BATCH) -> dict[str, Any]:
    """The next comments nobody grouped yet, and the patterns known so far."""
    waiting = [c for c in _comments() if c["pattern"] is None]
    batch = waiting[:max(1, min(limit, BATCH))]
    return {
        "patterns": [{"id": p["id"], "name": p["name"], "rule": p["rule"], "seen": p.get("count", 0)}
                     for p in _patterns()],
        "comments": [{"id": c["id"], "repo": c["repo"].split("/")[-1], "file": c["path"], "text": c["text"],
                      "code": c["code"], "answer": c["answer"]} for c in batch],
        "left_after_these": len(waiting) - len(batch),
    }


def save_review_patterns(groups: dict[str, str] | None = None, patterns: list[dict[str, Any]] | None = None,
                         merge: dict[str, str] | None = None) -> dict[str, Any]:
    """Record which pattern each comment belongs to ("skip": none), adding or rewording
    patterns first. ``merge`` moves every comment of one pattern into another (or to
    "skip"). Counts are worked out from the comments; a pattern left without comments is
    removed."""
    stored = {p["id"]: p for p in _patterns()}
    known_before = set(stored)
    for raw in patterns or []:
        if not isinstance(raw, dict) or not PATTERN_ID.match(str(raw.get("id") or "")):
            raise PermanentToolError("Each pattern needs an id: lowercase words joined by -, e.g. error-without-context")
        entry = stored.setdefault(raw["id"], {"id": raw["id"], "name": "", "rule": "", "example": "",
                                               "detect": "", "item": ""})
        for key, limit in (("name", 80), ("rule", 300), ("example", 600), ("detect", 300), ("item", 8)):
            if isinstance(raw.get(key), str) and raw[key].strip():
                entry[key] = raw[key].strip()[:limit]
        if not entry["name"] or not entry["rule"]:
            raise PermanentToolError(f"Pattern {raw['id']} needs a name and a rule")
    groups, merge = groups or {}, merge or {}
    if not isinstance(groups, dict) or not isinstance(merge, dict) or not (groups or merge):
        raise PermanentToolError('Give groups {"<comment id>": "<pattern id>" or "skip"} and/or '
                                 'merge {"<pattern id>": "<pattern id to move its comments to>" or "skip"}')
    comments = _comments()
    for old, into in merge.items():
        if old not in stored or (into != SKIP and into not in stored) or old == into:
            raise PermanentToolError(f"merge {old!r} → {into!r}: both must be patterns that exist (or \"skip\")")
        for comment in comments:
            if comment["pattern"] == old:
                comment["pattern"] = into
    by_id = {str(c["id"]): c for c in comments}
    for comment_id, pattern in groups.items():
        if str(comment_id) not in by_id:
            raise PermanentToolError(f"No comment {comment_id}")
        if pattern != SKIP and pattern not in stored:
            raise PermanentToolError(f"No pattern {pattern!r}: add it in patterns, or use \"skip\"")
        by_id[str(comment_id)]["pattern"] = pattern
    kept = _counted(stored, comments)
    if len(kept) > MAX_PATTERNS and {p["id"] for p in kept} - known_before:
        raise PermanentToolError(
            f"That would make {len(kept)} patterns; the list holds {MAX_PATTERNS}. Nothing was saved. Keep it "
            "to general rules: put these comments in a pattern that exists, widen one, merge two, or skip them.")
    _save("comments.json", {"format": FORMAT, "comments": comments})
    _save("patterns.json", {"format": FORMAT, "updated_at": time.time(), "patterns": kept})
    sync_addon()
    return {"grouped": len(groups), "merged": len(merge), "patterns": len(kept),
            "left": sum(c["pattern"] is None for c in comments)}


def _counted(stored: dict[str, dict[str, Any]], comments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every pattern that has comments, with how often it was seen, the most widespread
    first. One left without comments (all moved or skipped) is dropped."""
    for pattern in stored.values():
        own = [c for c in comments if c["pattern"] == pattern["id"]]
        pattern.update(count=len(own), prs=len({(c["repo"], c["pr"]) for c in own}),
                       repos=sorted({c["repo"].split("/")[-1] for c in own}),
                       reviewers=len({c["reviewer"] for c in own}),
                       last_seen=max((c["at"] for c in own), default=""))
    return sorted((p for p in stored.values() if p["count"]), key=lambda p: (-p["prs"], -p["count"], p["id"]))


# --- 3. use ------------------------------------------------------------------------------

def sync_addon() -> None:
    """Write the patterns you switched on as the skill that extends code-check (or remove
    it when none is on). Only a file this wrote is ever replaced or removed."""
    from agent_core import skills
    folder = Path(skills.USER_SKILLS_DIR) / ADDON
    target = folder / "SKILL.md"
    decisions = _load("decisions.json", {})
    chosen = [p for p in _patterns() if decisions.get(p["id"]) == "on"]
    ours = target.is_file() and GENERATED in target.read_text(encoding="utf-8", errors="replace")
    if target.exists() and not ours:
        return
    if not chosen:
        if ours:
            shutil.rmtree(folder, ignore_errors=True)
        return
    lines = [
        "---", f"name: {ADDON}",
        "description: Issues reviewers raised again and again in past pull requests, learned by review analytics.",
        "extends: code-check", "---", GENERATED, "# Known issues from past reviews", "",
        "Reviewers keep commenting on these. Check the change for each one. A match is a finding",
        "like any other; start its \"What's wrong\" with the id and how often it was seen, e.g.",
        "\"K2 (seen in 9 PRs): …\". No match: list the id under Passed.", "",
    ]
    for number, pattern in enumerate(chosen, 1):
        entry = (f"**K{number} {pattern['name']}** (seen {pattern['count']}× in {pattern['prs']} PRs) — {pattern['rule']}"
                 + (f" Spot it: {pattern['detect']}" if pattern.get("detect") else "")
                 + (f" Example: {pattern['example']}" if pattern.get("example") else ""))
        if sum(len(line) + 1 for line in lines) + len(entry) > ADDON_LIMIT:
            break
        lines.append(entry)
    text = "\n".join(lines) + "\n"
    if not target.is_file() or target.read_text(encoding="utf-8") != text:
        folder.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")


# --- your own analytics ------------------------------------------------------------------

ANALYTICS_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{1,40}$")
REPORT_LIMIT = 100_000


def analytics_name(title: str) -> str:
    """A title as a skill name: lowercase words joined by - ("Slow PRs" → slow-prs)."""
    return "-".join(re.findall(r"[a-z0-9]+", title.lower()))[:40].strip("-")


def create_analytics(title: str, wanted: str, name: str = "") -> dict[str, Any]:
    """Save an analytics as a skill of yours, from its title and what you want to get, in
    your own words. Nothing is worked out now: that happens when you run it. ``name``: the
    analytics to replace (editing one keeps its name and its reports)."""
    from agent_core import skills
    title = " ".join(str(title).replace("{", "").replace("}", "").split())[:40]
    wanted = str(wanted).strip()
    name = name or analytics_name(title)
    if not ANALYTICS_NAME.match(name):
        raise PermanentToolError("The title needs at least two letters or digits")
    if not wanted or len(wanted) > WANTED_LIMIT:
        raise PermanentToolError(f"Say what you want to get, in at most {WANTED_LIMIT:,} characters")
    folder = Path(skills.USER_SKILLS_DIR) / name
    if (skills.BUILTIN_SKILLS_DIR / name).exists():
        raise PermanentToolError(f"{name!r} is one of Orwick's own skills: choose another title")
    if folder.exists() and not (folder / DEFINITION).is_file():
        raise PermanentToolError(f"A skill named {name!r} exists and is not an analytics: choose another title")
    one_line = " ".join(wanted.split())
    description = f"Analytics “{title}”: " + (one_line if len(one_line) <= 200 else one_line[:199] + "…")
    skill = (f"---\nname: {name}\ndescription: {description}\nkind: analytics\ntitle: {title}\n---\n"
             f"# {title}\n\nAn analytics the user runs again and again. Work it out from what Orwick can\n"
             "read, the same way each time, and end with a short report.\n\n"
             "## What the user wants to get\n\n" + wanted + "\n\n" + HOW_TO_RUN.replace("NAME", name))
    ui = json.dumps({"title": title, "hint": description[:300], "actions": [{
        "id": "run", "label": "Run", "icon": "chart.bar.xaxis", "max_steps": 80,
        "prompt": f"Use the {name} skill: work out the {title} analytics and save its report.",
        "hint": description[:300]}]}, indent=2, ensure_ascii=False) + "\n"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "SKILL.md").write_text(skill, encoding="utf-8")
    (folder / "ui.json").write_text(ui, encoding="utf-8")
    (folder / DEFINITION).write_text(json.dumps({"format": FORMAT, "title": title, "wanted": wanted},
                                                ensure_ascii=False, indent=1), encoding="utf-8")
    return {"saved": name, "title": title}


DEFINITION = "analytics.json"   # the title and the text you gave, as you gave them (the app shows and edits them)
WANTED_LIMIT = 6_000
HOW_TO_RUN = """## How to work it out

1. **What you can read:** pull requests on Bitbucket (`read_pull_request`: one pull request,
   its files, a file's diff; `collect_review_comments` then `read_review_comments`: review
   comments with their code and replies); tickets, with the ticket tools that are on; the
   code (`grep`, `read_file`, `list_folder`; other repos in the code root as `../<repo>`);
   git history and other commands (`run_command`: it asks the user each time and has no
   network); the session's docs and the service maps.
2. **Missing something only the user knows** (which repos, how far back, a ticket key)? Ask
   once, shortly, before you start. Otherwise don't ask: work.
3. **Keep it cheap:** few calls, filter early, read only what the answer needs.
4. **Facts only.** Count from what you read; say what could not be read or was too little
   to count. Never a guess presented as a number.

## The report

Markdown, in short, clear, human-readable English: a first line `**Result:**` with the
answer in one sentence; then the numbers as tables (most important first); then what they
mean, in a few plain sentences; then `**Not covered:**` for anything you could not work out.
Save it with `save_analytics_report` (analytics: `NAME`). Reply in 2 lines.
"""


def save_analytics_report(analytics: str, report: str) -> dict[str, Any]:
    """Keep an analytics' latest report (Markdown) for the app's Analytics screen."""
    if not ANALYTICS_NAME.match(analytics or ""):
        raise PermanentToolError("analytics must be the analytics skill's name, e.g. slow-prs")
    if not str(report).strip() or len(report) > REPORT_LIMIT:
        raise PermanentToolError(f"The report must be Markdown text, at most {REPORT_LIMIT:,} characters")
    folder = analytics_dir() / analytics
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "report.md").write_text(report.strip() + "\n", encoding="utf-8")
    runs = _load(f"{analytics}/meta.json", {}).get("runs", 0) + 1
    _save(f"{analytics}/meta.json", {"format": FORMAT, "updated_at": time.time(), "runs": runs})
    return {"saved": analytics, "runs": runs}
