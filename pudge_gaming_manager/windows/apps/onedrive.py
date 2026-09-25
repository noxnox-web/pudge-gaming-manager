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

Finding the installer
---------------------
Its location moved between Windows builds: the per-user copy lives in
``%LOCALAPPDATA%\\Microsoft\\OneDrive``, and the machine-wide one in
``System32`` or ``SysWOW64`` depending on the build's bitness. All the known
locations are checked rather than one being assumed, because assuming meant
reporting success on a machine where nothing had been run.
"""

from __future__ import annotations

import os
import pathlib

from ...utilities.command_runner import CommandRunner
from ...utilities.exceptions import PgmError
from ...utilities.logging_setup import audit_event, get_logger

_log = get_logger(__name__)

#: Where ``OneDriveSetup.exe`` has lived, newest layout first.
_SETUP_LOCATIONS: tuple[str, ...] = (
    "%LOCALAPPDATA%\\Microsoft\\OneDrive\\OneDriveSetup.exe",
    "%SystemRoot%\\SysWOW64\\OneDriveSetup.exe",
    "%SystemRoot%\\System32\\OneDriveSetup.exe",
)

#: The running sync client, which holds the files the uninstaller replaces.
_PROCESS = "OneDrive.exe"


def find_setup() -> pathlib.Path | None:
    """The uninstaller, or ``None`` when OneDrive is not installed."""
    for template in _SETUP_LOCATIONS:
        expanded = os.path.expandvars(template)
        if "%" in expanded:
            continue
        path = pathlib.Path(expanded)
        if path.is_file():
            return path
    return None


def is_installed() -> bool:
    return find_setup() is not None


def uninstall(runner: CommandRunner | None = None) -> tuple[bool, str]:
    """Run the documented uninstaller. Returns ``(ok, detail)``.

    The client is stopped first: its own executable is one of the files the
    uninstaller removes, and leaving it running is what turns an uninstall
    into a half-uninstall that comes back at the next sign-in.
    """
    setup = find_setup()
    if setup is None:
        return True, "OneDrive не установлен."

    command = runner or CommandRunner()

    # Not an error if it was not running; that is the desired state.
    try:
        command.run(["taskkill", "/F", "/IM", _PROCESS], check=False, timeout_s=30)
    except PgmError as exc:
        _log.debug("could not stop %s: %s", _PROCESS, exc.what)

    try:
        command.run([str(setup), "/uninstall"], check=True, timeout_s=300)
    except PgmError as exc:
        audit_event(
            "apps", "uninstall_onedrive", target=str(setup), result="FAILED",
            error=exc.reason or exc.what,
        )
        return False, f"Не удалось удалить OneDrive: {exc.reason or exc.what}"

    # The uninstaller returns before the files are gone on some builds, so
    # success is decided by looking rather than by the exit code.
    still_there = find_setup()
    audit_event(
        "apps", "uninstall_onedrive", target=str(setup),
        result="SUCCESS" if still_there is None else "PARTIAL",
    )
    if still_there is None:
        return True, "OneDrive удалён. Папка с файлами оставлена на месте."
    return False, (
        f"Установщик отработал, но {still_there.name} всё ещё на месте — "
        "возможно, OneDrive был запущен."
    )


__all__ = ["find_setup", "is_installed", "uninstall"]
