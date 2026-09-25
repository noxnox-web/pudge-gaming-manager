"""«Разрешить отключение этого устройства для экономии энергии».

The checkbox on a device's Power Management tab in Device Manager. Behind
it is the WMI class ``MSPower_DeviceEnable`` in ``root\\wmi``: one instance
per device that supports the setting, named by the device's instance id
plus ``_0``, with a boolean ``Enable``. Device Manager writes exactly that,
so this module does too — no driver-specific registry values.

Which devices to touch is the caller's decision — the tweaks pick USB hubs
and devices, or the physical network adapters, by instance id. Writing
needs elevation; reading does not.
"""

from __future__ import annotations

from dataclasses import dataclass

from ...utilities import wmi
from ...utilities.command_runner import CommandRunner
from ...utilities.exceptions import PgmError
from ...utilities.logging_setup import audit_event, get_logger
from ...utilities.powershell_runner import PowerShellRunner

_log = get_logger(__name__)

_READ = r"""
Get-CimInstance -Namespace root\wmi -ClassName MSPower_DeviceEnable -ErrorAction Stop |
  ForEach-Object { [pscustomobject]@{ Name = [string]$_.InstanceName; Enable = [bool]$_.Enable } }
"""

#: ``$Names`` is a newline-separated list, bound through the environment so
#: no instance name can alter the script.
_WRITE = r"""
$wanted = @{}
foreach ($n in ($Names -split "`n")) { if ($n) { $wanted[$n.ToLowerInvariant()] = $true } }
$value = [bool]::Parse($Value)
Get-CimInstance -Namespace root\wmi -ClassName MSPower_DeviceEnable -ErrorAction Stop |
  Where-Object { $wanted.ContainsKey(([string]$_.InstanceName).ToLowerInvariant()) } |
  ForEach-Object {
    $ok = $true
    try { Set-CimInstance -InputObject $_ -Property @{ Enable = $value } -ErrorAction Stop }
    catch { $ok = $false }
    [pscustomobject]@{ Name = [string]$_.InstanceName; Ok = $ok }
  }
"""


@dataclass(frozen=True, slots=True)
class DevicePowerState:
    instance_name: str
    enabled: bool
    """True when Windows may power the device down."""

    @property
    def instance_id(self) -> str:
        """The PnP instance id: WMI appends ``_0`` to it."""
        name = self.instance_name
        return name[:-2] if name.endswith("_0") else name


def read(powershell: PowerShellRunner | None = None) -> list[DevicePowerState]:
    """Every device's power-saving flag. Raises :class:`PgmError`.

    In-process through WMI (about 0.05 s); PowerShell (about 2 s) only when
    a runner is injected or COM is unavailable.
    """
    if powershell is None:
        try:
            rows = wmi.query({"p": (
                "root/wmi", "SELECT InstanceName, Enable FROM MSPower_DeviceEnable"
            )})["p"]
            return [
                DevicePowerState(str(r.get("InstanceName") or ""), bool(r.get("Enable")))
                for r in rows if r.get("InstanceName")
            ]
        except wmi.WmiError as exc:
            _log.info("device power via WMI unavailable: %s", exc.reason)
    runner = powershell or PowerShellRunner(CommandRunner())
    rows = runner.run_json(_READ, timeout_s=60, operation="read device power")
    return [
        DevicePowerState(str(r.get("Name") or ""), bool(r.get("Enable")))
        for r in rows
        if r.get("Name")
    ]


def set_enabled(
    names: list[str], enabled: bool, powershell: PowerShellRunner | None = None
) -> list[str]:
    """Set the flag on the named devices. Returns the names that failed."""
    if not names:
        return []
    runner = powershell or PowerShellRunner(CommandRunner())
    try:
        rows = runner.run_json(
            _WRITE,
            parameters={"Names": "\n".join(names), "Value": str(enabled)},
            timeout_s=120,
            requires_admin=True,
            operation="set device power",
        )
    except PgmError as exc:
        _log.warning("device power write failed: %s", exc.what)
        return list(names)

    succeeded = {str(r.get("Name") or "").lower() for r in rows if r.get("Ok")}
    failed = [n for n in names if n.lower() not in succeeded]
    audit_event(
        "devices", "set_power_saving",
        target=f"{len(names)} devices",
        new_state={"enabled": enabled},
        result="SUCCESS" if not failed else "PARTIAL",
        error=", ".join(failed[:5]) or None,
    )
    return failed


__all__ = ["DevicePowerState", "read", "set_enabled"]
