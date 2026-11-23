"""The gateway shows a preview before asking, and skips asking when the call can't succeed."""

from agent_core.approvals import ApprovalRequest, TerminalApprovalProvider
from agent_core.permissions import PermissionPolicy, RiskLevel
from agent_core.tools.registry import ToolDefinition
from tests import tool_fixtures as fx
from tests.conftest import RecordingApprover

STR = {"type": "string"}


def preview_ok(text: str) -> str:
    return f"-old\n+{text}"


def preview_fails(text: str) -> str:
    from agent_core.errors import PermanentToolError
    raise PermanentToolError("old_text was not found")


def tool(preview):
    return ToolDefinition(name="save", description="Save.", function=fx.echo,
                          risk_level=RiskLevel.WRITE, parameters={"text": STR}, preview=preview)


def test_preview_is_passed_to_the_approver(make_gateway, audit):
    approver = RecordingApprover(True)
    gateway = make_gateway([tool(preview_ok)], policy=PermissionPolicy(allow_write=True), approver=approver)
    assert gateway.execute("run", "save", {"text": "new"}).ok
    assert approver.requests[0].preview == "-old\n+new"
    order = [t for t in audit.types() if t in ("tool.preview_ready", "tool.approval_requested")]
    assert order == ["tool.preview_ready", "tool.approval_requested"]


def test_failed_preview_means_nobody_is_asked(make_gateway, audit):
    approver = RecordingApprover(True)
    gateway = make_gateway([tool(preview_fails)], policy=PermissionPolicy(allow_write=True), approver=approver)
    result = gateway.execute("run", "save", {"text": "new"})
    assert result.error.type == "permanent_failure" and "not found" in result.error.message
    assert approver.requests == []
    assert "tool.authorization_issued" not in audit.types()


def test_terminal_prompt_shows_the_diff_and_shortens_long_text():
    shown = []
    approver = TerminalApprovalProvider(input_fn=lambda prompt: "y", output_fn=shown.append, color=False)
    request = ApprovalRequest("c", "write_file", {"path": "a.txt", "content": "x" * 500},
                              RiskLevel.WRITE, preview="--- a\n+++ b\n-old\n+new")
    assert approver.approve(request) is True
    text = "\n".join(shown)
    assert "<500 characters, 1 lines>" in text and "x" * 200 not in text
    assert "Preview:" in text and "  -old" in text and "  +new" in text


def test_hidden_characters_are_shown_in_the_prompt():
    shown = []
    approver = TerminalApprovalProvider(input_fn=lambda prompt: "n", output_fn=shown.append, color=False)
    request = ApprovalRequest("c", "run_command", {"command": "ls\rrm -rf ~"}, RiskLevel.DANGEROUS,
                              preview="+print('a')\x1b[1A\x1b[2K")
    approver.approve(request)
    text = "\n".join(shown)
    assert "\x1b" not in text and "\\x1b[2K" in text
    assert '"command": "ls\\rrm -rf ~"' in text
    assert "Arguments:  {\n" in text  # normal line breaks stay line breaks
