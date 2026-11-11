"""Run a tool in an isolated subprocess that is killed on timeout.

The worker is started in its own session (process group), so on timeout we
SIGTERM then SIGKILL the whole group, including grandchildren the tool spawned.
"""

import json
import os
import signal
import subprocess
import sys
from typing import Any

from agent_core.errors import ToolExecutionError

WORKER_MODULE = "agent_core.execution.process_worker"


class ProcessExecutor:
    def __init__(
        self,
        allowed_module_prefixes: tuple[str, ...],
        termination_grace_seconds: float = 0.5,
    ) -> None:
        """Set which modules tools may come from and how long to wait when killing."""
        if not allowed_module_prefixes:
            raise ValueError("At least one allowed module prefix is required")
        if termination_grace_seconds <= 0:
            raise ValueError("termination_grace_seconds must be positive")
        self.allowed_module_prefixes = allowed_module_prefixes
        self.termination_grace_seconds = termination_grace_seconds

    def execute(
        self,
        function_ref: str,
        arguments: dict[str, Any],
        timeout_seconds: float,
    ) -> Any:
        """Return the tool's data, or raise ToolExecutionError."""
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")

        try:
            request = json.dumps({"function": function_ref, "arguments": arguments})
        except (TypeError, ValueError) as exc:
            raise ToolExecutionError(
                "Tool arguments are not JSON serializable",
                error_type="invalid_request",
            ) from exc

        command = [sys.executable, "-m", WORKER_MODULE]
        for prefix in self.allowed_module_prefixes:
            command += ["--allow-prefix", prefix]

        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                start_new_session=True,
            )
        except OSError as exc:
            raise ToolExecutionError(str(exc), error_type="worker_start_failed") from exc

        try:
            stdout, _stderr = process.communicate(input=request, timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            self._terminate(process)
            raise ToolExecutionError(
                f"Tool exceeded the {timeout_seconds}s execution deadline",
                error_type="timeout",
            )

        if process.returncode != 0:
            raise ToolExecutionError(
                f"Worker exited with code {process.returncode}",
                error_type="worker_crashed",
            )

        return self._parse_response(stdout)

    def _terminate(self, process: subprocess.Popen[str]) -> None:
        # SIGTERM first for a graceful exit, then always SIGKILL the group:
        # grandchildren may outlive the direct child.
        """Kill the worker and everything it started (SIGTERM, then SIGKILL)."""
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(process.pid, sig)
            except (ProcessLookupError, PermissionError):
                # Gone already. macOS raises PermissionError when every process
                # left in the group is a zombie (exited, not yet collected).
                pass
            try:
                process.wait(timeout=self.termination_grace_seconds)
            except subprocess.TimeoutExpired:
                pass
        try:
            process.communicate(timeout=self.termination_grace_seconds)
        except subprocess.TimeoutExpired:
            raise ToolExecutionError(
                "Worker termination could not be confirmed",
                error_type="termination_unconfirmed",
            )

    @staticmethod
    def _parse_response(stdout: str) -> Any:
        """Turn the worker's JSON reply into data, or raise its error."""
        try:
            response = json.loads(stdout)
        except json.JSONDecodeError as exc:
            raise ToolExecutionError(
                "Worker returned invalid JSON",
                error_type="invalid_worker_response",
            ) from exc

        if not isinstance(response, dict):
            raise ToolExecutionError(
                "Worker response must be an object",
                error_type="invalid_worker_response",
            )

        if response.get("ok") is True:
            return response.get("data")

        error = response.get("error")
        if (
            not isinstance(error, dict)
            or not isinstance(error.get("type"), str)
            or not isinstance(error.get("message"), str)
        ):
            raise ToolExecutionError(
                "Worker returned malformed error details",
                error_type="invalid_worker_response",
            )

        raise ToolExecutionError(error["message"], error_type=error["type"])
