"""Tools from three places (built-in, ~/.orwick/tools, the folder), and the code deciding their risk."""

import json

import pytest

from agent_core.errors import ToolManifestError
from agent_core.tools import BUILTIN, FOLDER, YOURS, load_all_tools
from agent_core.tools.manifest import load_manifest


def manifest(folder, name, run="agent_core.tools.files:read_file", risk="read", **extra):
    path = folder / name / "tool.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"name": name, "description": f"{name} tool", "risk_level": risk,
                                "parameters": {"path": {"type": "string"}}, "required": ["path"],
                                "run": {"python": run}, **extra}))
    return path


def test_every_folder_has_the_built_in_tools(tmp_path):
    loaded = load_all_tools(tmp_path / "none", tmp_path / "also-none")
    assert {t.name for t in loaded.registry.list_tools()} == {
        "read_file", "list_folder", "grep", "write_file", "edit_file", "run_command",
        "use_skill", "read_session_doc", "write_session_doc", "write_session_docs", "run_subagent",
        "read_service_map", "write_service_map", "read_pull_request",
        "collect_review_comments", "read_review_comments", "save_review_patterns",
        "save_analytics_report"}
    assert set(loaded.origins.values()) == {BUILTIN} and not loaded.notes


def test_your_tools_and_the_folders_tools_are_added(tmp_path):
    manifest(tmp_path / "yours", "peek")
    manifest(tmp_path / "folder", "look")
    loaded = load_all_tools(tmp_path / "folder", tmp_path / "yours")
    assert loaded.origins["peek"] == YOURS and loaded.origins["look"] == FOLDER
    assert len(loaded.registry) == 20


def test_a_built_in_tool_cant_be_replaced(tmp_path):
    manifest(tmp_path / "folder", "read_file", run="agent_core.tools.files:list_folder")
    loaded = load_all_tools(tmp_path / "folder", None)
    assert loaded.origins["read_file"] == BUILTIN
    assert loaded.registry.get("read_file").function.__name__ == "read_file"
    assert loaded.notes == [".orwick/tools/read_file skipped: a built-in tool has that name"]


def test_the_folder_wins_over_yours_for_the_same_name(tmp_path):
    manifest(tmp_path / "yours", "peek")
    manifest(tmp_path / "folder", "peek", run="agent_core.tools.files:list_folder")
    loaded = load_all_tools(tmp_path / "folder", tmp_path / "yours")
    assert loaded.origins["peek"] == FOLDER
    assert "replaces the one in ~/.orwick/tools" in loaded.notes[0]


@pytest.mark.parametrize("run, risk", [
    ("agent_core.tools.files:write_file", "read"),        # a write as "read": no approval would be asked
    ("agent_core.tools.files:edit_file", "read"),
    ("agent_core.tools.commands:run_command", "write"),   # a command below "dangerous"
])
def test_a_tool_json_cant_lower_the_risk_the_code_sets(tmp_path, run, risk):
    with pytest.raises(ToolManifestError, match="is too low"):
        load_manifest(manifest(tmp_path, "sneaky", run=run, risk=risk), ("agent_core.tools.",))


def test_a_higher_risk_than_needed_is_fine(tmp_path):
    tool = load_manifest(manifest(tmp_path, "careful_read", risk="write"), ("agent_core.tools.",))
    assert tool.risk_level == "write"


@pytest.mark.parametrize("run", ["agent_core.tools.commands:_execute", "agent_core.tools.files:workspace_root"])
def test_only_orwicks_tool_functions_can_be_run(tmp_path, run):
    with pytest.raises(ToolManifestError, match="is not one of Orwick's tool functions"):
        load_manifest(manifest(tmp_path, "inner", run=run, risk="dangerous"), ("agent_core.tools.",))


def test_only_orwicks_previews_can_be_used(tmp_path):
    with pytest.raises(ToolManifestError, match="is not one of Orwick's previews"):
        load_manifest(manifest(tmp_path, "odd", risk="read",
                               preview={"python": "agent_core.tools.files:read_file"}), ("agent_core.tools.",))
