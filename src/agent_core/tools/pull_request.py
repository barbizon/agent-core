"""Read a Bitbucket pull request: what it is, the files it changes, and one file's diff.

Commands run without network, so this is how the AI sees a pull request (the
code-check skill reviews one). It only reads, with your Bitbucket login from the
environment or ``~/.orwick/.env``: ``BITBUCKET_EMAIL`` and ``BITBUCKET_TOKEN`` (an API token).

* A link, or a repo with a branch: the pull request and its changed files.
* With ``file``: that file's diff.
"""

import base64
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from agent_core.errors import PermanentToolError, TemporaryToolError

API = "https://api.bitbucket.org/2.0/repositories"
LINK = re.compile(r"^https://bitbucket\.org/([\w.-]+)/([\w.-]+)/pull-requests/(\d+)")
REPO = re.compile(r"^[\w.-]+/[\w.-]+$")
DIFF_LIMIT = 16_000      # one file's diff; a longer one is cut, with a note
DESCRIPTION_LIMIT = 1_500
MAX_FILES = 300


def _get(url: str) -> bytes:
    login = os.environ.get("BITBUCKET_EMAIL"), os.environ.get("BITBUCKET_TOKEN")
    if not all(login):
        raise PermanentToolError("No Bitbucket login: set BITBUCKET_EMAIL and BITBUCKET_TOKEN in the "
                                 "environment Orwick starts from, or in ~/.orwick/.env.")
    basic = base64.b64encode(f"{login[0]}:{login[1]}".encode()).decode()
    request = urllib.request.Request(url, headers={"Authorization": f"Basic {basic}"})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise PermanentToolError("Bitbucket has no such repo or pull request (or this login can't see it).") from None
        if exc.code in (401, 403):
            raise PermanentToolError(f"Bitbucket refused the login (HTTP {exc.code}).") from None
        raise TemporaryToolError(f"Bitbucket answered HTTP {exc.code}.") from None
    except OSError as exc:
        raise TemporaryToolError(f"Can't reach Bitbucket ({exc}).") from None


def _json(url: str) -> dict[str, Any]:
    return json.loads(_get(url))


def _find(repo: str, branch: str) -> int | None:
    """The open pull request from this branch, if there is one."""
    query = urllib.parse.urlencode({"q": f'source.branch.name="{branch}" AND state="OPEN"',
                                    "fields": "values.id"})
    found = _json(f"{API}/{repo}/pullrequests?{query}")["values"]
    return found[0]["id"] if found else None


def _files(repo: str, number: int) -> list[dict[str, Any]]:
    files: list[dict[str, Any]] = []
    url: str | None = f"{API}/{repo}/pullrequests/{number}/diffstat?pagelen=100"
    while url and len(files) < MAX_FILES:
        page = _json(url)
        for item in page["values"]:
            files.append({"path": (item.get("new") or item.get("old"))["path"], "status": item["status"],
                          "added": item["lines_added"], "removed": item["lines_removed"]})
        url = page.get("next")
    return files


def read_pull_request(pr: str = "", repo: str = "", branch: str = "", file: str = "") -> dict[str, Any]:
    """``pr``: a pull request link. Or ``repo`` (workspace/repo) and ``branch``: the open
    pull request from that branch. ``file``: only that file's diff."""
    if pr:
        match = LINK.match(pr.strip())
        if not match:
            raise PermanentToolError("pr must be a link like https://bitbucket.org/<workspace>/<repo>/pull-requests/<number>")
        repo, number = f"{match[1]}/{match[2]}", int(match[3])
    else:
        if not REPO.match(repo) or not branch or '"' in branch:
            raise PermanentToolError("Give pr (a link), or repo as workspace/repo and its branch")
        found = _find(repo, branch)
        if found is None:
            return {"found": False, "note": f"No open pull request from {branch} in {repo}."}
        number = found
    link = f"https://bitbucket.org/{repo}/pull-requests/{number}"
    if file:
        query = urllib.parse.urlencode({"path": file})
        text = _get(f"{API}/{repo}/pullrequests/{number}/diff?{query}").decode("utf-8", errors="replace")
        if not text.strip():
            raise PermanentToolError(f"The pull request doesn't change {file}")
        return {"pr": link, "file": file, "diff": text[:DIFF_LIMIT], "cut_short": len(text) > DIFF_LIMIT}
    info = _json(f"{API}/{repo}/pullrequests/{number}")
    description = info.get("description") or ""
    return {
        "found": True, "pr": link, "number": number, "repo": repo,
        "title": info["title"], "state": info["state"], "author": info["author"]["display_name"],
        "source": info["source"]["branch"]["name"], "destination": info["destination"]["branch"]["name"],
        "commit": info["source"]["commit"]["hash"],
        "description": description[:DESCRIPTION_LIMIT],
        "files": _files(repo, number),
    }
