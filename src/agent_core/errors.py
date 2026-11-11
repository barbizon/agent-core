"""Exception hierarchy.

Every tool-related failure carries a stable, machine-readable ``error_type``
so the runtime can decide (for example) whether it is safe to retry, and so
the model receives a structured error rather than a stack trace.
"""


class ConfigError(Exception):
    """Invalid or missing configuration."""


class ToolManifestError(Exception):
    """A tool.json file is invalid; the agent refuses to start with it."""


class ModelError(Exception):
    """The model backend failed (auth, network, timeout, unparseable reply...)."""


# --- Raised by tool implementations ---------------------------------------

class ToolOperationError(Exception):
    """A known failure reported by a tool implementation."""

    def __init__(self, message: str, error_type: str) -> None:
        """Store the message and its error type."""
        super().__init__(message)
        self.error_type = error_type


class TemporaryToolError(ToolOperationError):
    def __init__(self, message: str) -> None:
        """A short-lived failure; safe to retry."""
        super().__init__(message, error_type="temporary_unavailable")


class RateLimitToolError(ToolOperationError):
    def __init__(self, message: str) -> None:
        """The service said 'too many requests'; safe to retry."""
        super().__init__(message, error_type="rate_limited")


class PermanentToolError(ToolOperationError):
    def __init__(self, message: str) -> None:
        """A failure that retrying won't fix."""
        super().__init__(message, error_type="permanent_failure")


# --- Raised by the runtime ------------------------------------------------

class ToolExecutionError(Exception):
    """The runtime failed to execute a tool (timeout, crash, bad args...)."""

    def __init__(self, message: str, error_type: str = "execution_error") -> None:
        """Store the message and its error type."""
        super().__init__(message)
        self.error_type = error_type


class ActionAuthorizationError(Exception):
    """A non-read action was attempted without a valid one-time authorization."""


class StepLimitExceeded(Exception):
    """The agent used all of its steps without producing a final answer."""
