"""Review analytics: collect pull request comments, group them, write the known-issues skill."""

import json

import pytest

from agent_core import skills
from agent_core.errors import PermanentToolError
from agent_core.tools import analytics, knowledge, pull_request

DIFF = ("diff --git a/pay.go b/pay.go\n--- a/pay.go\n+++ b/pay.go\n@@ -8,3 +8,4 @@ func Pay() error {\n"
        " \tif err != nil {\n+\t\treturn err\n \t}\n \treturn nil\n")


def comment(number, text, user="rev", parent=None, inline=True):
    return {"id": number, "content": {"raw": text}, "created_on": "2026-10-0%dT10:00:00+00:00" % (number % 9 + 1),
            "user": {"account_id": user}, **({"parent": {"id": parent}} if parent else {}),
            **({"inline": {"path": "pay.go", "to": 9}} if inline else {})}


@pytest.fixture
def bitbucket(monkeypatch, tmp_path):
    """Two pull requests with comments in acme/payments; records what was asked."""
    monkeypatch.setenv(analytics.ANALYTICS_DIR_ENV, str(tmp_path / "analytics"))
    asked = []
    prs = [{"id": 1, "comment_count": 4, "updated_on": "2026-10-01T10:00:00.000+00:00", "author": {"account_id": "dev"}},
           {"id": 2, "comment_count": 0, "updated_on": "2026-10-02T10:00:00.000+00:00", "author": {"account_id": "dev"}},
           {"id": 3, "comment_count": 1, "updated_on": "2026-10-03T10:00:00.000+00:00", "author": {"account_id": "dev"}}]
    comments = {1: [comment(11, "Wrap this error, the caller can't tell where it failed"),
                    comment(12, "Fixed in abc", user="dev", parent=11),
                    comment(13, "## Review passed", inline=False),
                    comment(14, "note to self", user="dev")],
                3: [comment(31, "add context to the returned err")]}

    def get_json(url):
        asked.append(url)
        if "/pullrequests?" in url:
            return {"values": prs}
        return {"values": comments[int(url.split("/pullrequests/")[1].split("/")[0])]}

    monkeypatch.setattr(pull_request, "_json", get_json)
    monkeypatch.setattr(pull_request, "_get", lambda url: DIFF.encode())
    return asked


def test_collect_keeps_review_comments_with_their_code_and_replies(bitbucket):
    with pytest.raises(PermanentToolError, match="No repos chosen yet"):
        analytics.collect_review_comments()
    result = analytics.collect_review_comments(["acme/payments"], 3)
    assert result["repos"] == {"acme/payments": {"pull_requests_read": 2, "new_comments": 2}}
    assert result["waiting_to_be_grouped"] == 2 and not result["more_pull_requests_left"]
    first, second = analytics.read_review_comments()["comments"]
    assert first["text"].startswith("Wrap this error") and first["answer"] == "Fixed in abc"   # the reply, kept with it
    assert "return err" in first["code"] and first["repo"] == "payments"
    assert second["id"] == 31
    # Again: the repos are remembered, only later pull requests are asked for, nothing is read twice.
    again = analytics.collect_review_comments()
    assert again["repos"]["acme/payments"]["new_comments"] == 0
    assert any("2026-10-03T10%3A00%3A00" in url for url in bitbucket[-3:])


def test_grouping_counts_patterns_and_refuses_what_it_does_not_know(bitbucket):
    analytics.collect_review_comments(["acme/payments"])
    pattern = {"id": "error-without-context", "name": "Error returned without context",
               "rule": "Wrap a returned error with what was being done.", "item": "E1"}
    with pytest.raises(PermanentToolError, match="No pattern"):
        analytics.save_review_patterns({"11": "nope"})
    with pytest.raises(PermanentToolError, match="needs an id"):
        analytics.save_review_patterns({"11": "x"}, [{"id": "Bad Id", "name": "n", "rule": "r"}])
    saved = analytics.save_review_patterns({"11": "error-without-context", "31": "error-without-context"}, [pattern])
    assert saved == {"grouped": 2, "merged": 0, "patterns": 1, "left": 0}
    [stored] = json.loads((analytics.analytics_dir() / "patterns.json").read_text())["patterns"]
    assert (stored["count"], stored["prs"], stored["repos"], stored["reviewers"]) == (2, 2, ["payments"], 1)
    assert stored["last_seen"] == "2026-10-05" and stored["item"] == "E1"
    assert analytics.read_review_comments()["patterns"][0]["seen"] == 2


def test_patterns_you_switch_on_become_a_skill_that_extends_code_check(bitbucket, monkeypatch, tmp_path):
    monkeypatch.setattr(skills, "USER_SKILLS_DIR", tmp_path / "skills")
    addon = tmp_path / "skills" / analytics.ADDON / "SKILL.md"
    analytics.collect_review_comments(["acme/payments"])
    analytics.save_review_patterns({"11": "error-without-context", "31": "skip"}, [
        {"id": "error-without-context", "name": "Error returned without context", "rule": "Wrap it.",
         "detect": "a bare return err"}])
    assert not addon.exists()                                   # a suggestion: nothing reaches reviews yet
    (analytics.analytics_dir() / "decisions.json").write_text('{"error-without-context": "on"}')
    analytics.sync_addon()
    text = addon.read_text()
    assert "extends: code-check" in text and "**K1 Error returned without context** (seen 1× in 1 PRs) — Wrap it." in text
    found = skills.discover(None, builtin_dir=skills.BUILTIN_SKILLS_DIR, claude_dir=None).skills
    monkeypatch.setenv(knowledge.SKILLS_ENV, json.dumps({s.name: str(s.folder) for s in found}))
    assert [a["name"] for a in knowledge.use_skill("code-check")["additions"]] == [analytics.ADDON]
    (analytics.analytics_dir() / "decisions.json").write_text('{"error-without-context": "muted"}')
    analytics.sync_addon()
    assert not addon.exists()                                   # muted: gone again
    addon.parent.mkdir(parents=True)
    addon.write_text("---\nname: x\n---\nmine\n")               # a file you wrote yourself is never replaced
    (analytics.analytics_dir() / "decisions.json").write_text('{"error-without-context": "on"}')
    analytics.sync_addon()
    assert addon.read_text().endswith("mine\n")


def test_a_repo_can_be_named_by_its_folder(tmp_path, monkeypatch, bitbucket):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "orders" / ".git").mkdir(parents=True)
    (tmp_path / "orders" / ".git" / "config").write_text('[remote "origin"]\n\turl = git@bitbucket.org:acme/orders.git\n')
    assert analytics._slug("orders") == "acme/orders" and analytics._slug("acme/payments") == "acme/payments"
    with pytest.raises(PermanentToolError, match="neither"):
        analytics._slug("nothing-here")


def test_an_analytics_is_saved_from_its_title_and_your_words_and_a_run_saves_its_report(monkeypatch, tmp_path):
    monkeypatch.setattr(skills, "USER_SKILLS_DIR", tmp_path / "skills")
    monkeypatch.setenv(analytics.ANALYTICS_DIR_ENV, str(tmp_path / "analytics"))
    wanted = "Which pull requests wait longest for a review?\nShow them per repo."
    assert analytics.create_analytics("Slow {pull} requests", wanted) == {"saved": "slow-pull-requests",
                                                                         "title": "Slow pull requests"}
    [made] = [s for s in skills.discover(None, claude_dir=None).skills if s.name == "slow-pull-requests"]
    assert made.kind == "analytics" and made.title == "Slow pull requests"
    assert [(a.id, a.inputs) for a in made.actions] == [("run", ())]
    body = (made.folder / "SKILL.md").read_text()
    assert wanted in body and "save_analytics_report` (analytics: `slow-pull-requests`)" in body
    assert json.loads((made.folder / "analytics.json").read_text())["wanted"] == wanted      # as you wrote it
    # Edited: the same name, new words.
    analytics.create_analytics("Slow PRs", "Only the open ones.", name="slow-pull-requests")
    assert json.loads((made.folder / "analytics.json").read_text()) == {"format": 1, "title": "Slow PRs",
                                                                       "wanted": "Only the open ones."}
    # Not a place to overwrite other skills, and the built-in ones are never shadowed.
    (tmp_path / "skills" / "mine").mkdir()
    (tmp_path / "skills" / "mine" / "SKILL.md").write_text("---\nname: mine\ndescription: d\n---\nx\n")
    for title, text in (("mine", "x"), ("code check", "x"), ("!", "x"), ("Fine title", " ")):
        with pytest.raises(PermanentToolError):
            analytics.create_analytics(title, text)

    assert analytics.save_analytics_report("slow-pull-requests", "**Result:** 3 wait over a week.\n") == {
        "saved": "slow-pull-requests", "runs": 1}
    assert analytics.save_analytics_report("slow-pull-requests", "**Result:** 2 now.")["runs"] == 2
    assert (tmp_path / "analytics" / "slow-pull-requests" / "report.md").read_text() == "**Result:** 2 now.\n"
    with pytest.raises(PermanentToolError):
        analytics.save_analytics_report("../x", "report")


def test_the_command_line_saves_an_analytics_without_starting_a_session(monkeypatch, tmp_path, capsys):
    from agent_core import cli
    monkeypatch.setattr(skills, "USER_SKILLS_DIR", tmp_path / "skills")
    monkeypatch.chdir(tmp_path)
    assert cli.main(["--create-analytics", "Slow PRs", "--wanted", "Which wait longest?"]) == 0
    assert json.loads(capsys.readouterr().out) == {"saved": "slow-prs", "title": "Slow PRs"}
    assert (tmp_path / "skills" / "slow-prs" / "SKILL.md").is_file() and not (tmp_path / ".orwick").exists()
    assert cli.main(["--create-analytics", "Slow PRs"]) == 2
    assert "Not saved: Say what you want to get" in capsys.readouterr().err


def test_analytics_skills_have_no_buttons_over_the_terminal():
    found = skills.discover(None, None, claude_dir=None).skills
    assert {s.name for s in found if s.kind == "analytics"} == {"review-analytics"}


def test_the_list_stays_short_merged_and_emptied_patterns_disappear(bitbucket):
    analytics.collect_review_comments(["acme/payments"])
    one = {"id": "error-without-context", "name": "Error without context", "rule": "Wrap it."}
    two = {"id": "bare-return", "name": "Bare return", "rule": "Wrap it too."}
    analytics.save_review_patterns({"11": "error-without-context", "31": "bare-return"}, [one, two])
    assert [p["id"] for p in analytics.read_review_comments()["patterns"]] == ["bare-return", "error-without-context"]
    with pytest.raises(PermanentToolError, match="both must be patterns"):
        analytics.save_review_patterns(merge={"bare-return": "nope"})
    merged = analytics.save_review_patterns(merge={"bare-return": "error-without-context"})
    assert merged["merged"] == 1 and merged["patterns"] == 1                      # the emptied one is gone
    [left] = analytics.read_review_comments()["patterns"]
    assert left["id"] == "error-without-context" and left["seen"] == 2
    assert analytics.save_review_patterns(merge={"error-without-context": "skip"})["patterns"] == 0
    # Past the limit nothing new is taken, and nothing is half-saved.
    many = [{"id": f"rule-{n:02d}", "name": f"Rule {n}", "rule": "r"} for n in range(analytics.MAX_PATTERNS + 1)]
    store = analytics.analytics_dir() / "comments.json"
    data = json.loads(store.read_text())
    data["comments"] = [{**data["comments"][0], "id": 100 + n, "pattern": None} for n in range(len(many))]
    store.write_text(json.dumps(data))
    with pytest.raises(PermanentToolError, match="the list holds 30"):
        analytics.save_review_patterns({str(100 + n): p["id"] for n, p in enumerate(many)}, many)
    assert analytics.read_review_comments()["patterns"] == []
    assert analytics.save_review_patterns({str(100 + n): p["id"] for n, p in enumerate(many[:30])}, many[:30])["patterns"] == 30
