"""The profile's ``services`` section: expected startup types, compared.

Report-only. PGM has a service manager with a protected list, but no tweak
that changes a service's startup type, and a profile file is not where one
should come from — so drift here is shown and a person decides.

Delayed start
-------------
``Get-Service`` on Windows PowerShell 5.1 reports "Automatic" for both
automatic and automatic-delayed services: its enum has no delayed member.
So the two are compared as the same class. Reporting drift between them
would be reporting a difference this source cannot see.
"""

from __future__ import annotations

from typing import Callable

from ...windows.services.manager import ServiceInfo, ServiceManager, StartupType
from .schema import ServicePolicy

SECTION = "services"

_LABELS = {
    "Automatic": "автоматически",
    "AutomaticDelayedStart": "автоматически (отложенный запуск)",
    "Manual": "вручную",
    "Disabled": "отключена",
}


def _class(startup: str) -> str:
    return "Automatic" if startup in ("Automatic", "AutomaticDelayedStart") else startup


def compare(
    policy: ServicePolicy,
    out: list,
    *,
    services: Callable[[], list[ServiceInfo]] | None = None,
) -> int:
    """Append a :class:`Difference` per drifted or missing service."""
    from .golden import Difference, DriftSeverity  # golden imports this module

    if not policy.expected:
        return 0
    listed = (services or (lambda: ServiceManager().list_services()))()
    by_name = {s.name.lower(): s for s in listed}
    for name, expectation in policy.expected.items():
        wanted = expectation.startup_type
        wanted_label = _LABELS.get(wanted, wanted)
        info = by_name.get(name.lower())
        if info is None:
            out.append(
                Difference(
                    SECTION, name, wanted_label, None, DriftSeverity.UNKNOWN,
                    f"Служба «{name}» на этом ПК не найдена"
                    + ("" if listed else " (список служб не прочитан)") + ".",
                )
            )
            continue
        actual = info.startup_type
        if actual is StartupType.UNKNOWN:
            out.append(
                Difference(
                    SECTION, name, wanted_label, None, DriftSeverity.UNKNOWN,
                    f"Тип запуска службы «{info.display_name or name}» не прочитан.",
                )
            )
            continue
        if _class(actual.value) != _class(wanted):
            out.append(
                Difference(
                    SECTION, name, wanted_label, _LABELS.get(actual.value, actual.value),
                    DriftSeverity.DRIFT,
                    f"Служба «{info.display_name or name}»: меняется вручную "
                    "в «Службах» Windows.",
                )
            )
    return len(policy.expected)


__all__ = ["SECTION", "compare"]
