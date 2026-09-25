"""Pointer acceleration — what Windows calls "enhance pointer precision".

Why ``SystemParametersInfo`` and not the registry
-------------------------------------------------
The three values live in ``HKCU\\Control Panel\\Mouse`` and it is tempting to
write them there. That would be wrong twice over: the running session keeps
its own copy, so a registry write changes nothing until the next sign-in, and
a value written directly is not validated by anyone.

``SystemParametersInfoW`` is the documented interface. With
``SPIF_UPDATEINIFILE | SPIF_SENDCHANGE`` one call applies the setting to the
live session *and* persists it *and* notifies other applications — and
``SPI_GETMOUSE`` reads back what is actually in force, which is what makes
the change verifiable rather than merely attempted.

What the three values mean
--------------------------
``[threshold1, threshold2, acceleration]``. Acceleration is the switch: at
0 the pointer moves a distance proportional to how far the mouse moved, full
stop. Above 0 Windows multiplies the distance once the mouse crosses
``threshold1`` within a polling interval, and again past ``threshold2``.

The defaults are ``[6, 10, 1]`` — acceleration on. Turning it off is the one
setting competitive players agree about: with acceleration, the same physical
movement produces a different on-screen distance depending on how fast it was
made, so a practised flick lands somewhere different each time. Nothing here
claims a frame-rate effect; the claim is consistency of aim, which is what
the setting actually governs.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass

from ...utilities.logging_setup import audit_event, get_logger

_log = get_logger(__name__)

_SPI_GETMOUSE = 0x0003
_SPI_SETMOUSE = 0x0004

#: Persist the change to the user profile as well as applying it live.
_SPIF_UPDATEINIFILE = 0x01
#: Tell running applications the setting changed.
_SPIF_SENDCHANGE = 0x02

_MouseParams = ctypes.c_int * 3


@dataclass(frozen=True, slots=True)
class MouseAcceleration:
    """The three values, as Windows reports them."""

    threshold1: int
    threshold2: int
    acceleration: int

    @property
    def enabled(self) -> bool:
        """True when Windows is scaling movement by speed."""
        return self.acceleration > 0

    def as_text(self) -> str:
        """Stable form for a backup record."""
        return f"{self.threshold1},{self.threshold2},{self.acceleration}"

    @classmethod
    def from_text(cls, text: str) -> "MouseAcceleration | None":
        parts = str(text).split(",")
        if len(parts) != 3:
            return None
        try:
            return cls(*(int(p) for p in parts))
        except ValueError:
            return None


#: What Windows ships with: acceleration on.
DEFAULT = MouseAcceleration(threshold1=6, threshold2=10, acceleration=1)

#: Acceleration off, thresholds neutralised with it.
DISABLED = MouseAcceleration(threshold1=0, threshold2=0, acceleration=0)


def _user32() -> ctypes.WinDLL | None:
    try:
        return ctypes.WinDLL("user32", use_last_error=True)
    except (AttributeError, OSError):  # pragma: no cover - not Windows
        return None


def read() -> MouseAcceleration | None:
    """The setting currently in force, or ``None`` if it cannot be read."""
    user32 = _user32()
    if user32 is None:
        return None

    params = _MouseParams()
    try:
        ok = user32.SystemParametersInfoW(
            wintypes.UINT(_SPI_GETMOUSE), wintypes.UINT(0), ctypes.byref(params),
            wintypes.UINT(0),
        )
    except OSError as exc:
        _log.warning("SPI_GETMOUSE raised: %s", exc)
        return None
    if not ok:
        _log.warning(
            "SPI_GETMOUSE failed: %s", ctypes.WinError(ctypes.get_last_error())
        )
        return None
    return MouseAcceleration(params[0], params[1], params[2])


def write(setting: MouseAcceleration) -> bool:
    """Apply and persist ``setting``. Returns whether Windows accepted it.

    Success here is not proof: the caller re-reads with :func:`read`,
    because a call that returns non-zero having done nothing is exactly the
    failure a verification step exists to catch.
    """
    user32 = _user32()
    if user32 is None:
        return False

    params = _MouseParams(
        setting.threshold1, setting.threshold2, setting.acceleration
    )
    before = read()
    try:
        ok = bool(
            user32.SystemParametersInfoW(
                wintypes.UINT(_SPI_SETMOUSE),
                wintypes.UINT(0),
                ctypes.byref(params),
                wintypes.UINT(_SPIF_UPDATEINIFILE | _SPIF_SENDCHANGE),
            )
        )
    except OSError as exc:
        _log.warning("SPI_SETMOUSE raised: %s", exc)
        return False

    audit_event(
        "input",
        "set_mouse_acceleration",
        target="SPI_SETMOUSE",
        old_state=before.as_text() if before else None,
        new_state=setting.as_text(),
        result="SUCCESS" if ok else "FAILED",
    )
    return ok


__all__ = ["DEFAULT", "DISABLED", "MouseAcceleration", "read", "write"]
