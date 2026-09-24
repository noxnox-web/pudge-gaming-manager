"""Hardware scan orchestration.

Lives in ``core`` rather than ``hardware`` because it composes several
subsystems and the database, and a subsystem may not import a
sibling. ``tests/test_architecture.py`` enforces that.

Performance note
----------------
Every CIM class needed is fetched in **one** PowerShell invocation. Starting
``powershell.exe`` costs roughly a second; five separate probes would make a
scan feel broken on a club PC. One call, one composite JSON object.
"""

from __future__ import annotations

import time
from typing import Any

from ...hardware import probes, storage
from ...hardware.gpu import detect
from ...hardware.models import HardwareSnapshot
from ...hardware.monitor import display
from ...utilities.command_runner import CommandRunner
from ...utilities.exceptions import PgmError
from ...utilities.logging_setup import get_logger
from ...utilities.powershell_runner import PowerShellRunner

_log = get_logger(__name__)

#: One round-trip for every CIM class the scan needs.
#:
#: Each sub-query is individually guarded with SilentlyContinue: a machine
#: without the Storage namespace must still return processor and memory data
#: rather than failing the whole scan.
_COMPOSITE_QUERY = """
$processor = @(Get-CimInstance Win32_Processor -ErrorAction SilentlyContinue |
    Select-Object Name, Manufacturer, MaxClockSpeed, NumberOfCores,
                  NumberOfLogicalProcessors)

$memory = @(Get-CimInstance Win32_PhysicalMemory -ErrorAction SilentlyContinue |
    Select-Object Manufacturer, Capacity, Speed, PartNumber)

$video = @(Get-CimInstance Win32_VideoController -ErrorAction SilentlyContinue |
    Select-Object Name, AdapterCompatibility, DriverVersion,
      @{ Name = 'DriverDate'
         Expression = {
           if ($_.DriverDate) { $_.DriverDate.ToString('yyyy-MM-dd') } else { '' }
         } })

$physical = @(Get-CimInstance -Namespace 'root/Microsoft/Windows/Storage' `
    -ClassName MSFT_PhysicalDisk -ErrorAction SilentlyContinue |
    Select-Object DeviceId, FriendlyName, MediaType, BusType, HealthStatus, Size)

# MSFT_Partition maps a drive letter straight to a disk number, and
# MSFT_PhysicalDisk.DeviceId *is* that disk number. This is the documented
# modern mapping; the older Win32_DiskDriveToDiskPartition association chain
# needs WQL path escaping and returned nothing on the test machine.
$volumeMap = @(
  Get-CimInstance -Namespace 'root/Microsoft/Windows/Storage' `
      -ClassName MSFT_Partition -ErrorAction SilentlyContinue |
    Where-Object { $_.DriveLetter } | ForEach-Object {
      [pscustomobject]@{
        Drive = ([string]$_.DriveLetter + ':')
        Index = $_.DiskNumber
      }
    }
)

[pscustomobject]@{
  Processor  = $processor
  Memory     = $memory
  Video      = $video
  Physical   = $physical
  VolumeMap  = $volumeMap
}
"""


class HardwareScanner:
    """Produces a :class:`HardwareSnapshot`.

    Read-only and unprivileged by design: a scan is the most common operation
    and must work in a standard player session.
    """

    def __init__(
        self,
        runner: CommandRunner | None = None,
        powershell: PowerShellRunner | None = None,
    ) -> None:
        self.runner = runner or CommandRunner()
        self.powershell = powershell or PowerShellRunner(runner=self.runner)

    def scan(self) -> HardwareSnapshot:
        """Run a full hardware scan.

        Never raises for a partially-unavailable system. A probe that fails
        contributes a warning and leaves its values unavailable, because a
        scan that returns nothing is less useful than one that returns most
        things and says what is missing.
        """
        started = time.monotonic()
        warnings: list[str] = []

        # psutil.cpu_percent(interval=None) measures since the *previous*
        # call, so the first call in a process always returns 0.0. Priming
        # here means the reading taken after the CIM query covers that
        # query's duration (~2.5s) — a real sample at no extra cost, rather
        # than a blocking sleep or a fabricated zero.
        probes.prime_cpu_sampler()

        cim = self._fetch_cim(warnings)

        os_info = probes.scan_os()
        cpu = self._safe(
            lambda: probes.scan_cpu(cim.get("Processor")),
            fallback=probes.CpuInfo(),
            label="CPU",
            warnings=warnings,
        )
        ram = self._safe(
            lambda: probes.scan_ram(cim.get("Memory")),
            fallback=probes.RamInfo(),
            label="memory",
            warnings=warnings,
        )
        gpus = self._safe(
            lambda: detect.scan_gpus(self.runner, cim.get("Video")),
            fallback=[],
            label="GPU",
            warnings=warnings,
        )
        disks = self._safe(
            lambda: storage.scan_disks(cim.get("Physical"), cim.get("VolumeMap")),
            fallback=[],
            label="storage",
            warnings=warnings,
        )
        monitors = self._safe(
            display.scan_monitors,
            fallback=[],
            label="display",
            warnings=warnings,
        )

        duration = time.monotonic() - started
        _log.info(
            "hardware scan finished in %.2fs (%d GPU, %d disk, %d monitor, "
            "%d warning)",
            duration, len(gpus), len(disks), len(monitors), len(warnings),
        )

        return HardwareSnapshot(
            captured_at=_utc_now(),
            os=os_info,
            cpu=cpu,
            gpus=tuple(gpus),
            ram=ram,
            disks=tuple(disks),
            monitors=tuple(monitors),
            scan_duration_s=duration,
            warnings=tuple(warnings),
        )

    # -- internals ---------------------------------------------------------

    def _fetch_cim(self, warnings: list[str]) -> dict[str, list[dict[str, Any]]]:
        """Fetch every CIM collection in one PowerShell call."""
        try:
            rows = self.powershell.run_json(
                _COMPOSITE_QUERY, depth=4, timeout_s=90
            )
        except PgmError as exc:
            warnings.append(
                f"System inventory could not be read: {exc.reason or exc.what}"
            )
            _log.warning("composite CIM query failed: %s", exc.what)
            return {}

        if not rows:
            warnings.append("System inventory returned no data.")
            return {}

        composite = rows[0]
        return {
            key: [r for r in (composite.get(key) or []) if isinstance(r, dict)]
            for key in ("Processor", "Memory", "Video", "Physical", "VolumeMap")
        }

    @staticmethod
    def _safe(probe: Any, *, fallback: Any, label: str, warnings: list[str]) -> Any:
        """Run one probe, converting failure into a warning.

        Broad by intent: a scan must survive any single probe misbehaving on
        unfamiliar hardware. The exception is logged with a traceback, so
        nothing is actually swallowed.
        """
        try:
            return probe()
        except Exception as exc:  # noqa: BLE001 - see docstring
            warnings.append(f"The {label} probe failed: {exc}")
            _log.exception("%s probe failed", label)
            return fallback


def _utc_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(tz=timezone.utc).isoformat(timespec="seconds")
