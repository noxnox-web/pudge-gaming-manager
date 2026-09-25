"""What of a profile's drift PGM can put right, and how.

Comparing never corrects by itself. This module only answers "which of
these differences has a tweak behind it", so the comparison window can offer
one explicit button — and the tweaks it names then go through the ordinary
plan, confirmation, backup and verification like any other change.

Three sections are correctable:

* **settings** — each drifted catalogue setting, to the profile's option;
* **power** — the profile's scheme, by GUID. A scheme created by duplicating
  one (every "Ultimate Performance" is such a copy) has a GUID unique to the
  machine it was made on, so on another PC the tweak's own validation will
  say the scheme does not exist rather than switch to something else;
* **display** — a monitor below its maximum refresh rate at the current
  resolution, raised to that maximum.

Hardware expectations are never correctable: PGM cannot install RAM.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ...hardware.models import HardwareSnapshot
from .schema import GoldenProfile


@dataclass(frozen=True, slots=True)
class ProfileFixes:
    settings: dict[str, str] = field(default_factory=dict)
    """Setting id -> the profile's option key."""

    power_scheme: tuple[str, str] | None = None
    """``(guid, name)`` to switch to, when the active scheme differs."""

    displays: tuple[tuple[str, str], ...] = ()
    """``(device_name, friendly_name)`` of monitors to raise to maximum."""

    cpu_model: str = ""
    """Passed to the power tweak, which refuses on dual-CCD Ryzen X3D."""

    @property
    def count(self) -> int:
        return len(self.settings) + bool(self.power_scheme) + len(self.displays)

    def covers(self, section: str, setting: str) -> bool:
        """True when the difference ``section.setting`` is in this fix."""
        if section == "settings":
            return setting in self.settings
        if section == "power":
            return self.power_scheme is not None
        if section == "display":
            return any(
                setting.endswith(f"[{friendly}]") for _device, friendly in self.displays
            )
        return False


def collect(
    profile: GoldenProfile,
    snapshot: HardwareSnapshot,
    *,
    active_power_guid: str | None,
    settings: dict[str, str],
) -> ProfileFixes:
    """The fixes for a comparison of ``snapshot`` against ``profile``."""
    power = None
    wanted = profile.power.scheme_guid
    if wanted is not None and active_power_guid is not None:
        if active_power_guid.lower() != wanted:
            power = (wanted, profile.power.scheme_name or wanted)

    displays: list[tuple[str, str]] = []
    policy = profile.display
    for monitor in snapshot.monitors:
        mode = monitor.current_mode
        if mode is None or not monitor.is_running_below_capability:
            continue
        below_minimum = (
            policy.minimum_refresh_hz is not None
            and mode.refresh_hz < policy.minimum_refresh_hz
        )
        if policy.require_maximum_refresh_rate or below_minimum:
            displays.append((monitor.device_name, monitor.friendly_name))

    return ProfileFixes(
        settings=dict(settings),
        power_scheme=power,
        displays=tuple(displays),
        cpu_model=snapshot.cpu.model,
    )


__all__ = ["ProfileFixes", "collect"]
