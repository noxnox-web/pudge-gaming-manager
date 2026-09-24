"""Display enumeration via the documented Win32 display API.

Uses ``EnumDisplayDevicesW`` + ``EnumDisplaySettingsExW`` (documented in the
Windows SDK) rather than WMI. Two reasons:

* ``EnumDisplaySettingsExW`` enumerates *every* mode the adapter/monitor pair
  supports, which is what "capable of 240 Hz, running 144 Hz" detection needs.
  WMI reports the current mode only.
* It reflects what Windows will actually accept, because it is the same data
  ``ChangeDisplaySettingsExW`` validates against.

Reference: Win32 ``EnumDisplaySettingsExW``, ``DEVMODEW``,
``ChangeDisplaySettingsExW``.
"""

from __future__ import annotations

import ctypes
import os
from ctypes import wintypes
from dataclasses import dataclass

from ..models import DisplayMode, MonitorInfo, Reading

# -- Win32 constants -------------------------------------------------------

ENUM_CURRENT_SETTINGS = -1
ENUM_REGISTRY_SETTINGS = -2

DISPLAY_DEVICE_ATTACHED_TO_DESKTOP = 0x00000001
DISPLAY_DEVICE_PRIMARY_DEVICE = 0x00000004

DM_PELSWIDTH = 0x00080000
DM_PELSHEIGHT = 0x00100000
DM_DISPLAYFREQUENCY = 0x00400000

CDS_UPDATEREGISTRY = 0x00000001
CDS_TEST = 0x00000002

DISP_CHANGE_SUCCESSFUL = 0
DISP_CHANGE_RESTART = 1
DISP_CHANGE_FAILED = -1
DISP_CHANGE_BADMODE = -2
DISP_CHANGE_NOTUPDATED = -3
DISP_CHANGE_BADFLAGS = -4
DISP_CHANGE_BADPARAM = -5
DISP_CHANGE_BADDUALVIEW = -6

#: Documented return codes, mapped to language an operator can act on.
CHANGE_RESULT_MESSAGES: dict[int, str] = {
    DISP_CHANGE_SUCCESSFUL: "The display settings were changed.",
    DISP_CHANGE_RESTART: "The computer must be restarted for this mode.",
    DISP_CHANGE_FAILED: "The display driver rejected the mode.",
    DISP_CHANGE_BADMODE: "This display mode is not supported.",
    DISP_CHANGE_NOTUPDATED: "Windows could not write the setting to the registry.",
    DISP_CHANGE_BADFLAGS: "Invalid flags were passed to the display API.",
    DISP_CHANGE_BADPARAM: "Invalid parameters were passed to the display API.",
    DISP_CHANGE_BADDUALVIEW: "The mode is not valid in a multi-display setup.",
}


class DISPLAY_DEVICEW(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("DeviceName", wintypes.WCHAR * 32),
        ("DeviceString", wintypes.WCHAR * 128),
        ("StateFlags", wintypes.DWORD),
        ("DeviceID", wintypes.WCHAR * 128),
        ("DeviceKey", wintypes.WCHAR * 128),
    ]


class DEVMODEW(ctypes.Structure):
    """Subset of DEVMODEW sufficient for display-mode work.

    The union members before ``dmFields`` are laid out as the documented
    ``POINTL`` + display-orientation fields; the layout must match the SDK
    exactly or ``EnumDisplaySettingsExW`` writes past the wrong offsets.
    """

    _fields_ = [
        ("dmDeviceName", wintypes.WCHAR * 32),
        ("dmSpecVersion", wintypes.WORD),
        ("dmDriverVersion", wintypes.WORD),
        ("dmSize", wintypes.WORD),
        ("dmDriverExtra", wintypes.WORD),
        ("dmFields", wintypes.DWORD),
        ("dmPositionX", ctypes.c_long),
        ("dmPositionY", ctypes.c_long),
        ("dmDisplayOrientation", wintypes.DWORD),
        ("dmDisplayFixedOutput", wintypes.DWORD),
        ("dmColor", ctypes.c_short),
        ("dmDuplex", ctypes.c_short),
        ("dmYResolution", ctypes.c_short),
        ("dmTTOption", ctypes.c_short),
        ("dmCollate", ctypes.c_short),
        ("dmFormName", wintypes.WCHAR * 32),
        ("dmLogPixels", wintypes.WORD),
        ("dmBitsPerPel", wintypes.DWORD),
        ("dmPelsWidth", wintypes.DWORD),
        ("dmPelsHeight", wintypes.DWORD),
        ("dmDisplayFlags", wintypes.DWORD),
        ("dmDisplayFrequency", wintypes.DWORD),
        ("dmICMMethod", wintypes.DWORD),
        ("dmICMIntent", wintypes.DWORD),
        ("dmMediaType", wintypes.DWORD),
        ("dmDitherType", wintypes.DWORD),
        ("dmReserved1", wintypes.DWORD),
        ("dmReserved2", wintypes.DWORD),
        ("dmPanningWidth", wintypes.DWORD),
        ("dmPanningHeight", wintypes.DWORD),
    ]


@dataclass(frozen=True, slots=True)
class DisplayChangeOutcome:
    """Result of a display-mode change attempt."""

    code: int
    message: str

    @property
    def ok(self) -> bool:
        return self.code == DISP_CHANGE_SUCCESSFUL

    @property
    def needs_restart(self) -> bool:
        return self.code == DISP_CHANGE_RESTART


def _user32() -> ctypes.WinDLL:
    return ctypes.WinDLL("user32", use_last_error=True)


def _is_windows() -> bool:
    return os.name == "nt"


def enumerate_display_devices() -> list[DISPLAY_DEVICEW]:
    """Return display adapters attached to the desktop."""
    if not _is_windows():
        return []
    user32 = _user32()
    devices: list[DISPLAY_DEVICEW] = []
    index = 0
    while True:
        device = DISPLAY_DEVICEW()
        device.cb = ctypes.sizeof(DISPLAY_DEVICEW)
        if not user32.EnumDisplayDevicesW(None, index, ctypes.byref(device), 0):
            break
        if device.StateFlags & DISPLAY_DEVICE_ATTACHED_TO_DESKTOP:
            devices.append(device)
        index += 1
    return devices


def _monitor_friendly_name(adapter_device_name: str) -> str:
    """Resolve the monitor name behind an adapter, e.g. 'Generic PnP Monitor'."""
    if not _is_windows():
        return ""
    user32 = _user32()
    monitor = DISPLAY_DEVICEW()
    monitor.cb = ctypes.sizeof(DISPLAY_DEVICEW)
    if user32.EnumDisplayDevicesW(adapter_device_name, 0, ctypes.byref(monitor), 0):
        return monitor.DeviceString
    return ""


def get_current_mode(device_name: str) -> DisplayMode | None:
    """Return the mode the display is running right now."""
    if not _is_windows():
        return None
    devmode = DEVMODEW()
    devmode.dmSize = ctypes.sizeof(DEVMODEW)
    ok = _user32().EnumDisplaySettingsExW(
        device_name, ENUM_CURRENT_SETTINGS, ctypes.byref(devmode), 0
    )
    if not ok:
        return None
    return DisplayMode(
        width=int(devmode.dmPelsWidth),
        height=int(devmode.dmPelsHeight),
        refresh_hz=int(devmode.dmDisplayFrequency),
    )


def enumerate_modes(device_name: str) -> list[DisplayMode]:
    """Return every mode the adapter reports for this display.

    Modes reporting 0 or 1 Hz are filtered out: those are the documented
    "hardware default / unspecified" values, not real refresh rates, and
    treating them as real would corrupt the maximum.
    """
    if not _is_windows():
        return []
    user32 = _user32()
    modes: list[DisplayMode] = []
    seen: set[tuple[int, int, int]] = set()
    index = 0
    while True:
        devmode = DEVMODEW()
        devmode.dmSize = ctypes.sizeof(DEVMODEW)
        if not user32.EnumDisplaySettingsExW(
            device_name, index, ctypes.byref(devmode), 0
        ):
            break
        index += 1
        refresh = int(devmode.dmDisplayFrequency)
        if refresh <= 1:
            continue
        key = (int(devmode.dmPelsWidth), int(devmode.dmPelsHeight), refresh)
        if key in seen:
            continue
        seen.add(key)
        modes.append(DisplayMode(width=key[0], height=key[1], refresh_hz=key[2]))
    return modes


def scan_monitors() -> list[MonitorInfo]:
    """Enumerate attached displays with their current and supported modes."""
    monitors: list[MonitorInfo] = []
    for device in enumerate_display_devices():
        name = device.DeviceName
        current = get_current_mode(name)
        modes = enumerate_modes(name)

        if modes:
            overall_max = max(m.refresh_hz for m in modes)
            max_reading: Reading[int] = Reading(
                overall_max, unit=" Hz", source="EnumDisplaySettingsExW"
            )
        else:
            overall_max = None
            max_reading = Reading.unavailable(
                "The display driver reported no supported modes.", unit=" Hz"
            )

        if current and modes:
            at_resolution = [
                m.refresh_hz
                for m in modes
                if m.width == current.width and m.height == current.height
            ]
            best_here: Reading[int] = (
                Reading(max(at_resolution), unit=" Hz", source="EnumDisplaySettingsExW")
                if at_resolution
                else Reading.unavailable("No modes at the current resolution.", unit=" Hz")
            )
        else:
            best_here = Reading.unavailable(
                "Current display mode is unknown.", unit=" Hz"
            )

        monitors.append(
            MonitorInfo(
                device_name=name,
                friendly_name=_monitor_friendly_name(name) or device.DeviceString,
                is_primary=bool(device.StateFlags & DISPLAY_DEVICE_PRIMARY_DEVICE),
                current_mode=current,
                max_refresh_hz=max_reading,
                max_refresh_at_current_resolution=best_here,
                supported_modes=tuple(modes),
                # HDR and VRR are not exposed by this API. Reported as
                # unavailable rather than guessed (rule #64).
                hdr_enabled=Reading.unavailable(
                    "Not exposed by the Win32 display API."
                ),
                vrr_enabled=Reading.unavailable(
                    "Requires a vendor API; out of scope for v1.0."
                ),
            )
        )
    return monitors


def test_mode(device_name: str, mode: DisplayMode) -> DisplayChangeOutcome:
    """Ask Windows whether a mode would be accepted, without applying it.

    ``CDS_TEST`` validates and changes nothing — this is the dry-run for
    display changes (rule #39).

    Success is reworded from the shared message table: a dry-run that reports
    "the display settings were changed" would be actively misleading in the
    preview the operator uses to decide whether to proceed.
    """
    outcome = _change_mode(device_name, mode, flags=CDS_TEST)
    if outcome.ok:
        return DisplayChangeOutcome(
            outcome.code, f"This display supports {mode}. Nothing was changed."
        )
    return outcome


def apply_mode(device_name: str, mode: DisplayMode) -> DisplayChangeOutcome:
    """Apply a display mode and persist it to the registry."""
    return _change_mode(device_name, mode, flags=CDS_UPDATEREGISTRY)


def _change_mode(
    device_name: str, mode: DisplayMode, *, flags: int
) -> DisplayChangeOutcome:
    if not _is_windows():
        return DisplayChangeOutcome(
            DISP_CHANGE_FAILED, "Display changes require Windows."
        )

    devmode = DEVMODEW()
    devmode.dmSize = ctypes.sizeof(DEVMODEW)
    # Start from the current mode so unspecified fields keep valid values.
    _user32().EnumDisplaySettingsExW(
        device_name, ENUM_CURRENT_SETTINGS, ctypes.byref(devmode), 0
    )
    devmode.dmPelsWidth = mode.width
    devmode.dmPelsHeight = mode.height
    devmode.dmDisplayFrequency = mode.refresh_hz
    devmode.dmFields = DM_PELSWIDTH | DM_PELSHEIGHT | DM_DISPLAYFREQUENCY

    code = int(
        _user32().ChangeDisplaySettingsExW(
            device_name, ctypes.byref(devmode), None, flags, None
        )
    )
    return DisplayChangeOutcome(
        code,
        CHANGE_RESULT_MESSAGES.get(code, f"The display API returned code {code}."),
    )
