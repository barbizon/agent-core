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


@pytest.fixture(autouse=True)
def main_config(tmp_path, monkeypatch):
    """Point the main settings (~/.orwick/config.json) at an empty temp place, so tests never
    read the real one. Tests that need main settings write to the returned path."""
    from agent_core import cli, config_view

    path = tmp_path / "home" / ".orwick" / "config.json"
    monkeypatch.setattr(cli, "USER_CONFIG_FILE", path)
    monkeypatch.setattr(cli, "USER_TOOLS_DIR", tmp_path / "home" / ".orwick" / "tools")
    monkeypatch.setattr(config_view, "USER_CONFIG_FILE", path)
    return path



@pytest.fixture(autouse=True)
def no_lock_wait(monkeypatch):
    """Busy sessions fail at once in tests (the real wait is a few seconds)."""
    from agent_core import sessions
    monkeypatch.setattr(sessions, "LOCK_WAIT_SECONDS", 0)


@pytest.fixture(autouse=True)
def keep_signal_handlers():
    """cli.main installs SIGHUP/SIGTERM handlers; put the old ones back after each test."""
    import signal

    saved = {sig: signal.getsignal(sig) for sig in (signal.SIGHUP, signal.SIGTERM)}
    yield
    for sig, handler in saved.items():
        signal.signal(sig, handler)


def hold_lock(path):
    """Hold a session's lock the way another running Orwick would (returns the descriptor)."""
    import fcntl
    import os

    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    os.write(fd, b"4242")
    return fd


@pytest.fixture(autouse=True)
def tool_lines_on():
    """cli.TRACE is shared; every test starts with tool lines shown."""
    from agent_core import cli
    cli.TRACE["on"] = True
    yield
    cli.TRACE["on"] = True


@pytest.fixture(autouse=True)
def no_claude_skills(tmp_path, monkeypatch):
    """Tests never see your own skills or review analytics (~/.claude/skills, ~/.orwick)."""
    from agent_core import skills
    from agent_core.tools import analytics
    monkeypatch.setattr(skills, "CLAUDE_SKILLS_DIR", tmp_path / "no-claude-skills")
    monkeypatch.setattr(skills, "USER_SKILLS_DIR", tmp_path / "no-skills-of-yours")
    monkeypatch.setenv(analytics.ANALYTICS_DIR_ENV, str(tmp_path / "no-analytics-of-yours"))
