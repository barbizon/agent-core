"""ToolGateway: the only path from a model's tool request to execution.

Pipeline for every call (each step is audited):

    1. lookup        unknown tool                 -> unknown_tool
    2. validate      arguments vs. schema         -> missing/unexpected/invalid_*
    3. permission    PermissionPolicy(risk)       -> permission_denied
    4. approval      human, non-read only         -> approval_required / approval_denied
    5. authorize     issue one-time token, ONLY after 3 and 4 succeed
    6. execute       consume token (non-read), then run with timeout + retry

Approval-first ordering: no authorization token exists for an action until
it has passed the policy *and* a human has approved it. A denied or
unapproved call leaves nothing behind in the authorization store.
"""

import time
from typing import Any

from agent_core.approvals import ApprovalProvider, ApprovalRequest
from agent_core.audit import AuditLogger, new_id
from agent_core.debug import Tracer
from agent_core.authorization import ActionAuthorization, ActionAuthorizationStore
from agent_core.errors import ActionAuthorizationError, ToolExecutionError
from agent_core.execution.process_executor import ProcessExecutor
from agent_core.execution.thread_executor import run_in_thread
from agent_core.permissions import PermissionPolicy, RiskLevel
from agent_core.results import ToolResult
from agent_core.retry import execute_with_retry
from agent_core.tools.registry import ExecutionMode, ToolDefinition, ToolRegistry


class ToolGateway:
    def __init__(
        self,
        registry: ToolRegistry,
        permission_policy: PermissionPolicy,
        approval_provider: ApprovalProvider,
        audit_logger: AuditLogger,
        process_executor: ProcessExecutor | None = None,
        authorization_store: ActionAuthorizationStore | None = None,
        tracer: Tracer | None = None,
    ) -> None:
        """Set up the gateway with the tools, rules, approver and audit log."""
        self.registry = registry
        self.permission_policy = permission_policy
        self.approval_provider = approval_provider
        self.audit_logger = audit_logger
        self.process_executor = process_executor
        self.authorization_store = authorization_store or ActionAuthorizationStore()
        self.tracer = tracer or Tracer()  # disabled unless debug mode is on

    def execute(self, run_id: str, tool_name: str, arguments: dict[str, Any]) -> ToolResult:
        """Run one tool call through all safety checks and return the result."""
        # In debug mode, every step below is collected and shown as one framed point.
        with self.tracer.collect_checks(f"Gateway · safety checks for '{tool_name}'"):
            return self._execute(run_id, tool_name, arguments)

    def _execute(self, run_id: str, tool_name: str, arguments: dict[str, Any]) -> ToolResult:
        """The pipeline itself: lookup, validate, permission, approval, token, run."""
        call_id = new_id()

        def audit(event_type: str, **details: Any) -> None:
            """Record an audit event for this call (and show it in debug mode)."""
            self.audit_logger.record(run_id, event_type, {"tool": tool_name, **details}, call_id)
            self.tracer.event(event_type, {"tool": tool_name, **details})

        audit("tool.requested", arguments=arguments)

        # 1-2. lookup + argument validation
        try:
            tool = self.registry.validate_arguments(tool_name, arguments)
        except ToolExecutionError as exc:
            audit("tool.rejected", error_type=exc.error_type, error=str(exc))
            return ToolResult.failure(exc.error_type, str(exc))

        # 3. static permission policy
        permitted = self.permission_policy.allows(tool.risk_level)
        audit("tool.permission_checked", risk_level=tool.risk_level, permitted=permitted)
        if not permitted:
            return ToolResult.failure(
                "permission_denied",
                f"Risk level '{tool.risk_level}' is not permitted in this session",
            )

        # 4-5. human approval, then (and only then) a one-time authorization
        authorization: ActionAuthorization | None = None
        if tool.risk_level != RiskLevel.READ:
            audit("tool.approval_requested", arguments=arguments)
            try:
                approved = self.approval_provider.approve(
                    ApprovalRequest(
                        call_id=call_id,
                        tool_name=tool_name,
                        arguments=dict(arguments),
                        risk_level=tool.risk_level,
                    )
                )
            except Exception as exc:  # a broken approver must fail closed
                audit("tool.approval_failed", error=str(exc))
                return ToolResult.failure("approval_required", "Approval could not be obtained")

            if not approved:
                audit("tool.approval_denied")
                return ToolResult.failure("approval_denied", "Human did not approve the action")

            audit("tool.approval_granted")
            try:
                authorization = self.authorization_store.issue(tool_name, arguments)
            except ActionAuthorizationError as exc:
                audit("tool.authorization_failed", error=str(exc))
                return ToolResult.failure("authorization_failed", str(exc))
            audit("tool.authorization_issued")

        # 6. execution
        return self._run(tool, arguments, authorization, audit)

    def _run(
        self,
        tool: ToolDefinition,
        arguments: dict[str, Any],
        authorization: ActionAuthorization | None,
        audit: Any,
    ) -> ToolResult:
        """Execute an approved call with its timeout and retry policy."""
        def operation() -> Any:
            """One attempt: use up the token if needed, then run the tool."""
            if tool.risk_level != RiskLevel.READ:
                try:
                    self.authorization_store.consume(authorization, tool.name, arguments)
                except ActionAuthorizationError as exc:
                    raise ToolExecutionError(str(exc), error_type="authorization_failed") from exc

            if tool.execution_mode == ExecutionMode.PROCESS:
                if self.process_executor is None:
                    raise ToolExecutionError(
                        "Process executor is not configured",
                        error_type="executor_unavailable",
                    )
                return self.process_executor.execute(
                    tool.function_ref, arguments, tool.timeout_seconds
                )

            return run_in_thread(tool.function, arguments, tool.timeout_seconds, tool.name)

        def on_attempt(attempt: int, status: str, details: dict[str, Any]) -> None:
            """Record each attempt in the audit log."""
            audit(f"tool.attempt_{status}", attempt=attempt, **details)

        audit("tool.execution_started", mode=tool.execution_mode)
        start = time.monotonic()
        try:
            result = execute_with_retry(operation, tool.retry_policy, on_attempt)
        except ToolExecutionError as exc:
            audit(
                "tool.execution_failed",
                error_type=exc.error_type,
                error=str(exc),
                duration_ms=_elapsed_ms(start),
            )
            return ToolResult.failure(exc.error_type, str(exc))

        audit("tool.execution_succeeded", attempts=result.attempts, duration_ms=_elapsed_ms(start))
        return ToolResult.success(result.data)


def _elapsed_ms(start: float) -> float:
    """Milliseconds passed since start."""
    return round((time.monotonic() - start) * 1000, 2)
