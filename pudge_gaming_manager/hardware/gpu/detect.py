"""GPU detection and telemetry.

Identity (model, driver version and date) comes from WMI; everything measured
comes from NVML, NVIDIA's documented read-only management library.

Why ``AdapterRAM`` is never read
--------------------------------
``Win32_VideoController.AdapterRAM`` is a 32-bit signed field. On the
development machine it reports a 12 GB RTX 3060 as 4 GB, and it saturates for
every card above 4 GB. VRAM comes from NVML instead.

Why NVML rather than ``nvidia-smi``
-----------------------------------
NVML is the library ``nvidia-smi`` itself is built on, so it returns the same
data without spawning a process per scan. On AMD and Intel GPUs no equivalent
documented interface ships with Windows, so those cards report identity only
and their telemetry stays unavailable with that reason stated.
"""

from __future__ import annotations

import os
from typing import Any, Sequence

from ...utilities.command_runner import CommandRunner, resolve_executable
from ...utilities.logging_setup import get_logger
from ..models import GpuInfo, Reading
from . import nvml

_log = get_logger(__name__)

#: Fallback only: used when NVML cannot load but the CLI is present.
NVIDIA_SMI_ARGS = [
    "--query-gpu=name,driver_version,memory.total",
    "--format=csv,noheader,nounits",
]


def _nvidia_smi() -> str | None:
    """``nvidia-smi`` by absolute path, from a location users cannot write.

    DCH drivers install it in System32 (resolved as a bare name); older
    drivers put it under Program Files. PATH and the working directory are
    never searched, since PGM may be running elevated.
    """
    found = resolve_executable("nvidia-smi")
    if found:
        return found
    legacy = os.path.join(
        os.environ.get("ProgramFiles") or r"C:\Program Files",
        "NVIDIA Corporation", "NVSMI", "nvidia-smi.exe",
    )
    return legacy if os.path.isfile(legacy) else None

_NON_NVIDIA = (
    "Windows provides no documented telemetry interface for this GPU vendor."
)


def _vendor_from_name(name: str) -> str:
    lowered = name.lower()
    if "nvidia" in lowered or "geforce" in lowered or "quadro" in lowered:
        return "NVIDIA"
    if "amd" in lowered or "radeon" in lowered:
        return "AMD"
    if "intel" in lowered:
        return "Intel"
    return ""


def _parse_driver_date(raw: Any) -> str:
    """Normalise a driver date to ``YYYY-MM-DD``.

    Three shapes reach this function, so all three are handled rather than
    assuming one:

    * ``'2026-09-05'`` — what the scanner's query produces directly.
    * ``'/Date(1788480000000)/'`` — PowerShell's legacy ``ConvertTo-Json``
      serialisation of a ``DateTime``, in Unix milliseconds.
    * ``'20260905000000.000000+180'`` — raw WMI ``CIM_DATETIME``.

    Returns an empty string rather than raising: a missing driver date must
    never fail a scan.
    """
    text = str(raw or "").strip()
    if not text:
        return ""

    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        return text[:10]

    if text.startswith("/Date(") and text.endswith(")/"):
        inner = text[6:-2]
        for separator in ("+", "-"):
            index = inner.find(separator, 1)
            if index > 0:
                inner = inner[:index]
                break
        try:
            from datetime import datetime, timezone

            moment = datetime.fromtimestamp(int(inner) / 1000.0, tz=timezone.utc)
            return moment.strftime("%Y-%m-%d")
        except (ValueError, OverflowError, OSError):
            return ""

    if len(text) >= 8 and text[:8].isdigit():
        return f"{text[0:4]}-{text[4:6]}-{text[6:8]}"
    return ""


def query_nvidia_vram_mb(runner: CommandRunner) -> dict[str, int]:
    """Fallback VRAM lookup through the ``nvidia-smi`` CLI.

    Only used when NVML fails to load. Uses ``try_run``: a missing
    ``nvidia-smi`` on an AMD machine is an expected absence.
    """
    executable = _nvidia_smi()
    if executable is None:
        return {}
    result = runner.try_run([executable, *NVIDIA_SMI_ARGS], timeout_s=15)
    if result is None or not result.ok:
        return {}

    vram: dict[str, int] = {}
    for line in result.lines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 3:
            continue
        try:
            vram[parts[0].lower()] = int(float(parts[2]))
        except ValueError:
            continue
    return vram


def _match_telemetry(
    model: str, telemetry: Sequence[nvml.GpuTelemetry]
) -> nvml.GpuTelemetry | None:
    """Pair a WMI adapter with its NVML device by name."""
    lowered = model.lower()
    for item in telemetry:
        name = item.name.lower()
        if name and (name == lowered or name in lowered or lowered in name):
            return item
    # A single NVIDIA GPU and a single NVML device can only be each other.
    if len(telemetry) == 1 and _vendor_from_name(model) == "NVIDIA":
        return telemetry[0]
    return None


def scan_gpus(
    runner: CommandRunner,
    video_controller_rows: Sequence[dict[str, Any]] | None = None,
) -> list[GpuInfo]:
    """Detect installed GPUs and read NVIDIA telemetry."""
    rows = list(video_controller_rows or ())
    if not rows:
        return []

    telemetry, nvml_reason = nvml.read_telemetry()
    fallback_vram: dict[str, int] = {}
    if not telemetry:
        fallback_vram = query_nvidia_vram_mb(runner)

    gpus: list[GpuInfo] = []
    for row in rows:
        model = str(row.get("Name") or "").strip()
        if not model:
            continue

        vendor = (
            str(row.get("AdapterCompatibility") or "").strip()
            or _vendor_from_name(model)
        )
        measured = _match_telemetry(model, telemetry)

        if measured is not None:
            gpus.append(
                _from_telemetry(row, model, vendor, measured)
            )
            continue

        # No NVML data: identity only.
        reason = (
            nvml_reason
            if _vendor_from_name(model) == "NVIDIA" and nvml_reason
            else _NON_NVIDIA
        )
        vram = Reading.unavailable(
            "Windows does not report VRAM reliably for this GPU "
            "(AdapterRAM is a 32-bit field and is wrong above 4 GB).",
            unit=" MB",
        )
        matched = fallback_vram.get(model.lower())
        if matched is None:
            for name, size in fallback_vram.items():
                if name in model.lower() or model.lower() in name:
                    matched = size
                    break
        if matched is not None:
            vram = Reading(matched, unit=" MB", source="nvidia-smi")

        gpus.append(
            GpuInfo(
                vendor=vendor,
                model=model,
                driver_version=str(row.get("DriverVersion") or "").strip(),
                driver_date=_parse_driver_date(row.get("DriverDate")),
                vram_total_mb=vram,
                temperature_c=Reading.unavailable(reason, unit="°C"),
                utilization_percent=Reading.unavailable(reason, unit="%"),
                vram_used_mb=Reading.unavailable(reason, unit=" MB"),
                vram_used_percent=Reading.unavailable(reason, unit="%"),
                fan_percent=Reading.unavailable(reason, unit="%"),
                power_watts=Reading.unavailable(reason, unit=" W"),
                power_limit_watts=Reading.unavailable(reason, unit=" W"),
                clock_sm_mhz=Reading.unavailable(reason, unit=" MHz"),
                max_clock_sm_mhz=Reading.unavailable(reason, unit=" MHz"),
                performance_state=Reading.unavailable(reason),
            )
        )

    return gpus


def _from_telemetry(
    row: dict[str, Any],
    model: str,
    vendor: str,
    measured: nvml.GpuTelemetry,
) -> GpuInfo:
    return GpuInfo(
        vendor=vendor,
        model=model,
        # NVML reports the driver version the runtime is actually using;
        # prefer it over the WMI adapter's inf version string.
        driver_version=str(row.get("DriverVersion") or "").strip(),
        driver_date=_parse_driver_date(row.get("DriverDate")),
        vram_total_mb=measured.vram_total_mb,
        temperature_c=measured.temperature_c,
        utilization_percent=measured.utilization_percent,
        vram_used_mb=measured.vram_used_mb,
        vram_used_percent=measured.vram_used_percent,
        fan_percent=measured.fan_percent,
        power_watts=measured.power_watts,
        power_limit_watts=measured.power_limit_watts,
        clock_sm_mhz=measured.clock_sm_mhz,
        max_clock_sm_mhz=measured.max_clock_sm_mhz,
        performance_state=measured.performance_state,
        throttle_reasons=measured.throttle_reasons,
        thermal_throttling=measured.is_thermally_throttling,
        power_throttling=measured.is_power_throttling,
        temperature_threshold=measured.temperature_threshold,
    )


def has_nvidia_gpu(gpus: Sequence[GpuInfo]) -> bool:
    return any(gpu.is_nvidia for gpu in gpus)
