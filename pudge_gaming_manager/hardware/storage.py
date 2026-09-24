"""Storage probes: capacity, free space, media type and SMART health.

Media type and health come from ``MSFT_PhysicalDisk`` in the
``root/Microsoft/Windows/Storage`` namespace, which is the documented modern
source and reports SSD/HDD correctly. ``Win32_DiskDrive`` does not expose
media type at all, and inferring "SSD" from a model-name substring is the
kind of guess rule #64 forbids.
"""

from __future__ import annotations

from typing import Any, Sequence

import psutil

from ..utilities.logging_setup import get_logger
from .models import DiskInfo, Reading

_log = get_logger(__name__)

_GB = 1024 ** 3

#: MSFT_PhysicalDisk.MediaType, per Microsoft's storage WMI documentation.
MEDIA_TYPES: dict[int, str] = {
    0: "",           # Unspecified
    3: "HDD",
    4: "SSD",
    5: "SCM",        # Storage-class memory
}

#: MSFT_PhysicalDisk.HealthStatus.
HEALTH_STATUS: dict[int, str] = {
    0: "Healthy",
    1: "Warning",
    2: "Unhealthy",
}

#: MSFT_PhysicalDisk.BusType, the values relevant to a gaming PC.
BUS_TYPES: dict[int, str] = {
    1: "SCSI", 2: "ATAPI", 3: "ATA", 4: "1394", 5: "SSA", 6: "Fibre Channel",
    7: "USB", 8: "RAID", 9: "iSCSI", 10: "SAS", 11: "SATA", 12: "SD",
    13: "MMC", 15: "File Backed Virtual", 16: "Storage Spaces", 17: "NVMe",
}

#: PowerShell for physical-disk facts. Returns [] when the Storage namespace
#: is unavailable rather than failing the whole scan.
PHYSICAL_DISK_QUERY = """
Get-CimInstance -Namespace 'root/Microsoft/Windows/Storage' `
    -ClassName MSFT_PhysicalDisk -ErrorAction SilentlyContinue |
  Select-Object DeviceId, FriendlyName, MediaType, BusType, HealthStatus,
                Size, SerialNumber
"""

#: Maps a drive letter to the physical disk behind it, so free space (a
#: volume fact) can be joined to media type and health (disk facts).
VOLUME_TO_DISK_QUERY = """
Get-CimInstance -ClassName Win32_LogicalDiskToPartition -ErrorAction SilentlyContinue |
  ForEach-Object {
    $logical = $_.Dependent.DeviceID
    $partition = $_.Antecedent.DeviceID
    $drive = Get-CimInstance -ClassName Win32_DiskDriveToDiskPartition `
        -ErrorAction SilentlyContinue |
      Where-Object { $_.Dependent.DeviceID -eq $partition } |
      Select-Object -First 1
    if ($drive) {
      $disk = Get-CimInstance -ClassName Win32_DiskDrive -ErrorAction SilentlyContinue |
        Where-Object { $_.DeviceID -eq $drive.Antecedent.DeviceID } |
        Select-Object -First 1
      if ($disk) {
        [pscustomobject]@{ Drive = $logical; Index = $disk.Index; Model = $disk.Model }
      }
    }
  }
"""


def scan_disks(
    physical_rows: Sequence[dict[str, Any]] | None = None,
    volume_map_rows: Sequence[dict[str, Any]] | None = None,
) -> list[DiskInfo]:
    """Enumerate fixed drives with capacity, free space and health.

    Args:
        physical_rows: Pre-fetched ``MSFT_PhysicalDisk`` rows.
        volume_map_rows: Pre-fetched drive-letter to disk-index mapping.

    Returns:
        One entry per mounted fixed volume. Removable and network drives are
        excluded: a USB stick's free space is not a health signal for the PC.
    """
    system_drive = _system_drive_letter()

    by_index: dict[int, dict[str, Any]] = {}
    for row in physical_rows or ():
        try:
            by_index[int(row.get("DeviceId"))] = row
        except (TypeError, ValueError):
            continue

    drive_to_index: dict[str, int] = {}
    drive_to_model: dict[str, str] = {}
    for row in volume_map_rows or ():
        drive = str(row.get("Drive") or "").upper()
        if not drive:
            continue
        try:
            drive_to_index[drive] = int(row.get("Index"))
        except (TypeError, ValueError):
            pass
        drive_to_model[drive] = str(row.get("Model") or "").strip()

    disks: list[DiskInfo] = []
    for partition in psutil.disk_partitions(all=False):
        if "cdrom" in partition.opts or not partition.fstype:
            continue

        drive = partition.device.rstrip("\\").upper()
        try:
            usage = psutil.disk_usage(partition.mountpoint)
        except (PermissionError, OSError) as exc:
            _log.debug("cannot read usage for %s: %s", partition.mountpoint, exc)
            disks.append(
                DiskInfo(
                    device_id=drive,
                    filesystem=partition.fstype,
                    is_system_disk=drive == system_drive,
                    total_gb=Reading.unavailable("Drive is not readable.", unit=" GB"),
                    free_gb=Reading.unavailable("Drive is not readable.", unit=" GB"),
                    free_percent=Reading.unavailable("Drive is not readable.", unit="%"),
                )
            )
            continue

        physical = by_index.get(drive_to_index.get(drive, -1), {})
        media_code = physical.get("MediaType")
        health_code = physical.get("HealthStatus")
        bus_code = physical.get("BusType")

        smart: Reading[bool]
        if health_code is None:
            smart = Reading.unavailable(
                "Health status not reported by the storage driver."
            )
        else:
            try:
                smart = Reading(
                    int(health_code) == 0,
                    source="MSFT_PhysicalDisk.HealthStatus",
                )
            except (TypeError, ValueError):
                smart = Reading.unavailable("Health status was not a known value.")

        disks.append(
            DiskInfo(
                device_id=drive,
                model=str(
                    physical.get("FriendlyName") or drive_to_model.get(drive, "")
                ).strip(),
                media_type=MEDIA_TYPES.get(_as_int(media_code), ""),
                bus_type=BUS_TYPES.get(_as_int(bus_code), ""),
                total_gb=Reading(usage.total / _GB, unit=" GB", source="psutil"),
                free_gb=Reading(usage.free / _GB, unit=" GB", source="psutil"),
                free_percent=Reading(
                    100.0 - float(usage.percent), unit="%", source="psutil"
                ),
                filesystem=partition.fstype,
                smart_healthy=smart,
                # SMART attribute 194 would give temperature, but reading raw
                # SMART needs a privileged device handle and vendor-specific
                # decoding. Out of scope for v1.0 rather than guessed.
                temperature_c=Reading.unavailable(
                    "Requires privileged raw SMART access; not supported in v1.0.",
                    unit="°C",
                ),
                is_system_disk=drive == system_drive,
            )
        )

    return disks


def _system_drive_letter() -> str:
    import os

    return (os.environ.get("SystemDrive") or "C:").upper()


def _as_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return -1
