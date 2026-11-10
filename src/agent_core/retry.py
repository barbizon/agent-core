"""Bounded retry with exponential backoff.

Only errors classified as transient are retried, and the tool registry
forbids retries for any non-read tool (a retried write could apply twice).
"""

import time
from dataclasses import dataclass
from typing import Any, Callable

from agent_core.errors import ToolExecutionError

RETRYABLE_ERROR_TYPES = frozenset({"temporary_unavailable", "rate_limited"})

AttemptCallback = Callable[[int, str, dict[str, Any]], None]


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 1
    initial_delay_seconds: float = 0.2
    backoff_multiplier: float = 2.0

    def __post_init__(self) -> None:
        """Reject invalid retry settings."""
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        if self.initial_delay_seconds < 0:
            raise ValueError("initial_delay_seconds cannot be negative")
        if self.backoff_multiplier < 1:
            raise ValueError("backoff_multiplier must be >= 1")

    def delay_for(self, attempt: int) -> float:
        """How long to wait after the given attempt (doubles each time by default)."""
        return self.initial_delay_seconds * self.backoff_multiplier ** (attempt - 1)


@dataclass(frozen=True)
class RetryResult:
    data: Any
    attempts: int


def execute_with_retry(
    operation: Callable[[], Any],
    policy: RetryPolicy,
    on_attempt: AttemptCallback | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> RetryResult:
    """Run the operation, retrying temporary errors up to the policy limit."""
    def notify(attempt: int, status: str, details: dict[str, Any]) -> None:
        """Report attempt progress, if someone is listening."""
        if on_attempt is not None:
            on_attempt(attempt, status, details)

    for attempt in range(1, policy.max_attempts + 1):
        notify(attempt, "started", {})
        start = time.monotonic()
        try:
            data = operation()
        except ToolExecutionError as exc:
            notify(attempt, "failed", {
                "duration_ms": _elapsed_ms(start),
                "error_type": exc.error_type,
                "error": str(exc),
            })
            if exc.error_type not in RETRYABLE_ERROR_TYPES or attempt >= policy.max_attempts:
                raise
            delay = policy.delay_for(attempt)
            notify(attempt, "retry_scheduled", {"delay_seconds": delay})
            sleep(delay)
        else:
            notify(attempt, "succeeded", {"duration_ms": _elapsed_ms(start)})
            return RetryResult(data=data, attempts=attempt)

    raise AssertionError("unreachable")


def _elapsed_ms(start: float) -> float:
    """Milliseconds passed since start."""
    return round((time.monotonic() - start) * 1000, 2)
