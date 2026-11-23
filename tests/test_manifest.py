"""tool.json loading: the real manifests load, and every kind of mistake gives a clear error."""

import json
from pathlib import Path

import pytest

from agent_core.errors import ToolManifestError
from agent_core.permissions import RiskLevel
from agent_core.tools import TOOL_MODULE_PREFIX
from agent_core.tools.manifest import load_tools
from agent_core.tools.registry import ExecutionMode

REPO_TOOLS = Path(__file__).parents[1] / "src" / "agent_core" / "tools" / "builtin"
ALLOWED = (TOOL_MODULE_PREFIX, "tests.")

GOOD = {
    "name": "echo",
    "description": "Echo text.",
    "risk_level": "read",
    "mode": "process",
    "timeout_seconds": 3,
    "parameters": {"text": {"type": "string", "description": "Text"}},
    "required": ["text"],
    "run": {"python": "tests.tool_fixtures:echo"},
}


def make_tool(tools_dir: Path, manifest, folder="echo") -> Path:
    folder_path = tools_dir / folder
    folder_path.mkdir(parents=True, exist_ok=True)
    text = manifest if isinstance(manifest, str) else json.dumps(manifest)
    (folder_path / "tool.json").write_text(text)
    return folder_path


def test_the_repo_tools_load():
    tools = {t.name: t for t in load_tools(REPO_TOOLS, (TOOL_MODULE_PREFIX,))}
    assert sorted(tools) == ["collect_review_comments", "edit_file", "grep", "list_folder", "read_file", "read_pull_request", "read_review_comments", "read_service_map", "read_session_doc", "run_command", "run_subagent", "save_analytics_report", "save_review_patterns", "use_skill", "write_file", "write_service_map", "write_session_doc", "write_session_docs"]
    assert tools["edit_file"].preview is not None and tools["write_file"].preview is not None
    assert tools["write_file"].risk_level == RiskLevel.WRITE
    assert tools["run_command"].risk_level == RiskLevel.DANGEROUS
    assert tools["run_command"].preview is not None
    assert tools["read_file"].execution_mode == ExecutionMode.PROCESS
    assert tools["read_file"].required == ["path"]
    assert tools["list_folder"].required == []  # "path" is optional


def test_good_manifest(tmp_path):
    make_tool(tmp_path, GOOD)
    [tool] = load_tools(tmp_path, ALLOWED)
    assert tool.name == "echo" and tool.timeout_seconds == 3
    assert tool.function(text="hi") == {"echo": "hi"}
    assert tool.openai_schema()["function"]["parameters"]["required"] == ["text"]


def test_missing_folder_means_no_tools(tmp_path):
    assert load_tools(tmp_path / "nope", ALLOWED) == []


def test_hidden_and_cache_folders_are_skipped(tmp_path):
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / ".draft").mkdir()
    assert load_tools(tmp_path, ALLOWED) == []


def bad(**changes):
    manifest = {**GOOD, **changes}
    return {k: v for k, v in manifest.items() if v is not ...}


@pytest.mark.parametrize("manifest,message", [
    ("{not json", "invalid JSON"),
    ("[]", "JSON object"),
    (bad(description=...), "missing key"),
    (bad(color="blue"), "unknown key"),
    (bad(name="other"), "must match its folder name"),
    (bad(risk_level="safe"), "risk_level must be one of"),
    (bad(mode="fast"), "mode must be one of"),
    (bad(parameters={"text": {"description": "no type"}}), "at least a 'type'"),
    (bad(parameters={"text": {"type": "string", "min": 1}}), "unknown key"),
    (bad(parameters={"text": {"type": "date"}}), "unsupported type"),
    (bad(required=["nope"]), "unknown parameter"),
    (bad(timeout_seconds="5"), "must be a number"),
    (bad(run={"command": ["python3", "x.py"]}), "not supported yet"),
    (bad(run={"python": "os:system"}), "is not allowed"),
    (bad(run={"python": "tests.tool_fixtures:missing"}), "cannot find"),
    (bad(run={"python": "no-colon"}), "must look like"),
    # the safety rules from ToolDefinition still apply:
    (bad(risk_level="write"), "Process execution supports only read tools"),
    (bad(mode="thread", risk_level="write", retry_attempts=3), "cannot enable automatic retries"),
])
def test_bad_manifests_are_rejected_with_the_file_name(tmp_path, manifest, message):
    make_tool(tmp_path, manifest)
    with pytest.raises(ToolManifestError, match=message) as error:
        load_tools(tmp_path, ALLOWED)
    assert "tool.json" in str(error.value)


def test_folder_without_tool_json_is_an_error(tmp_path):
    (tmp_path / "half_done").mkdir()
    with pytest.raises(ToolManifestError, match="missing tool.json"):
        load_tools(tmp_path, ALLOWED)
