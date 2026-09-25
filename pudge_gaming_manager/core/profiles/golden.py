"""Capturing and comparing Golden Profiles.

Two operations (rules #23, #24, #44):

* **capture** — record a reference PC's configuration as a profile.
* **compare** — diff a live scan against a profile, producing findings that
  are both machine-readable and readable by a person (rule #25).

Moving a profile between machines as JSON lives in :mod:`.storage`.

Drift detection never auto-corrects. It reports, and restoring the profile
is a human action.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum

from ... import __version__
from ...hardware.models import HardwareSnapshot
from ...utilities.logging_setup import get_logger
from ...windows.power.manager import PowerManager
from .schema import (
    SCHEMA_VERSION,
    CleanupPolicy,
    DisplayPolicy,
    GamesPolicy,
    GoldenProfile,
    HardwareExpectations,
    PowerPolicy,
    ProfileOrigin,
    WindowsSettingsPolicy,
)
from . import services_drift, settings_drift
from .fixes import ProfileFixes, collect

_log = get_logger(__name__)


class DriftSeverity(str, Enum):
    MATCH = "MATCH"
    DRIFT = "DRIFT"
    UNKNOWN = "UNKNOWN"
    """The live value could not be read, so nothing can be concluded."""


@dataclass(frozen=True, slots=True)
class Difference:
    """One way this PC differs from the profile."""

    section: str
    setting: str
    expected: object
    actual: object
    severity: DriftSeverity
    detail: str = ""
    fixable: bool = False
    """True when PGM ships a tweak that could correct it."""

    def line(self) -> str:
        if self.severity is DriftSeverity.UNKNOWN:
            return f"{self.section}.{self.setting}: не удалось проверить — {self.detail}"
        return (
            f"{self.section}.{self.setting}: ожидалось {self.expected!r}, "
            f"обнаружено {self.actual!r}"
        )


@dataclass(frozen=True, slots=True)
class ComparisonResult:
    """The outcome of comparing a PC against a profile."""

    profile_name: str
    differences: tuple[Difference, ...]
    checked: int
    fixes: ProfileFixes = field(default_factory=ProfileFixes)
    """The drift PGM can correct, through the usual plan and confirmation."""

    @property
    def drifted(self) -> tuple[Difference, ...]:
        return tuple(d for d in self.differences if d.severity is DriftSeverity.DRIFT)

    @property
    def unknown(self) -> tuple[Difference, ...]:
        return tuple(d for d in self.differences if d.severity is DriftSeverity.UNKNOWN)

    @property
    def matches(self) -> bool:
        return not self.drifted

    def summary(self) -> str:
        if self.matches and not self.unknown:
            return f"Совпадает с «{self.profile_name}»."
        parts = []
        if self.drifted:
            parts.append(f"различий: {len(self.drifted)}")
        if self.unknown:
            parts.append(f"не проверено: {len(self.unknown)}")
        return f"Отличается от «{self.profile_name}»: " + ", ".join(parts)

    def to_dict(self) -> dict:
        """Machine-readable form (rule #25)."""
        return {
            "profile": self.profile_name,
            "checked": self.checked,
            "matches": self.matches,
            "differences": [
                {
                    "section": d.section,
                    "setting": d.setting,
                    "expected": d.expected,
                    "actual": d.actual,
                    "severity": d.severity.value,
                    "fixable": d.fixable,
                    "detail": d.detail,
                }
                for d in self.differences
            ],
        }


# --------------------------------------------------------------------------
# Capture
# --------------------------------------------------------------------------


def capture(
    snapshot: HardwareSnapshot,
    *,
    name: str = "Club profile",
    description: str = "",
    active_power_scheme: tuple[str, str] | None = None,
    cleanup_categories: tuple[str, ...] = (),
    keep_steam_app_ids: tuple[int, ...] = (),
    windows_settings: dict[str, str] | None = None,
    tool_version: str = __version__,
) -> GoldenProfile:
    """Build a profile from a reference PC.

    Hardware expectations are derived from what this machine *has*, which
    makes the reference PC the definition of acceptable. Values the scan
    could not read are left unset rather than guessed, so an unreadable
    figure never becomes a requirement other PCs are measured against.
    """
    gpu = snapshot.primary_gpu
    disk = snapshot.system_disk

    vram_gb = None
    if gpu is not None and gpu.vram_total_mb.value:
        vram_gb = gpu.vram_total_mb.value // 1024

    ram_gb = None
    if snapshot.ram.total_mb.value:
        # Round down to a whole GB so a 16 GB PC does not fail its own profile.
        ram_gb = int(snapshot.ram.total_mb.value // 1024)

    hardware = HardwareExpectations(
        min_ram_gb=ram_gb,
        min_vram_gb=vram_gb,
        min_free_disk_percent=20.0,
        require_ssd_system_disk=(disk.media_type == "SSD") if disk else None,
        gpu_vendor=_vendor_literal(gpu.vendor if gpu else ""),
        min_cpu_cores=snapshot.cpu.physical_cores.value,
    )

    power = PowerPolicy()
    if active_power_scheme is not None:
        power = PowerPolicy(
            scheme_guid=active_power_scheme[0], scheme_name=active_power_scheme[1]
        )

    return GoldenProfile(
        schema_version=SCHEMA_VERSION,
        name=name,
        description=description,
        origin=ProfileOrigin(
            machine_name=snapshot.os.machine_name,
            os_caption=snapshot.os.caption,
            os_build=snapshot.os.build,
            gpu_model=gpu.model if gpu else "",
            captured_at=datetime.now(tz=timezone.utc).isoformat(timespec="seconds"),
            tool_version=tool_version,
        ),
        hardware=hardware,
        power=power,
        display=DisplayPolicy(require_maximum_refresh_rate=True),
        cleanup=CleanupPolicy(enabled_categories=cleanup_categories),
        games=GamesPolicy(keep_steam_app_ids=keep_steam_app_ids),
        settings=WindowsSettingsPolicy(expected=windows_settings or {}),
    )


def capture_from_machine(
    snapshot: HardwareSnapshot,
    *,
    name: str = "Club profile",
    power: PowerManager | None = None,
) -> GoldenProfile:
    """Capture this PC, including state the hardware scan does not read.

    Calls ``powercfg`` and reads Steam's manifests, so it blocks; run it off
    the UI thread. A power plan that cannot be read is left out of the profile
    rather than guessed, and the currently-installed Steam games become the
    reset exceptions — the reference PC defines what a club reset keeps.
    """
    scheme = (power or PowerManager()).get_active_scheme()
    return capture(
        snapshot,
        name=name,
        active_power_scheme=(scheme.normalized_guid, scheme.name) if scheme else None,
        keep_steam_app_ids=_installed_steam_app_ids(),
        windows_settings=settings_drift.capture(),
        tool_version=__version__,
    )


def _installed_steam_app_ids() -> tuple[int, ...]:
    """App IDs of the games installed on this PC, or empty if Steam is absent.

    Read-only. Any failure returns an empty list rather than raising: a
    capture must not fail because Steam's files were unreadable.
    """
    from ...games.steam.library import (
        discover_libraries,
        find_steam,
        installed_games,
    )

    install = find_steam()
    if install is None:
        return ()
    try:
        games = installed_games(discover_libraries(install))
    except OSError as exc:
        _log.warning("could not enumerate Steam games for capture: %s", exc)
        return ()
    return tuple(sorted({g.app_id for g in games}))


def _vendor_literal(vendor: str):
    lowered = vendor.lower()
    for known in ("NVIDIA", "AMD", "Intel"):
        if known.lower() in lowered:
            return known
    return None


# --------------------------------------------------------------------------
# Compare
# --------------------------------------------------------------------------


def compare(
    profile: GoldenProfile,
    snapshot: HardwareSnapshot,
    *,
    active_power_guid: str | None = None,
) -> ComparisonResult:
    """Diff a live machine against a profile."""
    differences: list[Difference] = []
    checked = 0

    checked += _compare_hardware(profile, snapshot, differences)
    checked += _compare_power(profile, active_power_guid, differences)
    checked += _compare_display(profile, snapshot, differences)

    return ComparisonResult(
        profile_name=profile.name,
        differences=tuple(differences),
        checked=checked,
    )


def compare_with_machine(
    profile: GoldenProfile,
    snapshot: HardwareSnapshot,
    *,
    power: PowerManager | None = None,
) -> ComparisonResult:
    """Compare this PC, reading live state only for the sections the profile pins.

    Blocks on ``powercfg`` and ``Get-Service``; run it off the UI thread.
    """
    active_guid = None
    if profile.power.scheme_guid is not None:
        active_guid = (power or PowerManager()).get_active_guid()
    base = compare(profile, snapshot, active_power_guid=active_guid)
    extra: list[Difference] = []
    checked, fixes = settings_drift.compare(profile.settings, extra)
    checked += services_drift.compare(profile.services, extra)
    return ComparisonResult(
        profile_name=base.profile_name,
        differences=base.differences + tuple(extra),
        checked=base.checked + checked,
        fixes=collect(
            profile, snapshot, active_power_guid=active_guid, settings=fixes
        ),
    )


def _compare_hardware(
    profile: GoldenProfile, snapshot: HardwareSnapshot, out: list[Difference]
) -> int:
    expectations = profile.hardware
    checked = 0

    if expectations.min_ram_gb is not None:
        checked += 1
        total = snapshot.ram.total_mb.value
        if total is None:
            out.append(
                Difference(
                    "hardware", "min_ram_gb", expectations.min_ram_gb, None,
                    DriftSeverity.UNKNOWN,
                    "Не удалось прочитать объём памяти"
                    + (
                        f": {snapshot.ram.total_mb.unavailable_reason}"
                        if snapshot.ram.total_mb.unavailable_reason
                        else "."
                    ),
                )
            )
        elif total / 1024 < expectations.min_ram_gb - 0.5:
            out.append(
                Difference(
                    "hardware", "min_ram_gb", expectations.min_ram_gb,
                    round(total / 1024, 1), DriftSeverity.DRIFT,
                    "У этого ПК меньше памяти, чем у эталона.",
                )
            )

    if expectations.min_vram_gb is not None:
        checked += 1
        gpu = snapshot.primary_gpu
        vram = gpu.vram_total_mb.value if gpu else None
        if vram is None:
            out.append(
                Difference(
                    "hardware", "min_vram_gb", expectations.min_vram_gb, None,
                    DriftSeverity.UNKNOWN, "Не удалось прочитать видеопамять.",
                )
            )
        elif vram / 1024 < expectations.min_vram_gb:
            out.append(
                Difference(
                    "hardware", "min_vram_gb", expectations.min_vram_gb,
                    vram // 1024, DriftSeverity.DRIFT,
                    "У этой видеокарты меньше видеопамяти, чем у эталона.",
                )
            )

    if expectations.min_free_disk_percent is not None:
        checked += 1
        disk = snapshot.system_disk
        free = disk.free_percent.value if disk else None
        if free is None:
            out.append(
                Difference(
                    "hardware", "min_free_disk_percent",
                    expectations.min_free_disk_percent, None,
                    DriftSeverity.UNKNOWN, "Не удалось прочитать свободное место.",
                )
            )
        elif free < expectations.min_free_disk_percent:
            out.append(
                Difference(
                    "hardware", "min_free_disk_percent",
                    expectations.min_free_disk_percent, round(free, 1),
                    DriftSeverity.DRIFT,
                    "На системном диске меньше свободного места, чем требует профиль.",
                    fixable=True,
                )
            )

    if expectations.require_ssd_system_disk:
        checked += 1
        disk = snapshot.system_disk
        media = disk.media_type if disk else ""
        if not media:
            out.append(
                Difference(
                    "hardware", "require_ssd_system_disk", "SSD", None,
                    DriftSeverity.UNKNOWN, "Не удалось определить тип носителя.",
                )
            )
        elif media != "SSD":
            out.append(
                Difference(
                    "hardware", "require_ssd_system_disk", "SSD", media,
                    DriftSeverity.DRIFT,
                    "Системный диск — не SSD.",
                )
            )

    if expectations.gpu_vendor is not None:
        checked += 1
        gpu = snapshot.primary_gpu
        vendor = _vendor_literal(gpu.vendor if gpu else "")
        if vendor is None:
            out.append(
                Difference(
                    "hardware", "gpu_vendor", expectations.gpu_vendor, None,
                    DriftSeverity.UNKNOWN, "Не удалось определить производителя видеокарты.",
                )
            )
        elif vendor != expectations.gpu_vendor:
            out.append(
                Difference(
                    "hardware", "gpu_vendor", expectations.gpu_vendor, vendor,
                    DriftSeverity.DRIFT, "Другой производитель видеокарты.",
                )
            )

    if expectations.min_cpu_cores is not None:
        checked += 1
        cores = snapshot.cpu.physical_cores.value
        if cores is None:
            out.append(
                Difference(
                    "hardware", "min_cpu_cores", expectations.min_cpu_cores, None,
                    DriftSeverity.UNKNOWN, "Не удалось прочитать число ядер.",
                )
            )
        elif cores < expectations.min_cpu_cores:
            out.append(
                Difference(
                    "hardware", "min_cpu_cores", expectations.min_cpu_cores, cores,
                    DriftSeverity.DRIFT, "Меньше ядер ЦП, чем у эталона.",
                )
            )

    return checked


def _compare_power(
    profile: GoldenProfile, active_guid: str | None, out: list[Difference]
) -> int:
    if profile.power.scheme_guid is None:
        return 0
    if active_guid is None:
        out.append(
            Difference(
                "power", "scheme_guid", profile.power.scheme_guid, None,
                DriftSeverity.UNKNOWN, "Не удалось прочитать активную схему питания.",
            )
        )
        return 1
    if active_guid.lower() != profile.power.scheme_guid:
        out.append(
            Difference(
                "power", "scheme_guid", profile.power.scheme_guid, active_guid,
                DriftSeverity.DRIFT,
                f"Ожидалась схема «{profile.power.scheme_name or 'из профиля'}».",
                fixable=True,
            )
        )
    return 1


def _compare_display(
    profile: GoldenProfile, snapshot: HardwareSnapshot, out: list[Difference]
) -> int:
    policy = profile.display
    checked = 0

    for monitor in snapshot.monitors:
        if monitor.current_mode is None:
            continue

        if policy.require_maximum_refresh_rate:
            checked += 1
            if monitor.is_running_below_capability:
                best = monitor.max_refresh_at_current_resolution.value
                out.append(
                    Difference(
                        "display", f"refresh_hz[{monitor.friendly_name}]",
                        best, monitor.current_mode.refresh_hz,
                        DriftSeverity.DRIFT,
                        "Монитор поддерживает большую частоту обновления на этом "
                        "разрешении.",
                        fixable=True,
                    )
                )

        if policy.minimum_refresh_hz is not None:
            checked += 1
            if monitor.current_mode.refresh_hz < policy.minimum_refresh_hz:
                out.append(
                    Difference(
                        "display", f"minimum_refresh_hz[{monitor.friendly_name}]",
                        policy.minimum_refresh_hz, monitor.current_mode.refresh_hz,
                        DriftSeverity.DRIFT,
                        "Ниже минимальной частоты обновления клуба.",
                        fixable=True,
                    )
                )

    return checked
