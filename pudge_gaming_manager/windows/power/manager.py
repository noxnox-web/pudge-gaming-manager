"""Power scheme management via ``powercfg``.

Localisation
------------
``powercfg`` output is translated. On the development machine it is Russian.
Every parser here keys on **GUIDs and the ``*`` active marker**, never on
English words like "Balanced" — a keyword parser would silently find nothing
on a localised club PC and report the machine as having no power schemes.

Scheme GUIDs are stable across Windows versions and locales; they are
documented by Microsoft and are the reliable identifier.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from ...utilities.command_runner import CommandRunner
from ...utilities.exceptions import CommandFailedError, PgmError
from ...utilities.logging_setup import get_logger

_log = get_logger(__name__)

_GUID_PATTERN = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)

#: Name shown in parentheses after the GUID, e.g. "... (Balanced)".
_TRAILING_NAME = re.compile(r"\(([^)]*)\)\s*\*?\s*$")


class BuiltInScheme(str, Enum):
    """Power scheme GUIDs documented by Microsoft.

    Stable across Windows versions and locales.
    """

    BALANCED = "381b4222-f694-41f0-9685-ff5bb260df2e"
    HIGH_PERFORMANCE = "8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c"
    POWER_SAVER = "a1841308-3541-4fab-bc81-f71556f20b4a"
    ULTIMATE_PERFORMANCE = "e9a42b02-d5df-448d-aa00-03f14749eb61"
    """Not present by default on most installations; must be checked for."""


@dataclass(frozen=True, slots=True)
class PowerScheme:
    guid: str
    name: str
    is_active: bool = False

    @property
    def normalized_guid(self) -> str:
        return self.guid.lower()


class PowerManager:
    """Reads and changes the active Windows power scheme."""

    def __init__(self, runner: CommandRunner | None = None) -> None:
        self.runner = runner or CommandRunner()

    # -- reading (no privilege required) -----------------------------------

    def list_schemes(self) -> list[PowerScheme]:
        """Return every power scheme known to this machine.

        Returns an empty list rather than raising when ``powercfg`` is
        unavailable, so a scan degrades instead of failing.
        """
        result = self.runner.try_run(["powercfg", "/list"], timeout_s=20)
        if result is None or not result.ok:
            _log.warning("powercfg /list unavailable")
            return []
        return self._parse_scheme_list(result.stdout)

    @staticmethod
    def _parse_scheme_list(output: str) -> list[PowerScheme]:
        schemes: list[PowerScheme] = []
        for line in output.splitlines():
            match = _GUID_PATTERN.search(line)
            if not match:
                continue
            guid = match.group(0).lower()
            # The active scheme is marked with a trailing asterisk. This is
            # the only locale-independent signal available.
            is_active = line.rstrip().endswith("*")
            name_match = _TRAILING_NAME.search(line.rstrip())
            name = name_match.group(1).strip() if name_match else ""
            schemes.append(PowerScheme(guid=guid, name=name, is_active=is_active))
        return schemes

    def get_active_guid(self) -> str | None:
        """Return the active scheme's GUID, or ``None`` if it cannot be read."""
        result = self.runner.try_run(["powercfg", "/getactivescheme"], timeout_s=20)
        if result is None or not result.ok:
            return None
        match = _GUID_PATTERN.search(result.stdout)
        return match.group(0).lower() if match else None

    def get_active_scheme(self) -> PowerScheme | None:
        guid = self.get_active_guid()
        if guid is None:
            return None
        for scheme in self.list_schemes():
            if scheme.normalized_guid == guid:
                return scheme
        return PowerScheme(guid=guid, name="", is_active=True)

    def scheme_exists(self, guid: str) -> bool:
        target = guid.lower()
        return any(s.normalized_guid == target for s in self.list_schemes())

    # -- writing -----------------------------------------------------------

    def set_active(self, guid: str) -> None:
        """Make ``guid`` the active power scheme.

        Raises:
            PgmError: The scheme does not exist, or ``powercfg`` refused.
        """
        target = guid.lower()
        if not _GUID_PATTERN.fullmatch(target):
            raise PgmError(
                what=f"Не удалось переключиться на схему питания «{guid}»",
                reason="Это значение не является идентификатором схемы питания.",
                remedy="Выберите схему питания из списка в настройках.",
            )
        if not self.scheme_exists(target):
            raise PgmError(
                what="Не удалось переключить схему питания",
                reason=f"На этом ПК нет схемы питания с идентификатором {target}.",
                remedy=(
                    "Выберите схему, которая есть на этой машине. "
                    "«Максимальная производительность» отсутствует, пока её "
                    "не добавят вручную."
                ),
            )

        try:
            self.runner.run(
                ["powercfg", "/setactive", target],
                timeout_s=20,
                check=True,
                remedy="Запустите Pudge Cleaner от имени администратора.",
            )
        except CommandFailedError as exc:
            raise PgmError(
                what="Не удалось сменить схему питания",
                reason=exc.reason,
                remedy=(
                    exc.remedy
                    or "Запустите Pudge Cleaner от имени администратора."
                ),
                context={"guid": target},
            ) from exc
