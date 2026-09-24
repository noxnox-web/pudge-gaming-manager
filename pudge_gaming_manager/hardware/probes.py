"""CPU, RAM and operating-system probes.

Fast, read-only, and never requiring elevation — a scan is the most common
operation PGM performs and must work for an unprivileged player session.

CPU temperature is deliberately unavailable. Windows exposes no general CPU
thermal sensor: ``MSAcpi_ThermalZoneTemperature`` reports an ACPI thermal
zone (often the motherboard, sometimes nothing at all) and is widely wrong on
desktop hardware. Reporting a motherboard reading as "CPU temperature" would
be exactly the fabrication rule #64 forbids.
"""

from __future__ import annotations

import os
import platform
from typing import Any, Sequence

import psutil

from ..utilities.logging_setup import get_logger
from .models import CpuInfo, OsInfo, RamInfo, RamModule, Reading

_log = get_logger(__name__)

_MB = 1024 * 1024

#: Documented by Microsoft as the first Windows 11 build.
WINDOWS_11_MIN_BUILD = 22000


def prime_cpu_sampler() -> None:
    """Establish the baseline for a later non-blocking CPU reading.

    ``psutil.cpu_percent(interval=None)`` reports utilization since the
    previous call. Without a prior call it returns ``0.0``, which looks like
    a perfectly idle machine and is simply wrong. Call this at the start of a
    scan; read the value at the end.
    """
    try:
        psutil.cpu_percent(interval=None)
    except (OSError, NotImplementedError):
        _log.debug("cpu_percent priming unavailable")


def scan_os() -> OsInfo:
    """Read operating-system identity."""
    build_str = platform.version().split(".")[-1] if platform.version() else ""
    try:
        build = int(build_str)
    except ValueError:
        build = 0

    caption = ""
    if os.name == "nt":
        # platform.win32_edition() is available on modern CPython and avoids
        # a WMI round-trip for something this simple.
        try:
            edition = platform.win32_edition() or ""
        except (AttributeError, OSError):
            edition = ""
        release = platform.win32_ver()[0] if hasattr(platform, "win32_ver") else ""
        caption = f"Windows {release} {edition}".strip()

    return OsInfo(
        caption=caption or platform.system(),
        version=platform.version(),
        build=str(build) if build else build_str,
        architecture=platform.machine(),
        machine_name=platform.node(),
        # Windows 10 and 11 both report version 10.x; only the build number
        # distinguishes them.
        is_windows_11=build >= WINDOWS_11_MIN_BUILD,
    )


def scan_cpu(cim_rows: Sequence[dict[str, Any]] | None = None) -> CpuInfo:
    """Read CPU identity and utilization.

    Args:
        cim_rows: Optional pre-fetched ``Win32_Processor`` rows. Passing them
            in lets the scanner batch one PowerShell call for several probes
            instead of paying process-start cost per component.
    """
    row: dict[str, Any] = dict(cim_rows[0]) if cim_rows else {}

    model = str(row.get("Name") or platform.processor() or "").strip()
    manufacturer = str(row.get("Manufacturer") or "").strip()
    if not manufacturer and model:
        lowered = model.lower()
        if "intel" in lowered:
            manufacturer = "Intel"
        elif "amd" in lowered:
            manufacturer = "AMD"

    physical = psutil.cpu_count(logical=False)
    logical = psutil.cpu_count(logical=True)

    frequency = None
    try:
        frequency = psutil.cpu_freq()
    except (OSError, NotImplementedError, AttributeError):
        _log.debug("cpu_freq unavailable on this platform")

    base_clock: Reading[float] = Reading.unavailable(
        "Not reported by the processor.", unit=" MHz"
    )
    if row.get("MaxClockSpeed"):
        base_clock = Reading(
            float(row["MaxClockSpeed"]), unit=" MHz", source="Win32_Processor"
        )
    elif frequency and frequency.max:
        base_clock = Reading(float(frequency.max), unit=" MHz", source="psutil")

    current_clock: Reading[float] = (
        Reading(float(frequency.current), unit=" MHz", source="psutil")
        if frequency and frequency.current
        else Reading.unavailable("Current clock not reported.", unit=" MHz")
    )

    # interval=None returns utilization since the previous call, which is
    # non-blocking. A blocking sample would stall the scan for a full second.
    utilization = psutil.cpu_percent(interval=None)

    return CpuInfo(
        manufacturer=manufacturer,
        model=model,
        physical_cores=(
            Reading(physical, source="psutil")
            if physical
            else Reading.unavailable("Core count not reported.")
        ),
        logical_processors=(
            Reading(logical, source="psutil")
            if logical
            else Reading.unavailable("Thread count not reported.")
        ),
        base_clock_mhz=base_clock,
        current_clock_mhz=current_clock,
        utilization_percent=Reading(float(utilization), unit="%", source="psutil"),
        temperature_c=Reading.unavailable(
            "Windows exposes no reliable CPU thermal sensor without a "
            "vendor driver.",
            unit="°C",
        ),
    )


def scan_ram(cim_rows: Sequence[dict[str, Any]] | None = None) -> RamInfo:
    """Read memory totals, usage and installed modules.

    Args:
        cim_rows: Optional pre-fetched ``Win32_PhysicalMemory`` rows.
    """
    virtual = psutil.virtual_memory()

    modules: list[RamModule] = []
    for raw in cim_rows or ():
        capacity = raw.get("Capacity")
        try:
            capacity_mb = int(capacity) // _MB if capacity else 0
        except (TypeError, ValueError):
            capacity_mb = 0
        modules.append(
            RamModule(
                manufacturer=str(raw.get("Manufacturer") or "").strip(),
                capacity_mb=capacity_mb,
                speed_mhz=int(raw.get("Speed") or 0),
                part_number=str(raw.get("PartNumber") or "").strip(),
            )
        )

    speeds = {m.speed_mhz for m in modules if m.speed_mhz}
    if len(speeds) == 1:
        speed: Reading[int] = Reading(
            speeds.pop(), unit=" MHz", source="Win32_PhysicalMemory"
        )
    elif len(speeds) > 1:
        # Mixed speeds run at the slowest module. Reporting the fastest would
        # overstate the machine.
        speed = Reading(
            min(speeds),
            unit=" MHz",
            source="Win32_PhysicalMemory (mixed modules; slowest reported)",
        )
    else:
        speed = Reading.unavailable("Module speed not reported.", unit=" MHz")

    return RamInfo(
        total_mb=Reading(virtual.total // _MB, unit=" MB", source="psutil"),
        available_mb=Reading(virtual.available // _MB, unit=" MB", source="psutil"),
        used_mb=Reading((virtual.total - virtual.available) // _MB, unit=" MB",
                        source="psutil"),
        usage_percent=Reading(float(virtual.percent), unit="%", source="psutil"),
        speed_mhz=speed,
        modules=tuple(modules),
    )
