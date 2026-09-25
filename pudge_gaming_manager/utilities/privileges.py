"""Administrator-privilege detection.

PGM does not run its whole GUI elevated (rule #37). It detects what it has,
states plainly which operations need more, and requests elevation only for
those. A read-only scan — the most common operation — must never require
administrator rights.
"""

from __future__ import annotations

import contextlib
import ctypes
import functools
import os
import sys
from dataclasses import dataclass
from enum import Enum

from .exceptions import PrivilegeError


class Elevation(str, Enum):
    """What privilege an operation needs."""

    NONE = "NONE"
    """Runs as a standard user. All read-only scanning lives here."""

    ADMIN = "ADMIN"
    """Requires an elevated token."""


@dataclass(frozen=True, slots=True)
class PrivilegeState:
    """The privileges this process actually holds."""

    is_admin: bool
    username: str
    is_windows: bool

    def require(self, operation: str) -> None:
        """Raise :class:`PrivilegeError` unless this process is elevated.

        Called at the boundary of any privileged operation so the failure is
        a clear, actionable message rather than an ``OSError`` from deep
        inside a Windows API call.
        """
        if not self.is_admin:
            raise PrivilegeError(
                operation,
                context={"username": self.username, "is_windows": self.is_windows},
            )


def _query_is_admin() -> bool:
    """Ask Windows whether this process holds an elevated token.

    ``shell32.IsUserAnAdmin`` is the documented check and reflects the *token*,
    which is what matters: a user in the Administrators group running without
    elevation is correctly reported as not admin.
    """
    if os.name != "nt":
        # Non-Windows: only reachable in CI/tests. Never claim admin.
        return False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())  # type: ignore[attr-defined]
    except (AttributeError, OSError):
        # Cannot determine. Assume the weaker privilege — assuming admin we do
        # not have would turn a clear refusal into a confusing mid-operation
        # failure.
        return False


@functools.lru_cache(maxsize=1)
def get_privilege_state() -> PrivilegeState:
    """Return this process's privilege state.

    Cached: the elevation of a running process cannot change, so re-querying
    would be pure overhead in the scan loop.
    """
    return PrivilegeState(
        is_admin=_query_is_admin(),
        username=os.environ.get("USERNAME") or os.environ.get("USER") or "unknown",
        is_windows=os.name == "nt",
    )


def is_admin() -> bool:
    """True when this process holds an elevated token."""
    return get_privilege_state().is_admin


def require_admin(operation: str) -> None:
    """Raise :class:`PrivilegeError` unless elevated.

    Args:
        operation: Plain-language description used in the error message,
            e.g. ``'change the active power plan'``.
    """
    get_privilege_state().require(operation)


def relaunch_as_admin(argv: list[str] | None = None) -> bool:
    """Ask Windows to relaunch this program elevated via the UAC prompt.

    Returns ``True`` when the elevated process was started (the caller should
    then exit), ``False`` when the user declined or the call failed. Never
    raises: a declined UAC prompt is an ordinary user choice, not an error.
    """
    if os.name != "nt" or is_admin():
        return False

    params = " ".join(f'"{a}"' for a in (argv if argv is not None else sys.argv))
    try:
        # ShellExecuteW with the "runas" verb is the documented way to request
        # elevation. Returns >32 on success.
        result = ctypes.windll.shell32.ShellExecuteW(  # type: ignore[attr-defined]
            None, "runas", sys.executable, params, None, 1
        )
        return int(result) > 32
    except (AttributeError, OSError):
        return False


# -- token privileges ---------------------------------------------------------
#
# An elevated token *holds* SeBackupPrivilege and SeRestorePrivilege but has
# them disabled. Enabling them is what lets FILE_OPEN_FOR_BACKUP_INTENT read
# and delete regardless of a file's ACL — the documented way for an
# administrator to deal with TrustedInstaller-owned files, and the reason no
# ownership or ACL has to be rewritten. They are enabled only for the block
# that needs them and put back afterwards.

_SE_PRIVILEGE_ENABLED = 0x2
_TOKEN_ADJUST_PRIVILEGES = 0x20
_TOKEN_QUERY = 0x8
_ERROR_NOT_ALL_ASSIGNED = 1300


class _Luid(ctypes.Structure):
    _fields_ = [("LowPart", ctypes.c_uint32), ("HighPart", ctypes.c_int32)]


class _LuidAndAttributes(ctypes.Structure):
    _fields_ = [("Luid", _Luid), ("Attributes", ctypes.c_uint32)]


def _token_privileges(count: int):
    class _TokenPrivileges(ctypes.Structure):
        _fields_ = [
            ("PrivilegeCount", ctypes.c_uint32),
            ("Privileges", _LuidAndAttributes * count),
        ]

    return _TokenPrivileges


@contextlib.contextmanager
def enabled_privileges(*names: str):
    """Enable the named privileges for the block; yield whether all were.

    Yields ``False`` (and changes nothing) when the token does not hold them
    — a non-elevated process — so a caller can still run without backup
    semantics and let the ordinary ACL checks decide.
    """
    if os.name != "nt" or not names:
        yield False
        return
    from ctypes import wintypes

    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    advapi.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    advapi.LookupPrivilegeValueW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.POINTER(_Luid)]
    advapi.AdjustTokenPrivileges.argtypes = [
        wintypes.HANDLE, wintypes.BOOL, ctypes.c_void_p, wintypes.DWORD,
        ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD),
    ]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]

    token = wintypes.HANDLE()
    if not advapi.OpenProcessToken(
        kernel.GetCurrentProcess(), _TOKEN_ADJUST_PRIVILEGES | _TOKEN_QUERY, ctypes.byref(token)
    ):
        yield False
        return
    structure = _token_privileges(len(names))
    wanted = structure()
    wanted.PrivilegeCount = len(names)
    for index, name in enumerate(names):
        luid = _Luid()
        if not advapi.LookupPrivilegeValueW(None, name, ctypes.byref(luid)):
            kernel.CloseHandle(token)
            yield False
            return
        wanted.Privileges[index] = _LuidAndAttributes(luid, _SE_PRIVILEGE_ENABLED)
    previous = structure()
    returned = wintypes.DWORD()
    ok = advapi.AdjustTokenPrivileges(
        token, False, ctypes.byref(wanted), ctypes.sizeof(previous),
        ctypes.byref(previous), ctypes.byref(returned),
    )
    all_assigned = bool(ok) and ctypes.get_last_error() != _ERROR_NOT_ALL_ASSIGNED
    try:
        yield all_assigned
    finally:
        if ok:
            # Restores exactly the prior state of each privilege it changed.
            advapi.AdjustTokenPrivileges(token, False, ctypes.byref(previous), 0, None, None)
        kernel.CloseHandle(token)
