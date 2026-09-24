"""NVIDIA GPU telemetry via NVML.

NVML is NVIDIA's documented, supported management library — the same one
``nvidia-smi`` is built on. It is read-only, which is exactly the scope PGM
wants: it answers "how is this GPU doing right now?" without touching any
driver setting.

Why this is worth having
------------------------
NVML reports the driver's **own** temperature thresholds
(``GPU_MAX``, ``SLOWDOWN``, ``SHUTDOWN``) and its enforced power limit. Those
are vendor-documented values, so GPU thermal health is graded against what
NVIDIA says the card tolerates rather than a number PGM invented (rule #8).
It also reports *why* clocks are reduced, which turns "Thermal Throttling"
and "Power Throttling" from guesses into facts.

Resilience
----------
Every individual metric is guarded. NVML raises ``NVMLError_NotSupported``
per-metric on some cards (fan speed on passively cooled boards, power on
older ones), and a missing fan reading must not cost us the temperature.
A driver/library mismatch disables the whole module cleanly rather than
crashing a scan.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Callable, TypeVar

from ...utilities.logging_setup import get_logger
from ..models import Reading, Threshold, ThresholdKind

_log = get_logger(__name__)

try:  # pragma: no cover - import-time capability probe
    import pynvml

    _NVML_IMPORTED = True
except ImportError:  # pragma: no cover
    pynvml = None  # type: ignore[assignment]
    _NVML_IMPORTED = False

_T = TypeVar("_T")

_UNSUPPORTED = "This GPU or driver does not report the value."
_NO_NVML = "NVIDIA management library is not available on this system."

#: Throttle-reason bit -> plain language. Names per the NVML documentation.
#: ``GpuIdle`` is deliberately excluded from "is throttling" logic: an idle
#: GPU has reduced clocks because there is nothing to do, which is correct
#: behaviour and not a fault.
_THROTTLE_LABELS: list[tuple[str, str]] = [
    ("nvmlClocksThrottleReasonGpuIdle", "idle"),
    ("nvmlClocksThrottleReasonApplicationsClocksSetting", "application clock limit"),
    ("nvmlClocksThrottleReasonSwPowerCap", "software power cap"),
    ("nvmlClocksThrottleReasonHwSlowdown", "hardware slowdown"),
    ("nvmlClocksThrottleReasonSyncBoost", "sync boost"),
    ("nvmlClocksThrottleReasonSwThermalSlowdown", "software thermal slowdown"),
    ("nvmlClocksThrottleReasonHwThermalSlowdown", "hardware thermal slowdown"),
    ("nvmlClocksThrottleReasonHwPowerBrakeSlowdown", "hardware power brake"),
    ("nvmlClocksThrottleReasonDisplayClockSetting", "display clock limit"),
]

_THERMAL_REASONS = {"software thermal slowdown", "hardware thermal slowdown"}
_POWER_REASONS = {"software power cap", "hardware power brake"}


@dataclass(frozen=True, slots=True)
class GpuTelemetry:
    """A live snapshot of one NVIDIA GPU."""

    index: int
    name: str = ""
    temperature_c: Reading[float] = field(default_factory=Reading)
    utilization_percent: Reading[float] = field(default_factory=Reading)
    memory_utilization_percent: Reading[float] = field(default_factory=Reading)
    vram_total_mb: Reading[int] = field(default_factory=Reading)
    vram_used_mb: Reading[int] = field(default_factory=Reading)
    fan_percent: Reading[float] = field(default_factory=Reading)
    power_watts: Reading[float] = field(default_factory=Reading)
    power_limit_watts: Reading[float] = field(default_factory=Reading)
    clock_sm_mhz: Reading[int] = field(default_factory=Reading)
    clock_mem_mhz: Reading[int] = field(default_factory=Reading)
    max_clock_sm_mhz: Reading[int] = field(default_factory=Reading)
    performance_state: Reading[int] = field(default_factory=Reading)
    throttle_reasons: tuple[str, ...] = ()
    temperature_threshold: Threshold | None = None
    """Built from the driver's own limits, so ``ThresholdKind.VENDOR``."""

    @property
    def vram_used_percent(self) -> Reading[float]:
        total, used = self.vram_total_mb.value, self.vram_used_mb.value
        if not total or used is None:
            return Reading.unavailable("VRAM totals not reported.", unit="%")
        return Reading(used / total * 100.0, unit="%", source="NVML")

    @property
    def is_thermally_throttling(self) -> bool:
        return bool(_THERMAL_REASONS.intersection(self.throttle_reasons))

    @property
    def is_power_throttling(self) -> bool:
        return bool(_POWER_REASONS.intersection(self.throttle_reasons))

    @property
    def is_idle(self) -> bool:
        return "idle" in self.throttle_reasons


def _safe(call: Callable[[], _T], label: str) -> tuple[_T | None, str]:
    """Run one NVML query, converting any failure into an unavailability.

    Returns ``(value, reason)``; ``reason`` is empty on success.
    """
    try:
        return call(), ""
    except Exception as exc:  # noqa: BLE001 - NVML raises many distinct types
        name = type(exc).__name__
        if "NotSupported" in name:
            return None, _UNSUPPORTED
        _log.debug("NVML %s failed: %s", label, exc)
        return None, f"NVML could not read this value ({name})."


def _reading(
    call: Callable[[], Any],
    label: str,
    *,
    unit: str = "",
    convert: Callable[[Any], Any] | None = None,
) -> Reading[Any]:
    value, reason = _safe(call, label)
    if value is None:
        return Reading.unavailable(reason or _UNSUPPORTED, unit=unit)
    if convert is not None:
        value = convert(value)
    return Reading(value, unit=unit, source="NVML")


class NvmlSession:
    """Holds NVML initialised for the duration of a block.

    ``nvmlInit``/``nvmlShutdown`` are refcounted by the library, but the
    session keeps a lock so two threads (GUI refresh + background agent)
    cannot race the init/shutdown pair.
    """

    _lock = threading.Lock()

    def __init__(self) -> None:
        self.available = False
        self.unavailable_reason = _NO_NVML

    def __enter__(self) -> "NvmlSession":
        if not _NVML_IMPORTED:
            return self
        with self._lock:
            try:
                pynvml.nvmlInit()
                self.available = True
                self.unavailable_reason = ""
            except Exception as exc:  # noqa: BLE001
                # The usual cause is no NVIDIA GPU, or a driver older than
                # the library. Both are ordinary, not errors worth raising.
                self.available = False
                self.unavailable_reason = (
                    "NVIDIA drivers are not available on this PC "
                    f"({type(exc).__name__})."
                )
                _log.debug("nvmlInit failed: %s", exc)
        return self

    def __exit__(self, *exc_info: object) -> None:
        if not self.available:
            return
        with self._lock:
            try:
                pynvml.nvmlShutdown()
            except Exception:  # noqa: BLE001
                _log.debug("nvmlShutdown failed", exc_info=True)
            self.available = False

    # -- queries -----------------------------------------------------------

    def driver_version(self) -> str:
        if not self.available:
            return ""
        value, _ = _safe(pynvml.nvmlSystemGetDriverVersion, "driver version")
        return _as_text(value)

    def read_all(self) -> list[GpuTelemetry]:
        """Return telemetry for every NVIDIA GPU, or ``[]`` when unavailable."""
        if not self.available:
            return []
        count, _ = _safe(pynvml.nvmlDeviceGetCount, "device count")
        if not count:
            return []
        return [self.read(index) for index in range(int(count))]

    def read(self, index: int) -> GpuTelemetry:
        """Read one GPU. Never raises."""
        handle = pynvml.nvmlDeviceGetHandleByIndex(index)

        name, _ = _safe(lambda: pynvml.nvmlDeviceGetName(handle), "name")
        memory, _ = _safe(
            lambda: pynvml.nvmlDeviceGetMemoryInfo(handle), "memory"
        )
        utilization, _ = _safe(
            lambda: pynvml.nvmlDeviceGetUtilizationRates(handle), "utilization"
        )

        return GpuTelemetry(
            index=index,
            name=_as_text(name),
            temperature_c=_reading(
                lambda: pynvml.nvmlDeviceGetTemperature(
                    handle, pynvml.NVML_TEMPERATURE_GPU
                ),
                "temperature",
                unit="°C",
                convert=float,
            ),
            utilization_percent=(
                Reading(float(utilization.gpu), unit="%", source="NVML")
                if utilization is not None
                else Reading.unavailable(_UNSUPPORTED, unit="%")
            ),
            memory_utilization_percent=(
                Reading(float(utilization.memory), unit="%", source="NVML")
                if utilization is not None
                else Reading.unavailable(_UNSUPPORTED, unit="%")
            ),
            vram_total_mb=(
                Reading(int(memory.total) // 1048576, unit=" MB", source="NVML")
                if memory is not None
                else Reading.unavailable(_UNSUPPORTED, unit=" MB")
            ),
            vram_used_mb=(
                Reading(int(memory.used) // 1048576, unit=" MB", source="NVML")
                if memory is not None
                else Reading.unavailable(_UNSUPPORTED, unit=" MB")
            ),
            fan_percent=_reading(
                lambda: pynvml.nvmlDeviceGetFanSpeed(handle),
                "fan",
                unit="%",
                convert=float,
            ),
            power_watts=_reading(
                lambda: pynvml.nvmlDeviceGetPowerUsage(handle),
                "power",
                unit=" W",
                convert=lambda v: round(v / 1000.0, 1),
            ),
            power_limit_watts=_reading(
                lambda: pynvml.nvmlDeviceGetEnforcedPowerLimit(handle),
                "power limit",
                unit=" W",
                convert=lambda v: round(v / 1000.0, 1),
            ),
            clock_sm_mhz=_reading(
                lambda: pynvml.nvmlDeviceGetClockInfo(handle, pynvml.NVML_CLOCK_SM),
                "sm clock",
                unit=" MHz",
                convert=int,
            ),
            clock_mem_mhz=_reading(
                lambda: pynvml.nvmlDeviceGetClockInfo(handle, pynvml.NVML_CLOCK_MEM),
                "memory clock",
                unit=" MHz",
                convert=int,
            ),
            max_clock_sm_mhz=_reading(
                lambda: pynvml.nvmlDeviceGetMaxClockInfo(handle, pynvml.NVML_CLOCK_SM),
                "max sm clock",
                unit=" MHz",
                convert=int,
            ),
            performance_state=_reading(
                lambda: pynvml.nvmlDeviceGetPerformanceState(handle),
                "performance state",
                convert=int,
            ),
            throttle_reasons=self._throttle_reasons(handle),
            temperature_threshold=self._temperature_threshold(handle),
        )

    @staticmethod
    def _throttle_reasons(handle: Any) -> tuple[str, ...]:
        bitmask, _ = _safe(
            lambda: pynvml.nvmlDeviceGetCurrentClocksThrottleReasons(handle),
            "throttle reasons",
        )
        if bitmask is None:
            return ()
        reasons: list[str] = []
        for constant, label in _THROTTLE_LABELS:
            bit = getattr(pynvml, constant, None)
            if bit is not None and int(bitmask) & int(bit):
                reasons.append(label)
        return tuple(reasons)

    @staticmethod
    def _temperature_threshold(handle: Any) -> Threshold | None:
        """Build a vendor threshold from the driver's own limits.

        ``GPU_MAX`` is the highest temperature NVIDIA rates for continuous
        operation, and ``SLOWDOWN`` is where the driver itself reduces
        clocks. Warning at the first and critical at the second means PGM
        never invents a number for GPU temperature.
        """
        gpu_max, _ = _safe(
            lambda: pynvml.nvmlDeviceGetTemperatureThreshold(
                handle, pynvml.NVML_TEMPERATURE_THRESHOLD_GPU_MAX
            ),
            "gpu max threshold",
        )
        slowdown, _ = _safe(
            lambda: pynvml.nvmlDeviceGetTemperatureThreshold(
                handle, pynvml.NVML_TEMPERATURE_THRESHOLD_SLOWDOWN
            ),
            "slowdown threshold",
        )
        if gpu_max is None and slowdown is None:
            return None

        warning = float(gpu_max if gpu_max is not None else slowdown) # type: ignore[arg-type]
        critical = float(slowdown if slowdown is not None else gpu_max)  # type: ignore[arg-type]
        if critical < warning:
            warning, critical = critical, warning

        return Threshold(
            warning=warning,
            critical=critical,
            kind=ThresholdKind.VENDOR,
            source=(
                f"NVIDIA driver limits: max operating {warning:.0f}°C, "
                f"driver slowdown {critical:.0f}°C"
            ),
            higher_is_worse=True,
        )


def _as_text(value: Any) -> str:
    """NVML returns ``bytes`` on some library versions and ``str`` on others."""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value or "")


def read_telemetry() -> tuple[list[GpuTelemetry], str]:
    """Convenience one-shot read.

    Returns:
        ``(telemetry, reason)``. ``reason`` explains an empty list.
    """
    with NvmlSession() as session:
        if not session.available:
            return [], session.unavailable_reason
        return session.read_all(), ""


def is_available() -> bool:
    with NvmlSession() as session:
        return session.available
