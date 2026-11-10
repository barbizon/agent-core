"""Pipeline ordering, especially approval-first authorization."""

from agent_core.permissions import PermissionPolicy, RiskLevel
from agent_core.retry import RetryPolicy
from agent_core.tools.registry import ExecutionMode, ToolDefinition
from tests import tool_fixtures as fx
from tests.conftest import RecordingApprover

STR = {"type": "string"}


def read_tool(**kw):
    spec = dict(name="echo", description="Echo.", function=fx.echo,
                risk_level=RiskLevel.READ, parameters={"text": STR})
    spec.update(kw)
    return ToolDefinition(**spec)


def write_tool(**kw):
    return read_tool(name="write", risk_level=RiskLevel.WRITE, **kw)


def test_read_tool_runs_without_approval(make_gateway, audit):
    approver = RecordingApprover(False)
    gateway = make_gateway([read_tool()], approver=approver)
    result = gateway.execute("run", "echo", {"text": "hi"})
    assert result.ok and result.data == {"echo": "hi"}
    assert approver.requests == []
    assert "tool.authorization_issued" not in audit.types()


def test_process_mode_read_tool(make_gateway):
    gateway = make_gateway([read_tool(execution_mode=ExecutionMode.PROCESS)])
    assert gateway.execute("run", "echo", {"text": "hi"}).data == {"echo": "hi"}


def test_policy_denial_happens_before_approval_and_issues_no_token(make_gateway, audit):
    approver = RecordingApprover(True)
    gateway = make_gateway([write_tool()], approver=approver)  # default policy: read-only
    result = gateway.execute("run", "write", {"text": "x"})
    assert result.error.type == "permission_denied"
    assert approver.requests == []
    assert gateway.authorization_store.pending_count == 0
    assert "tool.authorization_issued" not in audit.types()


def test_approval_denied_issues_no_token(make_gateway, audit):
    gateway = make_gateway([write_tool()], policy=PermissionPolicy(allow_write=True),
                           approver=RecordingApprover(False))
    result = gateway.execute("run", "write", {"text": "x"})
    assert result.error.type == "approval_denied"
    assert gateway.authorization_store.pending_count == 0
    assert "tool.authorization_issued" not in audit.types()
    assert "tool.execution_started" not in audit.types()


def test_approved_write_issues_token_after_approval_and_consumes_it(make_gateway, audit):
    approver = RecordingApprover(True)
    gateway = make_gateway([write_tool()], policy=PermissionPolicy(allow_write=True),
                           approver=approver)
    result = gateway.execute("run", "write", {"text": "x"})
    assert result.ok
    assert approver.requests[0].tool_name == "write"
    assert gateway.authorization_store.pending_count == 0  # consumed

    types = audit.types()
    order = ["tool.permission_checked", "tool.approval_requested", "tool.approval_granted",
             "tool.authorization_issued", "tool.execution_started", "tool.execution_succeeded"]
    assert [t for t in types if t in order] == order


def test_crashing_approver_fails_closed(make_gateway):
    class Broken:
        def approve(self, request):
            raise RuntimeError("tty gone")

    gateway = make_gateway([write_tool()], policy=PermissionPolicy(allow_write=True),
                           approver=Broken())
    assert gateway.execute("run", "write", {"text": "x"}).error.type == "approval_required"


def test_invalid_arguments_rejected_before_approval(make_gateway):
    approver = RecordingApprover(True)
    gateway = make_gateway([write_tool()], policy=PermissionPolicy(allow_write=True),
                           approver=approver)
    assert gateway.execute("run", "write", {}).error.type == "missing_argument"
    assert gateway.execute("run", "nope", {}).error.type == "unknown_tool"
    assert approver.requests == []


def test_read_tool_retries_transient_errors(make_gateway, audit):
    fx._calls["flaky"] = 0
    gateway = make_gateway([read_tool(name="flaky", function=fx.flaky,
                                      retry_policy=RetryPolicy(max_attempts=3, initial_delay_seconds=0))])
    result = gateway.execute("run", "flaky", {"text": "x"})
    assert result.ok and result.data == {"attempt": 3}
    assert audit.types().count("tool.attempt_retry_scheduled") == 2


def test_audit_events_share_run_and_call_ids(make_gateway, audit):
    make_gateway([read_tool()]).execute("run-1", "echo", {"text": "hi"})
    assert {e.run_id for e in audit.events} == {"run-1"}
    assert len({e.call_id for e in audit.events}) == 1
