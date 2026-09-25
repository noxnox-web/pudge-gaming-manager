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
from ...utilities import wmi
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
        *,
        use_wmi: bool = True,
    ) -> None:
        """
        Args:
            use_wmi: Read the inventory in-process through COM first. Tests
                that inject a fake ``powershell`` turn it off so the fake is
                what gets read.
        """
        self._use_wmi = use_wmi
        self.runner = runner or CommandRunner()
        self.powershell = powershell or PowerShellRunner(runner=self.runner)
        self._inventory: dict[str, list[dict[str, Any]]] | None = None
        """The last successful inventory, or None before the first scan.

        One scanner is shared between the dashboard's scan controller and
        the optimize controller, which run on different worker threads, so
        two scans can touch this at once. That is safe without a lock and
        deliberately so: the dict is built complete in ``_fetch_cim`` and
        never mutated afterwards, and rebinding a name is atomic under the
        GIL. A concurrent reader therefore sees either the previous
        inventory or the new one, both of which are whole and valid — never
        a half-filled dict. Adding a lock here would buy nothing and would
        make a scan able to block another one.
        """

    def scan(self, *, reuse_inventory: bool = False) -> HardwareSnapshot:
        """Run a hardware scan.

        Never raises for a partially-unavailable system. A probe that fails
        contributes a warning and leaves its values unavailable, because a
        scan that returns nothing is less useful than one that returns most
        things and says what is missing.

        Args:
            reuse_inventory: Reuse the *inventory* from the previous scan —
                which CPU, how many memory modules, each disk's model and
                SMART verdict — instead of asking PowerShell for it again.
                That one call is 2.3 of the 2.4 seconds a scan takes, and
                what it returns is the hardware's identity, which does not
                change while the window is open.

                Every **measurement** is still taken fresh: processor load,
                GPU temperature, free disk space, the current display mode.
                Nothing carried over is a reading, so a refreshed snapshot
                never shows a stale number as if it had just been taken
                (rule #40). Hardware genuinely appearing mid-session — a
                monitor plugged in, a drive attached — is what the explicit
                rescan is for, and that always does the full call.
        """
        started = time.monotonic()
        warnings: list[str] = []

        # psutil.cpu_percent(interval=None) measures since the *previous*
        # call, so the first call in a process always returns 0.0. Priming
        # here means the reading taken after the CIM query covers that
        # query's duration (~2.5s) — a real sample at no extra cost, rather
        # than a blocking sleep or a fabricated zero.
        probes.prime_cpu_sampler()

        if reuse_inventory and self._inventory is not None:
            cim = self._inventory
        else:
            cim = self._fetch_cim(warnings)
            if cim:
                self._inventory = cim

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
            "%d warning, inventory %s)",
            duration, len(gpus), len(disks), len(monitors), len(warnings),
            "reused" if reuse_inventory and self._inventory is cim else "read",
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
        """Fetch every CIM collection: in-process WMI, else one PowerShell call.

        WMI through COM returns the same five collections in about 0.2 s;
        the PowerShell call takes 2.3 s, most of it starting the
        interpreter. PowerShell stays as the fallback, so a machine where
        COM is unavailable loses speed, never data.
        """
        if self._use_wmi:
            try:
                started = time.monotonic()
                rows = _fetch_wmi()
                _log.info("inventory via WMI in %.2fs", time.monotonic() - started)
                return rows
            except wmi.WmiError as exc:
                _log.info("in-process WMI unavailable, using PowerShell: %s", exc.reason)
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


#: The composite query, as WQL per collection. Same properties, same keys.
_WQL = {
    "Processor": ("root/cimv2", "SELECT Name, Manufacturer, MaxClockSpeed, "
                  "NumberOfCores, NumberOfLogicalProcessors FROM Win32_Processor"),
    "Memory": ("root/cimv2", "SELECT Manufacturer, Capacity, Speed, PartNumber "
               "FROM Win32_PhysicalMemory"),
    "Video": ("root/cimv2", "SELECT Name, AdapterCompatibility, DriverVersion, "
              "DriverDate FROM Win32_VideoController"),
    "Physical": ("root/Microsoft/Windows/Storage", "SELECT DeviceId, FriendlyName, "
                 "MediaType, BusType, HealthStatus, Size FROM MSFT_PhysicalDisk"),
    "Partitions": ("root/Microsoft/Windows/Storage",
                   "SELECT DriveLetter, DiskNumber FROM MSFT_Partition"),
}


def _fetch_wmi() -> dict[str, list[dict[str, Any]]]:
    rows = wmi.query(_WQL)
    # DriveLetter is a Char16: COM hands over its code, 0 for "no letter".
    volume_map = []
    for row in rows.pop("Partitions"):
        letter = row.get("DriveLetter")
        if isinstance(letter, int):
            letter = chr(letter) if letter else ""
        if letter:
            volume_map.append({"Drive": f"{letter}:", "Index": row.get("DiskNumber")})
    rows["VolumeMap"] = volume_map
    return rows


def _utc_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(tz=timezone.utc).isoformat(timespec="seconds")
