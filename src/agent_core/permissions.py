"""Risk levels and the static permission policy.

The policy is the first gate: it decides which *classes* of action are
allowed at all in this session, before any human is asked.
"""

from dataclasses import dataclass
from enum import StrEnum


class RiskLevel(StrEnum):
    READ = "read"
    WRITE = "write"
    DANGEROUS = "dangerous"


@dataclass(frozen=True)
class PermissionPolicy:
    allow_read: bool = True
    allow_write: bool = False
    allow_dangerous: bool = False

    def allows(self, risk_level: RiskLevel) -> bool:
        """Return True if this risk level is allowed in this session."""
        if risk_level == RiskLevel.READ:
            return self.allow_read
        if risk_level == RiskLevel.WRITE:
            return self.allow_write
        if risk_level == RiskLevel.DANGEROUS:
            return self.allow_dangerous
        return False  # unknown levels are denied by default
