"""Reading and toggling what Windows launches at sign-in.

Two mechanisms, one list
------------------------
An autostart entry is either a registry value under a ``Run`` key or a
shortcut in a Startup folder. Both are enumerated here and presented as one
list, because the operator does not care which mechanism a program used.

How an entry is disabled
------------------------
Never by deleting it. Windows keeps its own enabled/disabled flag under
``...\\CurrentVersion\\Explorer\\StartupApproved``, which is exactly what
Task Manager's Startup tab writes, and that is what this module writes too.
The consequences matter:

* Disabling is reversible. The ``Run`` value and the shortcut stay where
  they are; only the approval byte changes.
* An operator who later looks in Task Manager sees the same state PGM set,
  rather than wondering where an entry went.
* PGM never has to write to a ``Run`` key, which the registry allowlist
  refuses on purpose — those keys are how persistence is installed, and
  nothing here needs them.

``StartupApproved`` itself sits beneath the already-allowlisted
``...\\CurrentVersion\\Explorer`` key, so every write goes through
``RegistryManager`` with its type-preserving backup, rather than reaching
for ``winreg`` directly.

The approval byte
-----------------
Twelve bytes, of which only the first is a flag: bit 0 set means disabled.
Windows writes ``02`` for enabled and ``03`` for disabled, and uses ``06``
and ``07`` in some cases, so the bit is tested rather than the whole byte.
A disabled entry carries the FILETIME of when it was disabled in bytes
4..12; enabled entries leave those zero.
"""

from __future__ import annotations

import os
import pathlib
import struct
import time
import winreg
from dataclasses import dataclass
from enum import Enum

from ...utilities.logging_setup import audit_event, get_logger
from ..registry.manager import Hive, RegistryManager, RegistryView

_log = get_logger(__name__)

_RUN = r"Software\Microsoft\Windows\CurrentVersion\Run"
_APPROVED = r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved"

#: Length of a StartupApproved value. Windows writes exactly this.
_APPROVAL_SIZE = 12

#: Seconds between the FILETIME epoch (1601-01-01) and the Unix epoch.
_FILETIME_EPOCH_DELTA = 11_644_473_600


class StartupSource(str, Enum):
    """Where an entry lives, and therefore how it is approved."""

    RUN_USER = "RUN_USER"
    RUN_MACHINE = "RUN_MACHINE"
    RUN_MACHINE_32 = "RUN_MACHINE_32"
    FOLDER_USER = "FOLDER_USER"
    FOLDER_COMMON = "FOLDER_COMMON"

    @property
    def label(self) -> str:
        return {
            StartupSource.RUN_USER: "Реестр — этот пользователь",
            StartupSource.RUN_MACHINE: "Реестр — все пользователи",
            StartupSource.RUN_MACHINE_32: "Реестр — все пользователи (32-бит)",
            StartupSource.FOLDER_USER: "Папка автозагрузки — этот пользователь",
            StartupSource.FOLDER_COMMON: "Папка автозагрузки — все пользователи",
        }[self]

    @property
    def hive(self) -> Hive:
        return (
            Hive.HKCU
            if self in (StartupSource.RUN_USER, StartupSource.FOLDER_USER)
            else Hive.HKLM
        )

    @property
    def approval_subkey(self) -> str:
        """The StartupApproved subkey holding this source's flags."""
        return {
            StartupSource.RUN_USER: f"{_APPROVED}\\Run",
            StartupSource.RUN_MACHINE: f"{_APPROVED}\\Run",
            StartupSource.RUN_MACHINE_32: f"{_APPROVED}\\Run32",
            StartupSource.FOLDER_USER: f"{_APPROVED}\\StartupFolder",
            StartupSource.FOLDER_COMMON: f"{_APPROVED}\\StartupFolder",
        }[self]

    @property
    def view(self) -> RegistryView:
        return (
            RegistryView.BIT32
            if self is StartupSource.RUN_MACHINE_32
            else RegistryView.BIT64
        )

    @property
    def requires_admin(self) -> bool:
        """Machine-wide entries need elevation to toggle."""
        return self.hive is Hive.HKLM


@dataclass(frozen=True, slots=True)
class StartupEntry:
    """One program Windows would launch at sign-in."""

    name: str
    """The registry value name, or the shortcut's file name."""

    command: str
    """The command line, or the shortcut's full path."""

    source: StartupSource
    enabled: bool

    @property
    def location_label(self) -> str:
        return self.source.label

    @property
    def key(self) -> str:
        """Stable identity for the UI's selection set."""
        return f"{self.source.value}::{self.name}"


def _approval_is_enabled(data: object) -> bool:
    """Read the approval flag. Anything unrecognised counts as enabled.

    A missing or malformed value is how Windows represents "never
    disabled", and guessing "disabled" for it would show an entry as off
    while it still runs.
    """
    if not isinstance(data, (bytes, bytearray)) or not data:
        return True
    return not (data[0] & 1)


def _approval_payload(enabled: bool) -> bytes:
    """The twelve bytes Windows writes for this state."""
    if enabled:
        return bytes([2]) + bytes(_APPROVAL_SIZE - 1)
    filetime = int((time.time() + _FILETIME_EPOCH_DELTA) * 10_000_000)
    return bytes([3, 0, 0, 0]) + struct.pack("<Q", filetime)


def _startup_folder(source: StartupSource) -> pathlib.Path | None:
    template = (
        "%APPDATA%\\Microsoft\\Windows\\Start Menu\\Programs\\Startup"
        if source is StartupSource.FOLDER_USER
        else "%ProgramData%\\Microsoft\\Windows\\Start Menu\\Programs\\Startup"
    )
    expanded = os.path.expandvars(template)
    if "%" in expanded:
        return None
    path = pathlib.Path(expanded)
    return path if path.is_dir() else None


class StartupManager:
    """Enumerates autostart entries and turns them on or off."""

    def __init__(self, registry: RegistryManager | None = None) -> None:
        self._registry = registry or RegistryManager()

    # -- read --------------------------------------------------------------

    def entries(self) -> list[StartupEntry]:
        """Every autostart entry this account can see. Changes nothing."""
        found: list[StartupEntry] = []
        for source in StartupSource:
            approvals = self._approvals(source)
            if source.name.startswith("RUN"):
                found.extend(self._run_entries(source, approvals))
            else:
                found.extend(self._folder_entries(source, approvals))

        found.sort(key=lambda e: (not e.enabled, e.name.lower()))
        _log.info("startup scan: %d entries", len(found))
        return found

    def _approvals(self, source: StartupSource) -> dict[str, bool]:
        """Value name -> enabled, for one source's approval key."""
        states: dict[str, bool] = {}
        try:
            with winreg.OpenKey(
                source.hive.handle,
                source.approval_subkey,
                0,
                winreg.KEY_READ | source.view.flag,
            ) as key:
                index = 0
                while True:
                    try:
                        name, data, _ = winreg.EnumValue(key, index)
                    except OSError:
                        break
                    states[name.lower()] = _approval_is_enabled(data)
                    index += 1
        except (FileNotFoundError, PermissionError, OSError):
            # No approval key means nothing was ever disabled here.
            return states
        return states

    def _run_entries(
        self, source: StartupSource, approvals: dict[str, bool]
    ) -> list[StartupEntry]:
        entries: list[StartupEntry] = []
        try:
            with winreg.OpenKey(
                source.hive.handle, _RUN, 0, winreg.KEY_READ | source.view.flag
            ) as key:
                index = 0
                while True:
                    try:
                        name, data, _ = winreg.EnumValue(key, index)
                    except OSError:
                        break
                    index += 1
                    if not name:
                        continue
                    entries.append(
                        StartupEntry(
                            name=name,
                            command=str(data),
                            source=source,
                            enabled=approvals.get(name.lower(), True),
                        )
                    )
        except (FileNotFoundError, PermissionError, OSError) as exc:
            _log.debug("cannot read %s\\%s: %s", source.hive.value, _RUN, exc)
        return entries

    def _folder_entries(
        self, source: StartupSource, approvals: dict[str, bool]
    ) -> list[StartupEntry]:
        folder = _startup_folder(source)
        if folder is None:
            return []
        entries: list[StartupEntry] = []
        try:
            for item in sorted(folder.iterdir()):
                if item.is_dir() or item.name.lower() == "desktop.ini":
                    continue
                entries.append(
                    StartupEntry(
                        name=item.name,
                        command=str(item),
                        source=source,
                        enabled=approvals.get(item.name.lower(), True),
                    )
                )
        except OSError as exc:
            _log.debug("cannot list %s: %s", folder, exc)
        return entries

    # -- write -------------------------------------------------------------

    def set_enabled(self, entry: StartupEntry, enabled: bool) -> StartupEntry:
        """Turn one entry on or off, and return its re-read state.

        The result is read back from the registry rather than assumed: a
        write that silently failed must not be reported as a change.
        """
        self._registry.write(
            entry.source.hive,
            entry.source.approval_subkey,
            entry.name,
            _approval_payload(enabled),
            "REG_BINARY",
            view=entry.source.view,
        )

        confirmed = self._approvals(entry.source).get(entry.name.lower(), True)
        audit_event(
            "startup",
            "set_enabled",
            target=entry.key,
            old_state={"enabled": entry.enabled},
            new_state={"enabled": confirmed},
            result="SUCCESS" if confirmed == enabled else "FAILED",
        )
        return StartupEntry(
            name=entry.name,
            command=entry.command,
            source=entry.source,
            enabled=confirmed,
        )


__all__ = ["StartupEntry", "StartupManager", "StartupSource"]
