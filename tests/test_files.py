"""File and folder tools: workspace limits and behavior, directly and through the gateway."""

import pytest

from agent_core.errors import PermanentToolError
from agent_core.execution.process_executor import ProcessExecutor
from agent_core.gateway import ToolGateway
from agent_core.permissions import PermissionPolicy
from agent_core.tools import TOOL_MODULE_PREFIX, build_registry, files
from tests.conftest import RecordingApprover

REPO_TOOLS = __import__("pathlib").Path(__file__).parents[1] / ".orvik" / "tools"


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv(files.WORKSPACE_ENV, str(tmp_path))
    return tmp_path


@pytest.fixture
def file_gateway(audit):
    """The real registry behind a gateway whose worker may import agent_core.tools."""
    def factory(policy=None, approver=None) -> ToolGateway:
        return ToolGateway(
            registry=build_registry(REPO_TOOLS),
            permission_policy=policy or PermissionPolicy(),
            approval_provider=approver or RecordingApprover(False),
            audit_logger=audit,
            process_executor=ProcessExecutor(allowed_module_prefixes=(TOOL_MODULE_PREFIX,)),
        )
    return factory


def test_workspace_defaults_to_the_start_folder(monkeypatch, tmp_path):
    monkeypatch.delenv(files.WORKSPACE_ENV, raising=False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "Downloads").mkdir()

    assert files.resolve_path("Downloads") == tmp_path.resolve() / "Downloads"


def test_list_folder_shows_folders_first(workspace):
    (workspace / "b.txt").write_text("hi")
    (workspace / "a_dir").mkdir()

    result = files.list_folder()

    assert result == {
        "path": ".",
        "entries": [{"name": "a_dir", "type": "folder"}, {"name": "b.txt", "type": "file", "size": 2}],
        "truncated": False,
    }


def test_list_folder_of_a_file_is_refused(workspace):
    (workspace / "a.txt").write_text("hi")
    with pytest.raises(PermanentToolError, match="Not a folder"):
        files.list_folder("a.txt")


def test_read_file_returns_content(workspace):
    (workspace / "notes").mkdir()
    (workspace / "notes" / "todo.txt").write_text("buy milk", encoding="utf-8")

    assert files.read_file("notes/todo.txt") == {"path": "notes/todo.txt", "content": "buy milk"}


def test_read_file_refuses_binary(workspace):
    (workspace / "img.bin").write_bytes(b"\xff\xfe\x00")
    with pytest.raises(PermanentToolError, match="UTF-8"):
        files.read_file("img.bin")


def test_read_file_refuses_too_big(workspace, monkeypatch):
    monkeypatch.setattr(files, "MAX_READ_BYTES", 3)
    (workspace / "big.txt").write_text("1234")
    with pytest.raises(PermanentToolError, match="limit"):
        files.read_file("big.txt")


def test_write_file_creates_folders_and_overwrites(workspace):
    first = files.write_file("out/report.md", "v1")
    second = files.write_file("out/report.md", "v2")

    assert first == {"path": "out/report.md", "created": True, "bytes_written": 2}
    assert second["created"] is False
    assert (workspace / "out" / "report.md").read_text() == "v2"


@pytest.mark.parametrize("path", ["../escape.txt", "/etc/passwd", "a/../../escape.txt"])
def test_paths_outside_the_workspace_are_refused(workspace, path):
    with pytest.raises(PermanentToolError, match="outside the workspace"):
        files.read_file(path)
    with pytest.raises(PermanentToolError, match="outside the workspace"):
        files.write_file(path, "x")


def test_symlink_pointing_outside_is_refused(workspace, tmp_path_factory):
    outside = tmp_path_factory.mktemp("outside")
    (outside / "secret.txt").write_text("secret")
    (workspace / "link").symlink_to(outside)

    with pytest.raises(PermanentToolError, match="outside the workspace"):
        files.read_file("link/secret.txt")


def test_read_tools_run_in_the_process_worker(workspace, file_gateway):
    (workspace / "a.txt").write_text("hello")
    gateway = file_gateway()

    listed = gateway.execute("run", "list_folder", {})
    read = gateway.execute("run", "read_file", {"path": "a.txt"})

    assert listed.ok and listed.data["entries"] == [{"name": "a.txt", "type": "file", "size": 5}]
    assert read.ok and read.data["content"] == "hello"


def test_write_file_needs_write_permission_and_approval(workspace, file_gateway):
    denied = file_gateway().execute("run", "write_file", {"path": "a.txt", "content": "x"})
    approver = RecordingApprover(True)
    allowed = file_gateway(policy=PermissionPolicy(allow_write=True), approver=approver).execute(
        "run", "write_file", {"path": "a.txt", "content": "x"}
    )

    assert denied.error.type == "permission_denied"
    assert allowed.ok and len(approver.requests) == 1
    assert (workspace / "a.txt").read_text() == "x"
