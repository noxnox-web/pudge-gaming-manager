"""Public vocabulary that ``core`` presents to the layers above it.

The GUI must render hardware readings, findings and scores, but it may not
depend on the subsystems that produce them — otherwise a change to a probe
reaches straight into a widget. ``core`` therefore re-exports the value types
that cross the boundary, and ``app`` imports them from here.

These are pure data types with no behaviour and no I/O. Re-exporting them is
a facade, not a layering loophole: the direction of dependency is still
app -> core, and the subsystems stay replaceable behind it.
"""

from __future__ import annotations

from ..hardware.models import (
    CpuInfo,
    DiskInfo,
    DisplayMode,
    GpuInfo,
    HardwareSnapshot,
    HealthItem,
    HealthStatus,
    MonitorInfo,
    OsInfo,
    RamInfo,
    Reading,
    Threshold,
    ThresholdKind,
)
from ..network.models import AdapterInfo, LinkKind, NetworkSnapshot, PingStats
from .diagnostics.model import Issue, Severity
from .optimization.tweak import Outcome, RiskLevel
from .scoring.score import Component, GamingScore

__all__ = [
    "CpuInfo",
    "DiskInfo",
    "DisplayMode",
    "GpuInfo",
    "HardwareSnapshot",
    "HealthItem",
    "HealthStatus",
    "MonitorInfo",
    "OsInfo",
    "RamInfo",
    "Reading",
    "Threshold",
    "ThresholdKind",
    "AdapterInfo",
    "LinkKind",
    "NetworkSnapshot",
    "PingStats",
    "Issue",
    "Severity",
    "Outcome",
    "RiskLevel",
    "Component",
    "GamingScore",
]
