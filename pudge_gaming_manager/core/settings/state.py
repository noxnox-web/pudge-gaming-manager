"""Reading where each catalogue setting stands on this PC.

Read-only. The same function feeds the settings dialog, the system audit
and the club profile's drift check, so all three agree on what "current"
means — including the cases that are not a value at all: the value is
absent and Windows uses its default, the setting does not exist on this
build, or the hardware cannot use it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from ...utilities.exceptions import PgmError
from ...windows.graphics import hags
from ...windows.registry.manager import RegistryManager
from ..optimization.tweaks.choice import flag_value
from .catalog import SETTINGS, SettingSpec, windows_build


@dataclass(frozen=True, slots=True)
class SettingState:
    """One setting as found on this PC."""

    spec: SettingSpec
    current_key: str | None
    """Option key matching the current value, ``None`` when it matches none
    (a custom value) or could not be read."""

    current_label: str
    absent: bool = False
    available: bool = True
    unavailable_reason: str = ""
    read_error: str = ""

    @property
    def matches_recommendation(self) -> bool | None:
        if self.spec.recommended is None or not self.available:
            return None
        return self.current_key == self.spec.recommended

    @property
    def recommended_label(self) -> str:
        if self.spec.recommended is None:
            return "зависит от ПК — нужен замер"
        return self.spec.option(self.spec.recommended).label


def _availability(
    spec: SettingSpec, build: int, hags_caps: hags.HagsCaps | None
) -> tuple[bool, str]:
    if spec.min_build and build and build < spec.min_build:
        return False, (
            f"нужна сборка Windows {spec.min_build} или новее, здесь {build}"
        )
    if spec.id == "hags" and hags_caps is not None and hags_caps.supported is False:
        return False, hags_caps.detail or "видеокарта не поддерживает HAGS"
    return True, ""


def read_state(
    spec: SettingSpec,
    registry: RegistryManager | None = None,
    *,
    build: int | None = None,
    hags_caps: hags.HagsCaps | None = None,
) -> SettingState:
    """Where one setting stands. Never raises."""
    registry = registry or RegistryManager()
    available, reason = _availability(
        spec, windows_build() if build is None else build, hags_caps
    )
    try:
        value = registry.read(spec.hive, spec.subkey, spec.value_name)
    except PgmError as exc:
        return SettingState(
            spec, None, "не удалось прочитать",
            available=available, unavailable_reason=reason,
            read_error=exc.reason or exc.what,
        )

    data = value.data
    absent = value.absent
    if spec.flag:
        flag = None if absent else flag_value(str(data or ""), spec.flag)
        absent = flag is None
        data = flag

    if absent:
        key = spec.absent_means if _has_option(spec, spec.absent_means) else None
        label = spec.extra.get("absent_label") or (
            spec.option(key).label if key else "не задано"
        )
        label = f"{label} (не задано, по умолчанию)"
        # HAGS absent: the driver decides; the kernel knows what it chose.
        if spec.id == "hags" and hags_caps is not None and hags_caps.enabled is not None:
            key = "on" if hags_caps.enabled else "off"
            label = f"{spec.option(key).label} (по умолчанию драйвера)"
        return SettingState(
            spec, key, label, absent=True,
            available=available, unavailable_reason=reason,
        )

    option = spec.option_for(data)
    if option is None:
        return SettingState(
            spec, None, f"своё значение: {data!r}",
            available=available, unavailable_reason=reason,
        )
    label = option.label
    # The registry holds what was requested; the kernel what is in force.
    # They differ between a change and the restart that applies it.
    if spec.id == "hags" and hags_caps is not None and hags_caps.enabled is not None:
        running = "on" if hags_caps.enabled else "off"
        if running != option.key:
            label += (
                f" — ждёт перезагрузки, сейчас работает: "
                f"{spec.option(running).label}"
            )
    return SettingState(
        spec, option.key, label,
        available=available, unavailable_reason=reason,
    )


def _has_option(spec: SettingSpec, key: str) -> bool:
    return any(o.key == key for o in spec.options)


def read_all(
    registry: RegistryManager | None = None,
    *,
    hags_query: Callable[[], hags.HagsCaps] = hags.query,
    specs: tuple[SettingSpec, ...] = SETTINGS,
) -> list[SettingState]:
    """Every catalogue setting on this PC."""
    registry = registry or RegistryManager()
    build = windows_build()
    caps = hags_query() if any(s.id == "hags" for s in specs) else None
    return [
        read_state(spec, registry, build=build, hags_caps=caps) for spec in specs
    ]


__all__ = ["SettingState", "read_all", "read_state", "windows_build"]
