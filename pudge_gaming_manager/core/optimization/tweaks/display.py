"""Refresh-rate tweak.

Rationale (rule #64)
--------------------
A display attached at a lower refresh rate than it supports renders fewer
frames to the panel regardless of what the GPU produces. On a club PC this
is usually an accident: a driver reinstall or a cable change resets the mode
to 60 Hz and nobody notices.

**What is claimed:** the display's refresh rate changes, and Windows
confirms it. **What is not claimed:** a frame-rate improvement — the GPU was
already rendering; the panel simply shows more of it.

The change is reversible: the previous mode is captured before the switch
and reapplied on rollback. ``CDS_TEST`` validates the target mode before
anything is applied, so an unsupported mode is refused rather than attempted.
"""

from __future__ import annotations

from ....hardware.models import DisplayMode
from ....hardware.monitor import display
from ....utilities.logging_setup import get_logger
from ..tweak import (
    ApplyResult,
    BackupRecord,
    BackupScope,
    Outcome,
    RiskLevel,
    Tweak,
    TweakContext,
    TweakState,
    Validation,
    Verification,
)

_log = get_logger(__name__)


def _encode(mode: DisplayMode) -> str:
    return f"{mode.width}x{mode.height}@{mode.refresh_hz}"


def _decode(text: str) -> DisplayMode | None:
    try:
        resolution, refresh = text.split("@")
        width, height = resolution.split("x")
        return DisplayMode(int(width), int(height), int(refresh))
    except (ValueError, AttributeError):
        return None


class SetRefreshRateTweak(Tweak):
    """Raise a display to the highest refresh rate it supports.

    The target is the maximum available **at the current resolution**.
    Choosing the panel's absolute maximum could silently drop a player from
    1440p to 1080p, which is a worse trade than a lower refresh rate and is
    not a decision this tweak is entitled to make.
    """

    id = "display.refresh_rate.maximise"
    version = 1
    name = "Use the display's highest refresh rate"
    description = (
        "Sets the monitor to the fastest refresh rate it supports at the "
        "resolution it is already running."
    )
    rationale = (
        "A panel attached below its supported refresh rate shows fewer "
        "frames than the GPU renders. Resolution is deliberately left "
        "unchanged."
    )
    risk = RiskLevel.LOW
    subsystem = "display"
    scope = BackupScope.DISPLAY
    requires_admin = False

    def __init__(self, device_name: str, friendly_name: str = "") -> None:
        self.device_name = device_name
        self.friendly_name = friendly_name or device_name

    # -- lifecycle ---------------------------------------------------------

    def scan(self, ctx: TweakContext) -> TweakState:
        current = display.get_current_mode(self.device_name)
        if current is None:
            return TweakState(
                needs_change=False,
                current_absent=True,
                summary=f"{self.friendly_name}: current mode unknown",
            )

        modes = display.enumerate_modes(self.device_name)
        at_resolution = [
            m.refresh_hz
            for m in modes
            if m.width == current.width and m.height == current.height
        ]
        best = max(at_resolution) if at_resolution else current.refresh_hz
        target = DisplayMode(current.width, current.height, best)

        return TweakState(
            current_value=_encode(current),
            desired_value=_encode(target),
            needs_change=best > current.refresh_hz,
            summary=(
                f"{self.friendly_name}: {current.refresh_hz} Hz -> {best} Hz"
                if best > current.refresh_hz
                else f"{self.friendly_name}: already at {current.refresh_hz} Hz"
            ),
        )

    def validate(self, ctx: TweakContext, state: TweakState) -> Validation:
        if state.current_absent:
            return Validation.refuse(
                "The current display mode could not be read, so it could not "
                "be restored afterwards."
            )
        target = _decode(str(state.desired_value))
        if target is None:
            return Validation.refuse("The target display mode is not valid.")

        # The documented dry run for display changes: Windows validates the
        # mode without applying it.
        outcome = display.test_mode(self.device_name, target)
        if not outcome.ok:
            return Validation.refuse(outcome.message)
        return Validation.allow()

    def backup(self, ctx: TweakContext, state: TweakState) -> BackupRecord:
        return BackupRecord(
            backup_id=BackupRecord.new_id(),
            tweak_id=self.id,
            tweak_version=self.version,
            scope=BackupScope.DISPLAY,
            target=self.device_name,
            old_value=state.current_value,
            old_value_absent=False,
            old_value_kind="DISPLAY_MODE",
            new_value=state.desired_value,
        )

    def apply(self, ctx: TweakContext, state: TweakState) -> ApplyResult:
        target = _decode(str(state.desired_value))
        if target is None:
            return ApplyResult(Outcome.FAILED, "The target display mode is not valid.")

        outcome = display.apply_mode(self.device_name, target)
        if not outcome.ok and not outcome.needs_restart:
            return ApplyResult(Outcome.FAILED, outcome.message)
        return ApplyResult(
            Outcome.SUCCESS, outcome.message, needs_restart=outcome.needs_restart
        )

    def verify(self, ctx: TweakContext, state: TweakState) -> Verification:
        current = display.get_current_mode(self.device_name)
        if current is None:
            return Verification.deny(None, "The display mode could not be re-read.")
        observed = _encode(current)
        if observed == state.desired_value:
            return Verification.confirm(observed, "Windows confirms the new mode.")
        return Verification.deny(
            observed,
            f"Expected {state.desired_value} but the display reports {observed}.",
        )

    def rollback(self, ctx: TweakContext, backup: BackupRecord) -> ApplyResult:
        previous = _decode(str(backup.old_value))
        if previous is None:
            return ApplyResult(
                Outcome.FAILED, f"Saved display mode '{backup.old_value}' is unreadable."
            )

        outcome = display.apply_mode(self.device_name, previous)
        if not outcome.ok:
            return ApplyResult(Outcome.FAILED, outcome.message)

        restored = display.get_current_mode(self.device_name)
        if restored is None or _encode(restored) != backup.old_value:
            return ApplyResult(
                Outcome.FAILED,
                f"Tried to restore {backup.old_value} but the display reports "
                f"{_encode(restored) if restored else 'nothing'}.",
            )
        return ApplyResult(Outcome.SUCCESS, f"Display mode restored to {backup.old_value}.")


def tweaks_for_underperforming_displays(monitors) -> list[SetRefreshRateTweak]:
    """Build one tweak per display running below its capability."""
    return [
        SetRefreshRateTweak(m.device_name, m.friendly_name)
        for m in monitors
        if m.is_running_below_capability
    ]
