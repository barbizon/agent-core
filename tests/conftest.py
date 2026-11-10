from typing import Any

import pytest

from agent_core.approvals import ApprovalRequest
from agent_core.audit import InMemoryAuditLogger
from agent_core.execution.process_executor import ProcessExecutor
from agent_core.gateway import ToolGateway
from agent_core.llm import ModelResponse
from agent_core.permissions import PermissionPolicy
from agent_core.tools.registry import ToolRegistry


class RecordingApprover:
    def __init__(self, answer: bool) -> None:
        self.answer = answer
        self.requests: list[ApprovalRequest] = []

    def approve(self, request: ApprovalRequest) -> bool:
        self.requests.append(request)
        return self.answer


class ScriptedModel:
    """Test double: replays a fixed list of responses and records what it was sent."""

    def __init__(self, responses: list[ModelResponse]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[list[dict[str, Any]], list[dict[str, Any]]]] = []

    def complete(self, messages, tools) -> ModelResponse:
        self.calls.append(([dict(m) for m in messages], tools))
        return self.responses.pop(0)


@pytest.fixture
def audit() -> InMemoryAuditLogger:
    return InMemoryAuditLogger()


@pytest.fixture
def make_gateway(audit):
    def factory(tools, policy=None, approver=None) -> ToolGateway:
        return ToolGateway(
            registry=ToolRegistry(tools),
            permission_policy=policy or PermissionPolicy(),
            approval_provider=approver or RecordingApprover(False),
            audit_logger=audit,
            process_executor=ProcessExecutor(allowed_module_prefixes=("tests.",)),
        )
    return factory
