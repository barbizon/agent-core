"""Skills (small index, loaded on demand) and session docs."""

import json
import os

import pytest

from agent_core import skills
from agent_core.errors import PermanentToolError
from agent_core.tools import knowledge


def skill(folder, name, description="Does a thing.", body="Step 1. Do it.\n"):
    path = folder / name / "SKILL.md"
    path.parent.mkdir(parents=True)
    path.write_text(f"---\nname: {name}\ndescription: {description}\n---\n{body}")
    return path


def test_only_the_header_is_read_at_start(tmp_path):
    path = skill(tmp_path, "big", body="x" * 1_000_000)
    assert skills.read_header(path) == ("big", "Does a thing.")
    (tmp_path / "nohead").mkdir()
    (tmp_path / "nohead" / "SKILL.md").write_text("# no header")
    assert skills.read_header(tmp_path / "nohead" / "SKILL.md") is None


def test_built_in_then_yours_then_the_folders(tmp_path):
    skill(tmp_path / "yours", "notes")
    skill(tmp_path / "folder", "notes", description="Project version.")
    skill(tmp_path / "folder", "feature-docs", description="Imposter.")     # a built-in name
    skill(tmp_path / "folder", "Bad_Name")
    found = skills.discover(tmp_path / "folder", tmp_path / "yours")
    by_name = {s.name: s for s in found.skills}
    assert by_name["feature-docs"].origin == skills.BUILTIN
    assert by_name["notes"].origin == skills.FOLDER and by_name["notes"].description == "Project version."
    assert any("feature-docs skipped" in n for n in found.notes)
    assert any("Bad_Name skipped" in n for n in found.notes)


def test_a_projects_skills_are_off_unless_switched_on(tmp_path):
    skill(tmp_path / "folder", "deploy")
    found = skills.discover(tmp_path / "folder", None).skills
    assert [s.name for s in skills.switched_on(found, {})] == ["code-check", "feature-docs", "review-analytics", "service-map"]
    assert {s.name for s in skills.switched_on(found, {"deploy": True, "feature-docs": False, "service-map": False,
                                                       "code-check": False, "review-analytics": False})} == {"deploy"}


def test_the_index_stays_small(tmp_path):
    for i in range(80):
        skill(tmp_path, f"skill-{i:02d}", description="A long description " * 20)
    found = skills.discover(tmp_path, None, builtin_dir=tmp_path / "none").skills
    text = skills.index(found)
    assert len(text) <= skills.INDEX_LIMIT and "use_skill" in text
    assert skills.index([]) == ""
    one = skills.index(found[:1])
    assert "- skill-00: A long description" in one and one.endswith("…")


def test_use_skill_returns_the_instructions_without_the_header(tmp_path, monkeypatch):
    path = skill(tmp_path, "notes", body="# Notes\nStep 1.\n")
    (path.parent / "template.md").write_text("x")
    monkeypatch.setenv(knowledge.SKILLS_ENV, json.dumps({"notes": str(path.parent)}))
    result = knowledge.use_skill("notes")
    assert result["instructions"] == "# Notes\nStep 1." and result["other_files"] == ["template.md"]
    with pytest.raises(PermanentToolError, match="No skill named"):
        knowledge.use_skill("other")


@pytest.fixture
def docs(tmp_path, monkeypatch):
    folder = tmp_path / "session" / "docs"
    monkeypatch.setenv(knowledge.SESSION_DOCS_ENV, str(folder))
    return folder


def test_session_docs_write_read_list_and_preview(docs):
    assert knowledge.read_session_doc() == {"docs": []}
    assert "(new doc)" in knowledge.preview_session_doc("feature-docs", "# PROJ-1\n")
    assert knowledge.write_session_doc("feature-docs", "# PROJ-1\n")["created"] is True
    assert knowledge.read_session_doc("feature-docs")["content"] == "# PROJ-1\n"
    assert knowledge.read_session_doc("feature-docs.md")["name"] == "feature-docs"
    assert "+## Open" in knowledge.preview_session_doc("feature-docs", "# PROJ-1\n## Open\n")
    assert knowledge.read_session_doc() == {"docs": ["feature-docs"]}
    assert not list(docs.glob("*.tmp"))


@pytest.mark.parametrize("name", ["../escape", "a/b", "Notes", ".hidden", ""])
def test_doc_names_are_plain_names_only(docs, name):
    with pytest.raises(PermanentToolError):
        knowledge.write_session_doc(name, "x")


def test_docs_refuse_links_and_huge_content(docs, tmp_path):
    docs.mkdir(parents=True)
    (tmp_path / "outside.md").write_text("secret")
    os.symlink(tmp_path / "outside.md", docs / "link.md")
    with pytest.raises(PermanentToolError, match="link"):
        knowledge.read_session_doc("link")
    with pytest.raises(PermanentToolError, match="at most"):
        knowledge.write_session_doc("big", "x" * (knowledge.DOC_LIMIT + 1))


def test_without_a_session_there_are_no_docs(monkeypatch):
    monkeypatch.delenv(knowledge.SESSION_DOCS_ENV, raising=False)
    with pytest.raises(PermanentToolError, match="no docs folder"):
        knowledge.read_session_doc()



def test_several_docs_are_written_with_one_approval(docs):
    preview = knowledge.preview_session_docs({"product-requirement": "# What\n", "todo": "## Left\n"})
    assert "docs/product-requirement.md (after)" in preview and "docs/todo.md (after)" in preview
    assert knowledge.write_session_docs({"product-requirement": "# What\n", "todo": "## Left\n"})["saved"] == \
        ["product-requirement", "todo"]
    result = knowledge.write_session_docs({"product-requirement": "# What\n", "todo": "## Left\n- one\n"})
    assert result == {"saved": ["todo"], "unchanged": ["product-requirement"]}
    for bad in ({}, {"a": 1}, {f"d{i}": "x" for i in range(11)}, {"../x": "y"}):
        with pytest.raises(PermanentToolError):
            knowledge.write_session_docs(bad)



# --- UI actions (ui.json) ---------------------------------------------------------------

def actions_file(tmp_path, actions):
    path = tmp_path / "ui.json"
    path.write_text(json.dumps({"actions": actions}))
    return path


def test_the_built_in_skill_offers_read_ticket_and_update_docs():
    feature_docs = next(s for s in skills.discover(None, None).skills if s.name == "feature-docs")
    assert [a.id for a in feature_docs.actions] == ["read-ticket", "update-docs"]
    read = feature_docs.actions[0]
    assert read.command("feature-docs") == "/do feature-docs read-ticket"
    assert [i.name for i in read.inputs] == ["ticket"] and read.feature == "{ticket}"
    assert read.max_steps == 40 and read.inputs[0].uppercase


def test_no_ui_json_means_no_actions(tmp_path):
    assert skills.read_actions(tmp_path / "ui.json") == ()


def test_the_code_check_skill_offers_review_and_grade():
    check = next(s for s in skills.discover(None, None).skills if s.name == "code-check")
    assert [(a.id, a.label) for a in check.actions] == [("review-code", "Review code"), ("grade-code", "Code Scoring")]
    assert all(a.hint for a in check.actions)
    assert check.title == "Code review checklist" and check.hint
    assert all([i.name for i in a.inputs] == ["own", "pr"] and a.max_steps == 60 for a in check.actions)
    own, pr = check.actions[0].inputs
    assert own.yes_no and pr.unless == "own" and pr.default == "this session's pull request"
    assert "read_pull_request" in (check.folder / "SKILL.md").read_text()
    body = (check.folder / "SKILL.md").read_text()
    assert len(body) < 12_000                               # use_skill's limit: nothing is cut off
    assert "`review-findings`" in body and "`grade`" in body


@pytest.mark.parametrize("bad", [
    [{"id": "Bad Id", "label": "x", "prompt": "p"}],
    [{"id": "a", "label": "", "prompt": "p"}],
    [{"id": "a", "label": "x", "prompt": "/clear"}],                       # no slash commands
    [{"id": "a", "label": "x", "prompt": "Use {nope}"}],                   # undeclared input
    [{"id": "a", "label": "x", "prompt": "p", "inputs": [{"name": "t", "pattern": "("}]}],
    [{"id": f"a{i}", "label": "x", "prompt": "p"} for i in range(7)],
    [{"id": "a", "label": "x", "prompt": "p", "max_steps": 0}],
    [{"id": "a", "label": "x", "prompt": "p", "max_steps": "40"}],
])
def test_bad_actions_are_refused(tmp_path, bad):
    with pytest.raises(ValueError):
        skills.read_actions(actions_file(tmp_path, bad))


def test_a_skill_with_a_broken_ui_json_still_loads_without_actions(tmp_path):
    path = skill(tmp_path, "notes")
    (path.parent / "ui.json").write_text("{broken")
    found = skills.discover(None, tmp_path, builtin_dir=tmp_path / "none")
    assert found.skills[0].actions == () and "ui.json ignored" in found.notes[0]


def test_a_ui_group_title_and_hint_are_checked(tmp_path):
    ui = tmp_path / "ui.json"
    ui.write_text(json.dumps({"title": "Mine", "hint": "What these do.", "actions": []}))
    assert skills.read_ui(ui) == ("Mine", "What these do.", ())
    ui.write_text(json.dumps({"title": "x" * 41, "actions": []}))
    with pytest.raises(ValueError):
        skills.read_ui(ui)


def test_claude_skills_use_yaml_block_descriptions_and_their_folder_name(tmp_path):
    folder = tmp_path / "claude" / "task-management"
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text("---\nname: tm\ndescription: |\n  Personal task tracking.\n"
                                     "  Use it for tasks.\nallowed-tools: Read\n---\nBody\n")
    other = tmp_path / "claude" / "twg"
    other.mkdir()
    (other / "SKILL.md").write_text("---\nname: twg\ndescription: >-\n  Use TWG for Jira.\n---\n")
    (tmp_path / "claude" / "golang-shared").mkdir()          # shared files, no SKILL.md: not a skill
    found = skills.discover(None, None, claude_dir=tmp_path / "claude")
    claude = {s.name: s for s in found.skills if s.origin == skills.CLAUDE}
    assert set(claude) == {"task-management", "twg"} and not found.notes
    assert claude["task-management"].description == "Personal task tracking. Use it for tasks."
    assert claude["twg"].description == "Use TWG for Jira."
    on = skills.switched_on(found.skills, {})                  # Claude's are off unless switched on
    assert {s.name for s in on} == {"code-check", "feature-docs", "review-analytics", "service-map"}
    assert "twg" in {s.name for s in skills.switched_on(found.skills, {"twg": True})}
    described = {d["name"]: d for d in skills.describe(found.skills, on)}
    assert described["twg"] == {"name": "twg", "origin": skills.CLAUDE, "source": "custom", "title": None,
                                "description": "Use TWG for Jira.", "default": False, "actions": 0, "on": False,
                                "setting": False, "session": None}


def test_yours_win_over_claudes_with_the_same_name(tmp_path):
    skill(tmp_path / "claude", "notes", description="Claude's.")
    skill(tmp_path / "yours", "notes", description="Mine.")
    found = skills.discover(None, tmp_path / "yours", claude_dir=tmp_path / "claude")
    [notes] = [s for s in found.skills if s.name == "notes"]
    assert notes.origin == skills.YOURS and notes.description == "Mine."


def test_use_skill_reads_the_skills_files_but_nothing_outside_its_skills_folder(tmp_path, monkeypatch):
    root = tmp_path / "skills"
    skill(root, "go-impl")
    (root / "go-impl" / "references").mkdir()
    (root / "go-impl" / "references" / "conventions.md").write_text("Use XxxDao.")
    (root / "golang-shared").mkdir()
    (root / "golang-shared" / "rules.md").write_text("Rule 1.")
    (root / "go-impl" / "token.pem").write_text("secret")
    (tmp_path / "outside.md").write_text("no")
    monkeypatch.setenv(knowledge.SKILLS_ENV, json.dumps({"go-impl": str(root / "go-impl")}))
    assert "references/conventions.md" in knowledge.use_skill("go-impl")["other_files"]
    assert knowledge.use_skill("go-impl", file="references/conventions.md")["content"] == "Use XxxDao."
    assert knowledge.use_skill("go-impl", file="../golang-shared/rules.md")["content"] == "Rule 1."
    for bad in ("../../outside.md", "token.pem", "references/missing.md"):
        with pytest.raises(PermanentToolError):
            knowledge.use_skill("go-impl", file=bad)
    (root / "go-impl" / "link.md").symlink_to(tmp_path / "outside.md")
    with pytest.raises(PermanentToolError):
        knowledge.use_skill("go-impl", file="link.md")


def test_claude_skills_without_a_license_are_custom_others_name_their_maker(tmp_path):
    root = tmp_path / "claude"
    for name, license_text in (("golang-testing", None), ("docx", "© 2025 Anthropic, PBC. All rights reserved."),
                               ("twg", "© 2026 Atlassian. All rights reserved."),
                               ("prompt-master", "MIT License\nCopyright (c) 2026 Someone")):
        skill(root, name)
        if license_text:
            (root / name / "LICENSE.txt").write_text(license_text)
    found = skills.discover(None, None, claude_dir=root)
    assert {s.name: s.source for s in found.skills if s.origin == skills.CLAUDE} == {
        "golang-testing": "custom", "docx": "anthropic", "twg": "atlassian", "prompt-master": "open-source"}
    assert {s.name: s.source for s in found.skills if s.origin == skills.BUILTIN} == {
        "code-check": "", "feature-docs": "", "review-analytics": "", "service-map": ""}


def test_an_input_asked_unless_must_follow_its_yes_no_question(tmp_path):
    asks = {"name": "whole", "label": "Whole folder?", "yes_no": True}
    flow = {"name": "flow", "unless": "whole", "default": "everything"}
    [action] = skills.read_actions(actions_file(tmp_path, [{"id": "a", "label": "x", "prompt": "p {flow}",
                                                            "inputs": [asks, flow]}]))
    assert action.inputs[0].yes_no and action.inputs[1].unless == "whole" and action.inputs[1].default == "everything"
    for inputs in ([flow, asks], [dict(asks, yes_no=False), flow]):
        with pytest.raises(ValueError, match="unless must name a yes_no input"):
            skills.read_actions(actions_file(tmp_path, [{"id": "a", "label": "x", "prompt": "p", "inputs": inputs}]))


def test_an_addition_loads_with_the_skill_it_extends_and_is_not_listed_alone(tmp_path, monkeypatch):
    for name, header, body in [("review", "", "Check the diff."),
                               ("review-myteam", "extends: review\n", "Our errors use the Problem envelope."),
                               ("other", "", "Something else.")]:
        (tmp_path / name).mkdir()
        (tmp_path / name / "SKILL.md").write_text(f"---\nname: {name}\ndescription: about {name}\n{header}---\n{body}\n")
    found = skills.discover(None, tmp_path, builtin_dir=tmp_path / "none", claude_dir=None).skills
    assert {s.name: s.extends for s in found} == {"other": "", "review": "", "review-myteam": "review"}
    listed = skills.index(found)
    assert "- review: about review" in listed and "review-myteam" not in listed
    monkeypatch.setenv(knowledge.SKILLS_ENV, json.dumps({s.name: str(s.folder) for s in found}))
    loaded = knowledge.use_skill("review")
    assert loaded["instructions"] == "Check the diff."
    assert loaded["additions"] == [{"name": "review-myteam", "instructions": "Our errors use the Problem envelope."}]
    assert "additions" not in knowledge.use_skill("other")
    # Switched off, the addition is not in the session's skills, so it doesn't load.
    monkeypatch.setenv(knowledge.SKILLS_ENV, json.dumps({"review": str(tmp_path / "review")}))
    assert "additions" not in knowledge.use_skill("review")
