"""The finding type every diagnostic module produces."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from ...hardware.models import HealthStatus, ThresholdKind


class Severity(str, Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


@dataclass(frozen=True, slots=True)
class Issue:
    """One finding an administrator can act on."""

    id: str
    title: str
    detail: str
    severity: Severity
    subsystem: str
    fixable: bool
    """True when PGM ships a tweak that addresses this."""

    fix_hint: str = ""
    threshold_kind: ThresholdKind | None = None
    """Set when the finding came from a threshold, so the UI can show
    whether the limit is vendor-documented or a PGM heuristic (rule #8)."""

    @property
    def status(self) -> HealthStatus:
        return {
            Severity.INFO: HealthStatus.GOOD,
            Severity.WARNING: HealthStatus.WARNING,
            Severity.CRITICAL: HealthStatus.CRITICAL,
        }[self.severity]
