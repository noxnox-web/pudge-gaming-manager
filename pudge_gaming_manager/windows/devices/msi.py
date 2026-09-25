"""Whether a PCI device is set to use message-signalled interrupts.

MSI mode is the setting guides reach for with "MSI Utility": a device either
raises line-based interrupts (INTx), which can be shared with other devices
on the same line, or writes a message, which cannot be. Modern GPU, NVMe
and USB 3 drivers enable MSI themselves; the registry value below is where
their INF records it.

Read-only, and deliberately so. Forcing ``MSISupported`` on a device whose
driver does not handle it leaves that device without working interrupts
after the next boot — for a GPU, a black screen; for the USB controller the
keyboard is on, no way to undo it from the keyboard. That is not a change a
club-PC tool should make with one click, so PGM reports and a person
decides.
"""

from __future__ import annotations

import winreg
from dataclasses import dataclass

from ...utilities import wmi
from ...utilities.command_runner import CommandRunner
from ...utilities.exceptions import PgmError
from ...utilities.powershell_runner import PowerShellRunner

_KEY = (
    r"SYSTEM\CurrentControlSet\Enum\{instance}\Device Parameters"
    r"\Interrupt Management\MessageSignaledInterruptProperties"
)


@dataclass(frozen=True, slots=True)
class MsiState:
    supported: bool | None
    """``True``/``False`` from ``MSISupported``; ``None`` when the driver
    wrote nothing, which usually means line-based interrupts."""

    message_limit: int | None = None
    error: str = ""

    @property
    def label(self) -> str:
        if self.error:
            return f"не прочитано ({self.error})"
        if self.supported is None:
            return "не задано драйвером (обычно INTx)"
        if not self.supported:
            return "выключен (INTx)"
        limit = f", лимит сообщений: {self.message_limit}" if self.message_limit else ""
        return f"включён{limit}"


def display_adapters(powershell=None) -> list[tuple[str, str]]:
    """``(name, PnP instance id)`` for each display adapter. Never raises."""
    if powershell is None:
        try:
            rows = wmi.query({"v": (
                "root/cimv2", "SELECT Name, PNPDeviceID FROM Win32_VideoController"
            )})["v"]
            return [
                (str(r.get("Name") or ""), str(r.get("PNPDeviceID") or ""))
                for r in rows
                if str(r.get("PNPDeviceID") or "").upper().startswith("PCI\\")
            ]
        except wmi.WmiError:
            pass
    runner = powershell or PowerShellRunner(CommandRunner())
    try:
        rows = runner.run_json(
            "Get-CimInstance Win32_VideoController | ForEach-Object { "
            "[pscustomobject]@{ Name = [string]$_.Name; Id = [string]$_.PNPDeviceID } }",
            timeout_s=60,
            operation="display adapters",
        )
    except PgmError:
        return []
    return [
        (str(r.get("Name") or ""), str(r.get("Id") or ""))
        for r in rows
        if str(r.get("Id") or "").upper().startswith("PCI\\")
    ]


def read(instance_id: str) -> MsiState:
    """MSI state for one PCI device instance id. Never raises."""
    if not instance_id.upper().startswith("PCI\\"):
        return MsiState(None, error="не PCI-устройство")
    try:
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            _KEY.format(instance=instance_id),
            0,
            winreg.KEY_READ | winreg.KEY_WOW64_64KEY,
        ) as key:
            try:
                supported, _ = winreg.QueryValueEx(key, "MSISupported")
            except FileNotFoundError:
                return MsiState(None)
            try:
                limit, _ = winreg.QueryValueEx(key, "MessageNumberLimit")
            except FileNotFoundError:
                limit = None
    except FileNotFoundError:
        return MsiState(None)
    except OSError as exc:
        return MsiState(None, error=str(exc.strerror or exc))
    return MsiState(bool(supported), int(limit) if limit is not None else None)


__all__ = ["MsiState", "display_adapters", "read"]
