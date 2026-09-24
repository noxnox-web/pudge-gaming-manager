"""Administrator-privilege detection.

PGM does not run its whole GUI elevated (rule #37). It detects what it has,
states plainly which operations need more, and requests elevation only for
those. A read-only scan — the most common operation — must never require
administrator rights.
"""

from __future__ import annotations

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
