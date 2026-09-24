"""Tests for display enumeration and refresh-rate capability logic.

The "running below capability" rule is pure logic and is tested with
constructed values, because the development machine's monitors both already
run at their maximum — the interesting case cannot be produced live without
changing the operator's screen.
"""

from __future__ import annotations

import ctypes
import os

import pytest

from pudge_gaming_manager.hardware.models import DisplayMode, MonitorInfo, Reading
from pudge_gaming_manager.hardware.monitor import display as d

windows_only = pytest.mark.skipif(os.name != "nt", reason="Windows-only behaviour")


def _monitor(
    current: DisplayMode | None, best_at_resolution: int | None
) -> MonitorInfo:
    best: Reading[int] = (
        Reading(best_at_resolution, unit=" Hz")
        if best_at_resolution is not None
        else Reading.unavailable("unknown", unit=" Hz")
    )
    return MonitorInfo(
        device_name=r"\\.\DISPLAY1",
        current_mode=current,
        max_refresh_at_current_resolution=best,
    )


# -- capability comparison -------------------------------------------------


def test_flags_monitor_running_below_its_capability() -> None:
    monitor = _monitor(DisplayMode(1920, 1080, 144), best_at_resolution=240)
    assert monitor.is_running_below_capability


def test_monitor_at_maximum_is_not_flagged() -> None:
    monitor = _monitor(DisplayMode(1920, 1080, 240), best_at_resolution=240)
    assert not monitor.is_running_below_capability


def test_comparison_is_per_resolution_not_absolute() -> None:
    """A 1440p/144 display on a panel that does 240 only at 1080p is correct.

    Comparing against the panel's absolute maximum would raise a false alarm
    and push an operator to drop the player's resolution.
    """
    monitor = MonitorInfo(
        current_mode=DisplayMode(2560, 1440, 144),
        max_refresh_hz=Reading(240, unit=" Hz"),
        max_refresh_at_current_resolution=Reading(144, unit=" Hz"),
    )
    assert not monitor.is_running_below_capability


def test_unknown_capability_is_never_flagged() -> None:
    """Missing data must not produce a warning (rule #64)."""
    assert not _monitor(DisplayMode(1920, 1080, 60), None).is_running_below_capability
    assert not _monitor(None, 240).is_running_below_capability


# -- struct layout ---------------------------------------------------------


@windows_only
def test_devmode_matches_the_sdk_layout() -> None:
    """A wrong DEVMODEW size means the API writes to the wrong offsets.

    220 bytes is the documented x64 size of the wide-character DEVMODEW with
    the display fields present.
    """
    assert ctypes.sizeof(d.DEVMODEW) == 220


# -- live enumeration ------------------------------------------------------


@windows_only
def test_enumerates_at_least_one_attached_display() -> None:
    assert d.enumerate_display_devices(), "no display attached to the desktop"


@windows_only
def test_current_mode_is_plausible() -> None:
    device = d.enumerate_display_devices()[0]
    mode = d.get_current_mode(device.DeviceName)
    assert mode is not None
    assert mode.width >= 640 and mode.height >= 480
    assert 20 <= mode.refresh_hz <= 1000


@windows_only
def test_enumerated_modes_exclude_placeholder_refresh_rates() -> None:
    """0 and 1 Hz are the documented "unspecified" values, not real rates."""
    device = d.enumerate_display_devices()[0]
    modes = d.enumerate_modes(device.DeviceName)
    assert modes
    assert all(m.refresh_hz > 1 for m in modes)


@windows_only
def test_enumerated_modes_are_deduplicated() -> None:
    device = d.enumerate_display_devices()[0]
    modes = d.enumerate_modes(device.DeviceName)
    keys = [(m.width, m.height, m.refresh_hz) for m in modes]
    assert len(keys) == len(set(keys))


@windows_only
def test_scan_reports_current_mode_among_supported_modes() -> None:
    for monitor in d.scan_monitors():
        if monitor.current_mode and monitor.supported_modes:
            assert monitor.current_mode in monitor.supported_modes


@windows_only
def test_exactly_one_primary_monitor() -> None:
    monitors = d.scan_monitors()
    assert sum(1 for m in monitors if m.is_primary) == 1


# -- dry run ---------------------------------------------------------------


@windows_only
def test_current_mode_validates_without_changing_anything() -> None:
    monitors = [m for m in d.scan_monitors() if m.current_mode]
    assert monitors
    monitor = monitors[0]
    before = d.get_current_mode(monitor.device_name)

    outcome = d.test_mode(monitor.device_name, monitor.current_mode)  # type: ignore[arg-type]

    assert outcome.ok
    # The dry-run must not claim it changed something...
    assert "settings were changed" not in outcome.message.lower()
    # ...and should say plainly that it did not.
    assert "nothing was changed" in outcome.message.lower()
    # The real guarantee: the mode on screen is untouched.
    assert d.get_current_mode(monitor.device_name) == before


@windows_only
def test_unsupported_mode_is_rejected_with_a_clear_reason() -> None:
    device = d.enumerate_display_devices()[0]
    outcome = d.test_mode(device.DeviceName, DisplayMode(1234, 567, 999))
    assert not outcome.ok
    assert outcome.code == d.DISP_CHANGE_BADMODE
    assert "not supported" in outcome.message.lower()


def test_every_documented_change_code_has_a_message() -> None:
    for code in (
        d.DISP_CHANGE_SUCCESSFUL, d.DISP_CHANGE_RESTART, d.DISP_CHANGE_FAILED,
        d.DISP_CHANGE_BADMODE, d.DISP_CHANGE_NOTUPDATED, d.DISP_CHANGE_BADFLAGS,
        d.DISP_CHANGE_BADPARAM, d.DISP_CHANGE_BADDUALVIEW,
    ):
        assert code in d.CHANGE_RESULT_MESSAGES
