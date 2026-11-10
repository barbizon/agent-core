import json

import pytest

from agent_core.approvals import ApprovalRequest, DenyAllApprovalProvider, TerminalApprovalProvider
from agent_core.audit import REDACTED, JsonlAuditLogger, redact
from agent_core.authorization import ActionAuthorizationStore
from agent_core.errors import ActionAuthorizationError, ToolExecutionError
from agent_core.permissions import PermissionPolicy, RiskLevel
from agent_core.results import ToolResult
from agent_core.retry import RetryPolicy, execute_with_retry


def test_permission_policy_defaults_to_read_only():
    policy = PermissionPolicy()
    assert policy.allows(RiskLevel.READ)
    assert not policy.allows(RiskLevel.WRITE)
    assert not policy.allows(RiskLevel.DANGEROUS)
    assert not policy.allows("unknown")


def test_authorization_is_single_use_and_bound_to_action():
    store = ActionAuthorizationStore()
    auth = store.issue("t", {"a": 1, "b": 2})
    store.consume(auth, "t", {"b": 2, "a": 1})  # key order irrelevant
    with pytest.raises(ActionAuthorizationError):
        store.consume(auth, "t", {"a": 1, "b": 2})  # replay

    auth = store.issue("t", {"a": 1})
    with pytest.raises(ActionAuthorizationError):
        store.consume(auth, "t", {"a": 2})  # different action
    with pytest.raises(ActionAuthorizationError):
        store.consume(None, "t", {"a": 1})


def test_retry_only_transient_errors():
    attempts = []

    def flaky():
        attempts.append(1)
        if len(attempts) < 3:
            raise ToolExecutionError("busy", "temporary_unavailable")
        return "ok"

    result = execute_with_retry(flaky, RetryPolicy(max_attempts=3), sleep=lambda s: None)
    assert (result.data, result.attempts) == ("ok", 3)

    def permanent():
        raise ToolExecutionError("bad", "permanent_failure")

    calls = []
    with pytest.raises(ToolExecutionError):
        execute_with_retry(permanent, RetryPolicy(max_attempts=3),
                           on_attempt=lambda *a: calls.append(a), sleep=lambda s: None)
    assert [c[1] for c in calls] == ["started", "failed"]


def test_audit_redacts_secrets_and_writes_jsonl(tmp_path):
    assert redact({"api_key": "x", "nested": [{"Password": "y", "ok": 1}]}) == {
        "api_key": REDACTED, "nested": [{"Password": REDACTED, "ok": 1}]}
    logger = JsonlAuditLogger(tmp_path / "a" / "audit.jsonl")
    logger.record("run", "evt", {"token": "s"}, call_id="c")
    line = json.loads((tmp_path / "a" / "audit.jsonl").read_text())
    assert line["details"] == {"token": REDACTED}
    assert line["call_id"] == "c"


def test_tool_result_is_truncated():
    content = ToolResult.success("x" * 1000).to_model_content(max_chars=120)
    assert len(content) == 120 and content.endswith("[truncated]")


def test_approvers_fail_closed():
    request = ApprovalRequest("c", "t", {}, RiskLevel.WRITE)
    assert DenyAllApprovalProvider().approve(request) is False
    assert TerminalApprovalProvider(input_fn=lambda p: "y", output_fn=lambda s: None).approve(request)
    assert not TerminalApprovalProvider(input_fn=lambda p: "yes please", output_fn=lambda s: None).approve(request)

    def eof(prompt):
        raise EOFError

    assert not TerminalApprovalProvider(input_fn=eof, output_fn=lambda s: None).approve(request)
