"""Structured, append-only audit trail (JSONL).

Every decision the gateway makes is recorded with a run_id (one user turn)
and a call_id (one tool call), so a session can be reconstructed afterwards.
Values under sensitive-looking keys are redacted before they are written.
"""

import json
import threading
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

SENSITIVE_KEY_PARTS = ("password", "secret", "token", "api_key", "apikey", "authorization")
REDACTED = "***REDACTED***"


@dataclass(frozen=True)
class AuditEvent:
    timestamp: str
    run_id: str
    event_type: str
    details: dict[str, Any]
    call_id: str | None = None


class AuditLogger(Protocol):
    def record(
        self,
        run_id: str,
        event_type: str,
        details: dict[str, Any],
        call_id: str | None = None,
    ) -> None:
        """Save one audit event."""
        ...


def redact(value: Any) -> Any:
    """Return a copy with values of secret-looking keys replaced by ***REDACTED***."""
    if isinstance(value, dict):
        return {
            key: REDACTED
            if isinstance(key, str)
            and any(part in key.lower() for part in SENSITIVE_KEY_PARTS)
            else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


def create_event(
    run_id: str,
    event_type: str,
    details: dict[str, Any],
    call_id: str | None = None,
) -> AuditEvent:
    """Build a timestamped audit event with secrets redacted."""
    return AuditEvent(
        timestamp=datetime.now(timezone.utc).isoformat(),
        run_id=run_id,
        event_type=event_type,
        details=redact(details),
        call_id=call_id,
    )


class JsonlAuditLogger:
    def __init__(self, path: str | Path) -> None:
        """Write events to this file, creating its folder if needed."""
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def record(
        self,
        run_id: str,
        event_type: str,
        details: dict[str, Any],
        call_id: str | None = None,
    ) -> None:
        """Append one event to the file as a JSON line."""
        event = create_event(run_id, event_type, details, call_id)
        line = json.dumps(asdict(event), default=str, ensure_ascii=False)
        with self._lock:
            with self.path.open("a", encoding="utf-8") as file:
                file.write(line + "\n")


class InMemoryAuditLogger:
    """Collects events in memory; used by tests and for inspection."""

    def __init__(self) -> None:
        """Start with an empty event list."""
        self.events: list[AuditEvent] = []

    def record(
        self,
        run_id: str,
        event_type: str,
        details: dict[str, Any],
        call_id: str | None = None,
    ) -> None:
        """Keep the event in memory."""
        self.events.append(create_event(run_id, event_type, details, call_id))

    def types(self) -> list[str]:
        """List the event types in order (handy in tests)."""
        return [event.event_type for event in self.events]


def new_id() -> str:
    """Make a new random unique id."""
    return str(uuid.uuid4())
