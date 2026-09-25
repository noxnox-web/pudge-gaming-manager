"""Removing OneDrive, which is not a Store application.

OneDrive is a desktop program with its own installer, so ``Get-AppxPackage``
never sees it and ``Remove-AppxPackage`` cannot touch it. Its setup
executable takes ``/uninstall``, which is the supported way and the one the
Settings app itself calls.

What this does not do
---------------------
It does not delete the ``OneDrive`` folder. Uninstalling stops the sync
client; the files that were synced stay on disk, and the copies in the cloud
stay in the cloud. Deleting the local folder would be destroying somebody's
documents to save a process, so that is left to a person — and the cleaner's
protected-path list refuses to touch it either.

Finding it
----------
Whether OneDrive is installed and where its uninstaller lives are two
different questions, and answering the first with the second was a bug.
``%SystemRoot%\\SysWOW64\\OneDriveSetup.exe`` (``System32`` on 32-bit) ships
with Windows and **stays after an uninstall** — it is what installs OneDrive
into each new profile. Treating its presence as "installed" made every
successful uninstall look failed, and offered the removal again forever.

So "installed" means the sync client itself, ``OneDrive.exe``, per-user in
``%LOCALAPPDATA%`` or per-machine in ``Program Files``. The uninstaller is
looked for separately: the Windows copy first, then the versioned copy the
client keeps beside itself.
"""

from __future__ import annotations

import os
import pathlib

from ...utilities.command_runner import CommandRunner
from ...utilities.exceptions import PgmError
from ...utilities.logging_setup import audit_event, get_logger

_log = get_logger(__name__)

#: Where the sync client lives: per-user first, then the per-machine layouts.
_CLIENT_LOCATIONS: tuple[str, ...] = (
    "%LOCALAPPDATA%\\Microsoft\\OneDrive\\OneDrive.exe",
    "%ProgramFiles%\\Microsoft OneDrive\\OneDrive.exe",
    "%ProgramFiles(x86)%\\Microsoft OneDrive\\OneDrive.exe",
)

#: The uninstaller Windows itself ships. Present whether or not OneDrive is.
_SYSTEM_SETUP_LOCATIONS: tuple[str, ...] = (
    "%SystemRoot%\\SysWOW64\\OneDriveSetup.exe",
    "%SystemRoot%\\System32\\OneDriveSetup.exe",
)

#: The running sync client, which holds the files the uninstaller replaces.
_PROCESS = "OneDrive.exe"


def _existing(templates: tuple[str, ...]) -> list[pathlib.Path]:
    found: list[pathlib.Path] = []
    for template in templates:
        expanded = os.path.expandvars(template)
        if "%" in expanded:
            continue
        path = pathlib.Path(expanded)
        if path.is_file():
            found.append(path)
    return found


def find_client() -> pathlib.Path | None:
    """The installed ``OneDrive.exe``, or ``None`` when OneDrive is absent."""
    found = _existing(_CLIENT_LOCATIONS)
    return found[0] if found else None


def is_installed() -> bool:
    return find_client() is not None


def is_machine_wide(client: pathlib.Path) -> bool:
    """True for a Program Files install, which needs ``/allusers`` and admin."""
    local = os.path.expandvars("%LOCALAPPDATA%")
    if "%" in local:
        return True
    try:
        client.relative_to(local)
    except ValueError:
        return True
    return False


def find_setup(client: pathlib.Path | None = None) -> pathlib.Path | None:
    """The uninstaller to run, or ``None`` when there is none to be found."""
    system = _existing(_SYSTEM_SETUP_LOCATIONS)
    if system:
        return system[0]
    if client is None:
        return None
    # The client keeps its own installer in a version-named folder beside it.
    beside = sorted(client.parent.glob("*/OneDriveSetup.exe"), reverse=True)
    return beside[0] if beside else None


def uninstall(runner: CommandRunner | None = None) -> tuple[bool, str]:
    """Run the documented uninstaller. Returns ``(ok, detail)``.

    The client is stopped first: its own executable is one of the files the
    uninstaller removes, and leaving it running is what turns an uninstall
    into a half-uninstall that comes back at the next sign-in.
    """
    client = find_client()
    if client is None:
        return True, "OneDrive не установлен."

    setup = find_setup(client)
    if setup is None:
        return False, "OneDrive установлен, но его деинсталлятор не найден."

    command = runner or CommandRunner()

    # Not an error if it was not running; that is the desired state.
    try:
        command.run(["taskkill", "/F", "/IM", _PROCESS], check=False, timeout_s=30)
    except PgmError as exc:
        _log.debug("could not stop %s: %s", _PROCESS, exc.what)

    args = [str(setup), "/uninstall"]
    if is_machine_wide(client):
        args.append("/allusers")
    try:
        command.run(args, check=True, timeout_s=300)
    except PgmError as exc:
        audit_event(
            "apps", "uninstall_onedrive", target=str(client), result="FAILED",
            error=exc.reason or exc.what,
        )
        return False, f"Не удалось удалить OneDrive: {exc.reason or exc.what}"

    # The uninstaller returns before the files are gone on some builds, so
    # success is decided by looking for the client rather than by the exit
    # code — and never by the Windows copy of the setup, which stays.
    still_there = find_client()
    audit_event(
        "apps", "uninstall_onedrive", target=str(client),
        result="SUCCESS" if still_there is None else "PARTIAL",
    )
    if still_there is None:
        return True, "OneDrive удалён. Папка с файлами оставлена на месте."
    return False, (
        f"Деинсталлятор отработал, но {still_there} всё ещё на месте — "
        "возможно, OneDrive был запущен."
    )


__all__ = ["find_client", "find_setup", "is_installed", "is_machine_wide", "uninstall"]
