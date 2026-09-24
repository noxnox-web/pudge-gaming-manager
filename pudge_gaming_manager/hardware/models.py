"""Value types for hardware scanning.

Availability is modelled explicitly. A value PGM could not read is
``UNAVAILABLE`` with a stated reason — never ``0``, never a guess. Rule #64
forbids inventing data, and a fabricated temperature is worse than a blank
one because an operator would act on it.

Thresholds carry their provenance (rule #8): a vendor-documented limit and
PGM's own heuristic must not look alike in the UI.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Generic, TypeVar


class HealthStatus(str, Enum):
    """How a measured value compares against its thresholds."""

    GOOD = "GOOD"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"
    UNAVAILABLE = "UNAVAILABLE"
    """PGM could not read this value. Not a fault in the PC."""

    @property
    def is_actionable(self) -> bool:
        return self in (HealthStatus.WARNING, HealthStatus.CRITICAL)


class ThresholdKind(str, Enum):
    """Where a threshold's authority comes from."""

    VENDOR = "VENDOR"
    """Documented by the hardware vendor or Microsoft. Cite the source."""

    HEURISTIC = "HEURISTIC"
    """PGM's own judgement. Labelled as such in the UI."""

    DERIVED = "DERIVED"
    """Computed from observed hardware, e.g. a percentage of installed RAM."""


T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class Reading(Generic[T]):
    """A single measured value, which may be unavailable.

    Args:
        value: The measurement, or ``None`` when unavailable.
        unit: Display unit, e.g. ``'°C'``, ``'GB'``, ``'Hz'``.
        source: Where it came from, e.g. ``'psutil'``, ``'nvidia-smi'``.
            Shown in diagnostics so an operator can judge its trustworthiness.
        unavailable_reason: Why there is no value. Required when ``value`` is
            ``None`` — "unknown" without a reason is not actionable.
    """

    value: T | None = None
    unit: str = ""
    source: str = ""
    unavailable_reason: str = ""

    @property
    def available(self) -> bool:
        return self.value is not None

    @classmethod
    def unavailable(cls, reason: str, *, unit: str = "") -> "Reading[T]":
        return cls(value=None, unit=unit, unavailable_reason=reason)

    def display(self, *, precision: int = 0) -> str:
        """Render for the UI. Unavailable values say so plainly."""
        if self.value is None:
            return "UNAVAILABLE"
        if isinstance(self.value, float):
            return f"{self.value:.{precision}f}{self.unit}"
        return f"{self.value}{self.unit}"


@dataclass(frozen=True, slots=True)
class Threshold:
    """A warning/critical boundary and where its authority comes from."""

    warning: float
    critical: float
    kind: ThresholdKind
    source: str = ""
    """Citation for VENDOR thresholds, rationale for HEURISTIC ones."""

    higher_is_worse: bool = True

    def evaluate(self, value: float | None) -> HealthStatus:
        if value is None:
            return HealthStatus.UNAVAILABLE
        if self.higher_is_worse:
            if value >= self.critical:
                return HealthStatus.CRITICAL
            if value >= self.warning:
                return HealthStatus.WARNING
        else:
            if value <= self.critical:
                return HealthStatus.CRITICAL
            if value <= self.warning:
                return HealthStatus.WARNING
        return HealthStatus.GOOD

    def describe(self) -> str:
        """Human-readable provenance, shown next to the value."""
        label = {
            ThresholdKind.VENDOR: "vendor limit",
            ThresholdKind.HEURISTIC: "heuristic",
            ThresholdKind.DERIVED: "derived",
        }[self.kind]
        return f"{label}: {self.source}" if self.source else label


@dataclass(frozen=True, slots=True)
class HealthItem:
    """One row on the Hardware Health page."""

    name: str
    reading: Reading[Any]
    status: HealthStatus
    threshold: Threshold | None = None
    detail: str = ""

    @property
    def is_actionable(self) -> bool:
        return self.status.is_actionable


# -- component records -----------------------------------------------------


@dataclass(frozen=True, slots=True)
class CpuInfo:
    manufacturer: str = ""
    model: str = ""
    physical_cores: Reading[int] = field(default_factory=Reading)
    logical_processors: Reading[int] = field(default_factory=Reading)
    base_clock_mhz: Reading[float] = field(default_factory=Reading)
    current_clock_mhz: Reading[float] = field(default_factory=Reading)
    utilization_percent: Reading[float] = field(default_factory=Reading)
    temperature_c: Reading[float] = field(default_factory=Reading)


@dataclass(frozen=True, slots=True)
class GpuInfo:
    """GPU facts and, on NVIDIA hardware, live telemetry.

    Identity comes from WMI. Everything measured comes from NVML, NVIDIA's
    documented read-only management library. On AMD and Intel GPUs the
    telemetry fields stay unavailable with that reason stated, because no
    equivalent documented interface ships with Windows.
    """

    vendor: str = ""
    model: str = ""
    driver_version: str = ""
    driver_date: str = ""
    vram_total_mb: Reading[int] = field(default_factory=Reading)
    temperature_c: Reading[float] = field(default_factory=Reading)
    utilization_percent: Reading[float] = field(default_factory=Reading)
    vram_used_mb: Reading[int] = field(default_factory=Reading)
    vram_used_percent: Reading[float] = field(default_factory=Reading)
    fan_percent: Reading[float] = field(default_factory=Reading)
    power_watts: Reading[float] = field(default_factory=Reading)
    power_limit_watts: Reading[float] = field(default_factory=Reading)
    clock_sm_mhz: Reading[int] = field(default_factory=Reading)
    max_clock_sm_mhz: Reading[int] = field(default_factory=Reading)
    performance_state: Reading[int] = field(default_factory=Reading)

    throttle_reasons: tuple[str, ...] = ()
    """Why the driver is currently limiting clocks, per NVML."""

    thermal_throttling: bool = False
    power_throttling: bool = False

    temperature_threshold: "Threshold | None" = None
    """The driver's own limits, so ``ThresholdKind.VENDOR`` (rule #8)."""

    @property
    def is_nvidia(self) -> bool:
        return "nvidia" in self.vendor.lower() or "nvidia" in self.model.lower()

    @property
    def temperature_status(self) -> HealthStatus:
        """Grade temperature against the driver's documented limits."""
        if self.temperature_threshold is None:
            return HealthStatus.UNAVAILABLE
        return self.temperature_threshold.evaluate(self.temperature_c.value)


@dataclass(frozen=True, slots=True)
class RamModule:
    manufacturer: str = ""
    capacity_mb: int = 0
    speed_mhz: int = 0
    part_number: str = ""


@dataclass(frozen=True, slots=True)
class RamInfo:
    total_mb: Reading[int] = field(default_factory=Reading)
    available_mb: Reading[int] = field(default_factory=Reading)
    used_mb: Reading[int] = field(default_factory=Reading)
    usage_percent: Reading[float] = field(default_factory=Reading)
    speed_mhz: Reading[int] = field(default_factory=Reading)
    modules: tuple[RamModule, ...] = ()

    @property
    def module_count(self) -> int:
        return len(self.modules)


@dataclass(frozen=True, slots=True)
class DiskInfo:
    device_id: str = ""
    model: str = ""
    media_type: str = ""
    """'SSD', 'HDD', or '' when Windows does not report it."""

    bus_type: str = ""
    total_gb: Reading[float] = field(default_factory=Reading)
    free_gb: Reading[float] = field(default_factory=Reading)
    free_percent: Reading[float] = field(default_factory=Reading)
    filesystem: str = ""
    smart_healthy: Reading[bool] = field(default_factory=Reading)
    temperature_c: Reading[float] = field(default_factory=Reading)
    is_system_disk: bool = False


@dataclass(frozen=True, slots=True)
class DisplayMode:
    width: int
    height: int
    refresh_hz: int

    def __str__(self) -> str:
        return f"{self.width}x{self.height} @ {self.refresh_hz} Hz"


@dataclass(frozen=True, slots=True)
class MonitorInfo:
    device_name: str = ""
    friendly_name: str = ""
    is_primary: bool = False
    current_mode: DisplayMode | None = None
    max_refresh_hz: Reading[int] = field(default_factory=Reading)
    max_refresh_at_current_resolution: Reading[int] = field(default_factory=Reading)
    supported_modes: tuple[DisplayMode, ...] = ()
    hdr_enabled: Reading[bool] = field(default_factory=Reading)
    vrr_enabled: Reading[bool] = field(default_factory=Reading)

    @property
    def is_running_below_capability(self) -> bool:
        """True when a higher refresh rate is available at this resolution.

        Compared at the *current resolution* deliberately: a monitor that can
        do 240 Hz at 1080p but is running 1440p at 144 Hz is configured
        correctly, and flagging it would be a false alarm.
        """
        best = self.max_refresh_at_current_resolution.value
        if self.current_mode is None or best is None:
            return False
        return best > self.current_mode.refresh_hz


@dataclass(frozen=True, slots=True)
class OsInfo:
    caption: str = ""
    version: str = ""
    build: str = ""
    architecture: str = ""
    machine_name: str = ""
    is_windows_11: bool = False


@dataclass(frozen=True, slots=True)
class HardwareSnapshot:
    """Everything one hardware scan produced."""

    captured_at: str
    os: OsInfo
    cpu: CpuInfo
    gpus: tuple[GpuInfo, ...]
    ram: RamInfo
    disks: tuple[DiskInfo, ...]
    monitors: tuple[MonitorInfo, ...]
    scan_duration_s: float = 0.0
    warnings: tuple[str, ...] = ()
    """Non-fatal problems during the scan, e.g. a probe that timed out."""

    @property
    def primary_gpu(self) -> GpuInfo | None:
        for gpu in self.gpus:
            if gpu.is_nvidia:
                return gpu
        return self.gpus[0] if self.gpus else None

    @property
    def system_disk(self) -> DiskInfo | None:
        for disk in self.disks:
            if disk.is_system_disk:
                return disk
        return self.disks[0] if self.disks else None

    @property
    def primary_monitor(self) -> MonitorInfo | None:
        for monitor in self.monitors:
            if monitor.is_primary:
                return monitor
        return self.monitors[0] if self.monitors else None
