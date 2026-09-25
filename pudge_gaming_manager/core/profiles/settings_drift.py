"""The profile's «Настройки Windows» section: capture, compare, correct.

Split from ``golden.py`` because this section differs from the others in one
way that matters: every setting in it has a tweak PGM can apply. So drift
here is not only reported — :func:`fixes` turns it into the exact choices
the settings service plans, and the operator can bring a PC back to the
club's reference through the same preview → backup → verify path as any
other change. Nothing is corrected without that preview.
"""

from __future__ import annotations

from typing import Callable

from ..settings.catalog import SETTINGS_BY_ID
from ..settings.state import SettingState, read_all
from .schema import WindowsSettingsPolicy

SECTION = "settings"


def capture(states: Callable[[], list[SettingState]] = read_all) -> dict[str, str]:
    """This PC's settings as profile expectations.

    Only settings that are available here and read as one of their known
    options are captured: a custom value or an unreadable one is not a
    reference other PCs should be held to.
    """
    return {
        s.spec.id: s.current_key
        for s in states()
        if s.available and s.current_key is not None and not s.read_error
    }


def compare(
    policy: WindowsSettingsPolicy,
    out: list,
    *,
    states: Callable[[], list[SettingState]] = read_all,
) -> tuple[int, dict[str, str]]:
    """Append a :class:`Difference` per drifted or unreadable setting.

    Returns ``(checked, fixes)`` where ``fixes`` maps setting id to the
    profile's option for every setting that drifted and can be changed here.
    """
    from .golden import Difference, DriftSeverity  # golden imports this module

    if not policy.expected:
        return 0, {}
    by_id = {s.spec.id: s for s in states()}
    checked = 0
    fixes: dict[str, str] = {}
    for setting_id, option_key in policy.expected.items():
        spec = SETTINGS_BY_ID[setting_id]
        expected_label = spec.option(option_key).label
        state = by_id.get(setting_id)
        checked += 1
        if state is None or state.read_error:
            out.append(
                Difference(
                    SECTION, setting_id, expected_label, None, DriftSeverity.UNKNOWN,
                    f"{spec.name}: " + (state.read_error if state else "не прочитано"),
                )
            )
            continue
        if not state.available:
            out.append(
                Difference(
                    SECTION, setting_id, expected_label, None, DriftSeverity.UNKNOWN,
                    f"{spec.name}: недоступно на этом ПК — {state.unavailable_reason}",
                )
            )
            continue
        if state.current_key == option_key:
            continue
        out.append(
            Difference(
                SECTION, setting_id, expected_label, state.current_label,
                DriftSeverity.DRIFT, spec.name, fixable=True,
            )
        )
        fixes[setting_id] = option_key
    return checked, fixes


__all__ = ["SECTION", "capture", "compare"]
