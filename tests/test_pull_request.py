import json

import pytest

from agent_core.errors import PermanentToolError
from agent_core.tools import pull_request

LINK = "https://bitbucket.org/acme/payments/pull-requests/12"
INFO = {"title": "Limits", "state": "OPEN", "author": {"display_name": "Dana"}, "description": "why",
        "source": {"branch": {"name": "feature/PROJ-1-limits"}, "commit": {"hash": "abc123"}},
        "destination": {"branch": {"name": "develop"}}}
STAT = {"values": [{"status": "modified", "lines_added": 3, "lines_removed": 1,
                    "old": {"path": "a.go"}, "new": {"path": "a.go"}},
                   {"status": "removed", "lines_added": 0, "lines_removed": 9, "old": {"path": "b.go"}, "new": None}]}


@pytest.fixture
def bitbucket(monkeypatch):
    """Answers by the end of the URL; records every URL asked."""
    asked = []
    answers = {"/pullrequests/12": json.dumps(INFO), "/diffstat?pagelen=100": json.dumps(STAT),
               "/diff?path=a.go": "diff --git a/a.go b/a.go\n+x\n", "/diff?path=none.go": ""}

    def get(url):
        asked.append(url)
        if "/pullrequests?q=" in url:
            return json.dumps({"values": [{"id": 12}] if "PROJ-1-limits" in url else []}).encode()
        return next(body for end, body in answers.items() if url.endswith(end)).encode()

    monkeypatch.setattr(pull_request, "_get", get)
    return asked


def test_a_link_gives_the_pull_request_and_its_files(bitbucket):
    result = pull_request.read_pull_request(pr=LINK + "/diff")
    assert result["pr"] == LINK and result["source"] == "feature/PROJ-1-limits" and result["destination"] == "develop"
    assert result["commit"] == "abc123" and result["found"]
    assert result["files"] == [{"path": "a.go", "status": "modified", "added": 3, "removed": 1},
                               {"path": "b.go", "status": "removed", "added": 0, "removed": 9}]
    assert all("/acme/payments/" in url for url in bitbucket)


def test_a_file_gives_only_its_diff(bitbucket):
    assert pull_request.read_pull_request(pr=LINK, file="a.go")["diff"].startswith("diff --git a/a.go")
    with pytest.raises(PermanentToolError, match="doesn't change none.go"):
        pull_request.read_pull_request(pr=LINK, file="none.go")


def test_a_branch_finds_its_open_pull_request_or_says_there_is_none(bitbucket):
    assert pull_request.read_pull_request(repo="acme/payments", branch="feature/PROJ-1-limits")["number"] == 12
    assert pull_request.read_pull_request(repo="acme/payments", branch="other")["found"] is False


def test_a_wrong_link_and_a_missing_login_are_refused(monkeypatch):
    with pytest.raises(PermanentToolError, match="pr must be a link"):
        pull_request.read_pull_request(pr="payments 12")
    for name in ("BITBUCKET_EMAIL", "BITBUCKET_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(PermanentToolError, match="No Bitbucket login"):
        pull_request.read_pull_request(pr=LINK)
