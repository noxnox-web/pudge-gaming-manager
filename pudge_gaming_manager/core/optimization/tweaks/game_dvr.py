"""Turning off Game DVR's background recording.

Rationale (rule #64)
--------------------
Game DVR's background capture keeps the last stretch of gameplay
continuously encoded so it can be saved retroactively. Doing that means the
GPU's video encoder and some CPU are working the entire time a game is in
the foreground, whether or not anybody ever presses the save shortcut. On a
club PC nobody does — the clips go to a profile that is wiped between
sessions.

**What is claimed:** background capture is off, and Windows reports it off.
**What is not claimed:** a specific frame-rate gain. The cost depends on the
encoder and the title; what is certain is that work stops being done.

Two values, because there are two switches
------------------------------------------
``GameConfigStore\\GameDVR_Enabled`` is the per-user setting the Xbox Game
Bar writes. ``Policies\\...\\GameDVR\\AllowGameDVR`` is the machine-wide
policy that overrides it. Setting only the first leaves the feature able to
come back on when the Game Bar next runs, so both are offered — separately,
because they are separately reversible and one needs elevation while the
other does not.
"""

from __future__ import annotations

from typing import Any

from ....windows.registry.manager import Hive
from ..tweak import RiskLevel
from .registry_value import ABSENT, RegistryValueTweak

_ON_OFF = {0: "выключено", 1: "включено"}


class DisableGameDvrTweak(RegistryValueTweak):
    """The per-user Game Bar switch."""

    id = "gaming.game_dvr.disable_user"
    version = 1
    name = "Фоновая запись Game DVR"
    description = (
        "Выключает непрерывную фоновую запись игрового процесса для этого "
        "пользователя. Скриншоты и запись по кнопке остаются доступны."
    )
    rationale = (
        "Фоновая запись постоянно кодирует последние минуты игры, чтобы их "
        "можно было сохранить задним числом: видеокодировщик и часть "
        "процессора заняты всё время, пока игра активна, даже если запись "
        "никто ни разу не сохранит. На клубном ПК её и не сохраняют — "
        "профиль стирается между сессиями."
    )
    risk = RiskLevel.MEDIUM
    subsystem = "gaming"
    requires_admin = False

    hive = Hive.HKCU
    subkey = r"System\GameConfigStore"
    value_name = "GameDVR_Enabled"
    desired_data = 0
    value_type = "REG_DWORD"

    def describe(self, data: Any) -> str:
        if data == ABSENT:
            return "не задано (включено по умолчанию)"
        return _ON_OFF.get(data, str(data))


class DisableGameDvrPolicyTweak(RegistryValueTweak):
    """The machine-wide policy that keeps it off.

    Without this the per-user switch can be turned back on by the Game Bar,
    so on a club PC the two are applied together — but they stay two
    tweaks, because they are two settings with two rollbacks and one of
    them needs elevation.
    """

    id = "gaming.game_dvr.disable_policy"
    version = 1
    name = "Game DVR: политика для всех пользователей"
    description = (
        "Запрещает фоновую запись на уровне всей машины, чтобы она не "
        "включилась обратно у следующего пользователя."
    )
    rationale = (
        "Пользовательский переключатель Game Bar может вернуть фоновую "
        "запись обратно при следующем запуске. Машинная политика "
        "фиксирует состояние — на клубном ПК, где профили сменяются, это "
        "и есть работающая настройка."
    )
    risk = RiskLevel.MEDIUM
    subsystem = "gaming"
    requires_admin = True

    hive = Hive.HKLM
    subkey = r"SOFTWARE\Policies\Microsoft\Windows\GameDVR"
    value_name = "AllowGameDVR"
    desired_data = 0
    value_type = "REG_DWORD"

    def describe(self, data: Any) -> str:
        if data == ABSENT:
            return "не задано (разрешено)"
        return {0: "запрещено", 1: "разрешено"}.get(data, str(data))


__all__ = ["DisableGameDvrPolicyTweak", "DisableGameDvrTweak"]
