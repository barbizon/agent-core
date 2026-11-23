"""Service maps: related folders (read only), the map library and the skill's contract."""

import json

import pytest

from agent_core import contracts
from agent_core.errors import PermanentToolError
from agent_core.tools import files, service_map

GOOD_MAP = "Focus: orders\n\n```mermaid\nflowchart LR\n  A --> B\n```\n"
GOOD_FLOWS = (
    "| # | Flow | Trigger | What it achieves |\n|---|---|---|---|\n| 1 | Reward | worker | pays coins |\n\n"
    "### 1. Refund job — pays refunds\nEvery 20s.\n\n```mermaid\nsequenceDiagram\n  A->>B: hi\n```\n"
)


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    ws, bonus, other = tmp_path / "go" / "orders", tmp_path / "go" / "billing", tmp_path / "go" / "shipping"
    for folder in (ws, bonus, other):
        folder.mkdir(parents=True)
    (bonus / "consumer.go").write_text("func Handle(price.list.changed) {}\n")
    (bonus / ".env").write_text("TOKEN=secret\n")
    (other / "x.go").write_text("price.list.changed\n")
    monkeypatch.setenv(files.WORKSPACE_ENV, str(ws))
    monkeypatch.setenv(files.RELATED_DIRS_ENV, str(bonus))
    monkeypatch.setenv(service_map.MAPS_DIR_ENV, str(tmp_path / "maps"))
    monkeypatch.delenv(files.PROTECTED_PATHS_ENV, raising=False)
    return ws


def test_a_related_folder_can_be_read_and_searched_but_never_written(workspace):
    assert files.read_file("../billing/consumer.go")["path"] == "../billing/consumer.go"
    found = files.grep("price.list.changed", "../billing")
    assert [m["path"] for m in found["matches"]] == ["../billing/consumer.go"]
    with pytest.raises(PermanentToolError, match="read but not changed"):
        files.write_file("../billing/new.go", "package x\n")
    with pytest.raises(PermanentToolError, match="secret"):
        files.read_file("../billing/.env")                     # secrets stay hidden there too
    with pytest.raises(PermanentToolError, match="outside the workspace or the related folders"):
        files.read_file("../shipping/x.go")                     # not a related folder


def test_the_contract_refuses_a_map_in_the_wrong_shape_before_any_approval(workspace):
    with pytest.raises(PermanentToolError) as refused:
        service_map.preview_service_map("orders", {
            "map": "no diagram here",
            "flows": "### 1. Reward job — pays\nno diagram\n",
            "notes": "free text",
        })
    problems = str(refused.value)
    assert "nothing was saved" in problems
    assert "map: needs at least 1 Mermaid flowchart" in problems
    assert "flows: needs a table with exactly these columns: | # | Flow | Trigger | What it achieves |" in problems
    assert "section “1. Reward job — pays” needs a Mermaid sequenceDiagram" in problems
    assert "notes: not a doc of this skill" in problems
    assert not (service_map.maps_dir() / "orders").exists()


def test_a_new_map_needs_its_required_docs_then_one_flow_can_be_updated_alone(workspace):
    with pytest.raises(PermanentToolError, match="flows: required"):
        service_map.write_service_map("orders", {"map": GOOD_MAP})
    saved = service_map.write_service_map("orders", {"flows": GOOD_FLOWS, "map": GOOD_MAP},
                                          question="Price lists")
    assert saved["saved"] == ["flows", "map"]
    meta = json.loads((service_map.maps_dir() / "orders" / "meta.json").read_text())
    assert meta["docs"] == ["map", "flows"]                     # the contract's order, not the call's
    assert meta["folder"] == str(workspace.resolve()) and meta["related"] == [str((workspace.parent / "billing").resolve())]
    assert meta["question"] == "Price lists" and meta["titles"]["open-questions"] == "Open questions"
    # Later: just one flow changes; the required map is already there. The services found
    # beside it are recorded (missing ones dropped).
    service_map.write_service_map("orders", {"flows": GOOD_FLOWS.replace("20s", "30s")},
                                  services=["../billing", "missing_repo"])
    meta = json.loads((service_map.maps_dir() / "orders" / "meta.json").read_text())
    assert meta["related"] == [str((workspace.parent / "billing").resolve())]
    listed = service_map.read_service_map()
    assert listed["maps"][0]["service"] == "orders" and listed["maps"][0]["docs"] == ["map", "flows"]
    assert "30s" in service_map.read_service_map("orders", "flows")["content"]


def test_diagram_types_and_table_headers_are_read_from_markdown():
    text = "```mermaid\n%% note\ngraph TD\n A-->B\n```\n| A | B |\n|:-|-:|\n| 1 | 2 |\n"
    assert contracts.diagram_types(text) == ["flowchart"]
    assert contracts.table_headers(text) == [["A", "B"]]


def test_the_code_root_is_the_folder_of_repos_around_the_workspace_or_the_setting(workspace, monkeypatch, tmp_path):
    monkeypatch.delenv(files.CODE_ROOT_ENV, raising=False)
    assert files.code_root() is None                             # the repos beside it aren't git repos
    for name in ("billing", "shipping"):
        (workspace.parent / name / ".git").mkdir()
    assert files.code_root() == workspace.parent.resolve()
    monkeypatch.setenv(files.RELATED_DIRS_ENV, "")
    assert files.open_code_root() == workspace.parent.resolve()
    assert files.read_file("../shipping/x.go")["content"].startswith("price.list.changed")
    with pytest.raises(PermanentToolError, match="can be read but not changed"):
        files.write_file("../shipping/y.go", "x")
    assert files.write_file("own.go", "package x\n")             # the workspace itself stays writable
    monkeypatch.setenv(files.CODE_ROOT_ENV, str(tmp_path / "maps"))   # the setting wins
    (tmp_path / "maps").mkdir(exist_ok=True)
    assert files.code_root() == (tmp_path / "maps").resolve()


def test_the_service_map_skill_opens_the_code_root_when_used(workspace, monkeypatch):
    from agent_core.tools import knowledge
    monkeypatch.delenv(files.CODE_ROOT_ENV, raising=False)
    monkeypatch.setenv(files.RELATED_DIRS_ENV, "")
    for name in ("billing", "shipping"):
        (workspace.parent / name / ".git").mkdir()
    monkeypatch.setenv(knowledge.SKILLS_ENV, json.dumps({"service-map": str(service_map.BUILTIN_SKILL_DIR)}))
    loaded = knowledge.use_skill("service-map")
    assert loaded["code_root"].startswith(f"../ ({workspace.parent.resolve()})")
    assert loaded["code_root_repos"] == ["billing", "shipping"]
    assert str(workspace.parent.resolve()) in files.related_roots().__repr__()


def test_a_map_without_a_name_gets_the_next_free_session_name(workspace, monkeypatch, tmp_path):
    from agent_core.tools import knowledge
    monkeypatch.setenv(knowledge.SESSION_DOCS_ENV, str(tmp_path / "2026-11-18_083757_cd61" / "docs"))
    assert service_map.auto_name("2026-11-18_083757_cd61") == "2026-11-18_083757_cd61_map1"
    first = service_map.write_service_map("", {"map": GOOD_MAP, "flows": GOOD_FLOWS})
    second = service_map.write_service_map(" ", {"map": GOOD_MAP, "flows": GOOD_FLOWS})
    assert (first["service"], second["service"]) == ("2026-11-18_083757_cd61_map1", "2026-11-18_083757_cd61_map2")


def test_a_rebuild_replaces_the_whole_map(workspace):
    service_map.write_service_map("m", {"map": GOOD_MAP, "flows": GOOD_FLOWS, "open-questions": "old"})
    with pytest.raises(PermanentToolError, match="flows: required"):
        service_map.write_service_map("m", {"map": GOOD_MAP}, rebuild=True)   # everything again
    saved = service_map.write_service_map("m", {"map": GOOD_MAP, "flows": GOOD_FLOWS}, rebuild=True)
    assert saved["removed"] == ["open-questions"]
    assert service_map.read_service_map("m")["docs"] == ["map", "flows"]
