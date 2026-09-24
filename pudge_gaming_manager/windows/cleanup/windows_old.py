"""Removing ``%SystemDrive%\\Windows.old`` after a Windows 11 upgrade.

A feature update leaves the previous install in ``Windows.old`` so the user
can roll back for ten days, after which Windows deletes it on its own. On a
club PC it is dead weight — often tens of gigabytes — and there is no reason
to keep the rollback.

It cannot be removed like an ordinary folder: many files are owned by
``TrustedInstaller`` and deny even administrators, which is why Disk Cleanup
uses a privileged path. This does the documented equivalent — take ownership,
grant the Administrators group full control, then remove — through
``takeown``, ``icacls`` and ``rmdir``, each resolved from System32 by
``CommandRunner``.

Deleting it removes the ability to roll the Windows upgrade back. That is the
operator's decision, made at the preview; this module only carries it out.
"""

from __future__ import annotations

import os
import pathlib
import sys

from ...utilities.command_runner import CommandRunner
from ...utilities.logging_setup import get_logger
from ...utilities.secure_delete import _is_reparse_point

_log = get_logger(__name__)

#: Windows 11 is build 22000 and up; ``Windows.old`` on 10 is out of scope.
_WINDOWS_11_BUILD = 22000

#: takeown /r on a large tree is slow; give it room rather than time out.
_STEP_TIMEOUT_S = 600.0


def is_windows_11() -> bool:
    """True when this is Windows 11 (build 22000+)."""
    if os.name != "nt":
        return False
    try:
        return sys.getwindowsversion().build >= _WINDOWS_11_BUILD  # type: ignore[attr-defined]
    except (AttributeError, OSError):
        return False


def windows_old_path() -> pathlib.Path:
    """``Windows.old`` on the system drive."""
    system_drive = os.environ.get("SystemDrive", "C:")
    return pathlib.Path(system_drive + "\\") / "Windows.old"


def is_present(path: pathlib.Path | None = None) -> bool:
    """True when a real ``Windows.old`` directory exists (not a link)."""
    target = path or windows_old_path()
    try:
        return target.is_dir() and not _is_reparse_point(target)
    except OSError:
        return False


def folder_size(path: pathlib.Path | None = None) -> int | None:
    """Best-effort size of ``Windows.old``; ``None`` if it cannot be read.

    Access-denied files (still owned by TrustedInstaller) are skipped, so the
    figure is a floor, not an exact total — enough to show the operator the
    scale of what will be freed.
    """
    target = path or windows_old_path()
    if not target.is_dir():
        return None
    total = 0
    for current, _dirs, files in os.walk(target, followlinks=False):
        here = pathlib.Path(current)
        for name in files:
            try:
                total += (here / name).stat().st_size
            except OSError:
                continue
    return total


def remove(
    path: pathlib.Path | None = None, runner: CommandRunner | None = None
) -> bool:
    """Take ownership of ``Windows.old`` and delete it. Returns success.

    Refuses a reparse point (a junction planted in place of the folder must
    not redirect an elevated delete). Each step needs administrator rights.
    """
    target = path or windows_old_path()
    if not is_present(target):
        return not target.exists()

    run = runner or CommandRunner()
    text = str(target)

    # Take ownership and grant Administrators full control, or the delete is
    # denied on TrustedInstaller-owned files.
    run.try_run(["takeown", "/f", text, "/r", "/d", "Y"], timeout_s=_STEP_TIMEOUT_S)
    run.try_run(
        ["icacls", text, "/grant", "*S-1-5-32-544:F", "/t", "/c", "/q"],
        timeout_s=_STEP_TIMEOUT_S,
    )
    # rmdir via cmd: one native recursive delete, far faster than per-file.
    run.try_run(["cmd", "/c", "rmdir", "/s", "/q", text], timeout_s=_STEP_TIMEOUT_S)

    removed = not target.exists()
    _log.info("windows.old removal at %s: %s", target, "removed" if removed else "failed")
    return removed


__all__ = [
    "folder_size",
    "is_present",
    "is_windows_11",
    "remove",
    "windows_old_path",
]
