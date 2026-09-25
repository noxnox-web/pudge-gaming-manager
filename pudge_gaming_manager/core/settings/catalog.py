"""Every Windows setting PGM can show, with the case for and against it.

Where these came from
---------------------
A set of Windows gaming-optimisation guides, which disagree with each other
on several points. The disagreements are the reason this file exists in this
shape: a setting is not reduced to "apply" or "don't". Each one carries

* the options Windows actually accepts, as the raw registry data;
* what Windows does when the value is absent (its real default);
* a recommendation — or ``None`` when there honestly is no universal one;
* why, in words an operator can check, including where sources conflict;
* the risk, whether a restart or sign-out is needed, the minimum build, and
  what hardware it depends on.

Three tiers
-----------
``RECOMMENDED``
    Offered by ОПТИМИЗИРОВАТЬ ПК, with the recommended option, through the
    ordinary preview. Only settings every source agrees on.
``ADVANCED``
    Shown in the «Настройки Windows» dialog with the current state and the
    reasoning. Nothing is chosen for the operator.
``EXPERIMENTAL``
    Same dialog, separate section. Registry values with a documented
    mechanism but disputed or hardware-dependent effect. Never in any preset.

Nothing here writes. Reading is :mod:`.state`; changing is a
:class:`~..optimization.tweaks.choice.RegistryChoiceTweak`, which backs the
old value up and verifies the new one like every other tweak.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from ...windows.registry.manager import Hive
from ..optimization.tweak import RiskLevel


class Tier(str, Enum):
    RECOMMENDED = "RECOMMENDED"
    ADVANCED = "ADVANCED"
    EXPERIMENTAL = "EXPERIMENTAL"

    @property
    def label(self) -> str:
        return {
            Tier.RECOMMENDED: "Рекомендуемые",
            Tier.ADVANCED: "Расширенные",
            Tier.EXPERIMENTAL: "Экспериментальные",
        }[self]


class Activation(str, Enum):
    """When Windows starts honouring a new value."""

    IMMEDIATE = "IMMEDIATE"
    SIGN_OUT = "SIGN_OUT"
    RESTART = "RESTART"

    @property
    def label(self) -> str:
        return {
            Activation.IMMEDIATE: "сразу",
            Activation.SIGN_OUT: "после повторного входа в Windows",
            Activation.RESTART: "после перезагрузки",
        }[self]


@dataclass(frozen=True, slots=True)
class Option:
    """One value the setting can take."""

    key: str
    label: str
    data: Any


@dataclass(frozen=True, slots=True)
class SettingSpec:
    """One registry-backed Windows setting."""

    id: str
    name: str
    tier: Tier
    hive: Hive
    subkey: str
    value_name: str
    value_type: str
    options: tuple[Option, ...]
    absent_means: str
    """The option key Windows behaves as when the value does not exist."""

    what: str
    """What changes, mechanically."""

    why: str
    """The case for the recommended option — or for having no recommendation."""

    risk: RiskLevel
    activation: Activation = Activation.IMMEDIATE
    recommended: str | None = None
    """Option key to recommend, or ``None`` when it depends on the PC."""

    conflict: str = ""
    """Where the sources disagree, stated rather than resolved by fiat."""

    min_build: int = 0
    hardware: str = "любое"
    flag: str = ""
    """For a ``key=value;`` string setting, the one key this spec owns."""

    extra: dict[str, Any] = field(default_factory=dict)

    def option(self, key: str) -> Option:
        for option in self.options:
            if option.key == key:
                return option
        raise KeyError(f"{self.id}: нет варианта {key!r}")

    def option_for(self, data: Any) -> Option | None:
        for option in self.options:
            if option.data == data:
                return option
        return None

    @property
    def tweak_id(self) -> str:
        return f"setting.{self.id}"


_GAMEBAR = r"Software\Microsoft\GameBar"
_PERSONALIZE = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
_WINDOW_METRICS = r"Control Panel\Desktop\WindowMetrics"
_DIRECTX_PREFS = r"Software\Microsoft\DirectX\UserGpuPreferences"
_GRAPHICS_DRIVERS = r"SYSTEM\CurrentControlSet\Control\GraphicsDrivers"
_SYSTEM_PROFILE = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Multimedia\SystemProfile"
_GAMES_TASK = _SYSTEM_PROFILE + r"\Tasks\Games"
_PRIORITY_CONTROL = r"SYSTEM\CurrentControlSet\Control\PriorityControl"

_ON_OFF_DWORD = (Option("on", "включено", 1), Option("off", "выключено", 0))

#: Windows 11's first build. Settings introduced with it are ignored below.
WINDOWS_11 = 22000


SETTINGS: tuple[SettingSpec, ...] = (
    # -- recommended: every source agrees ------------------------------------
    SettingSpec(
        id="windowed_game_optimizations",
        name="Оптимизации для игр в окне",
        tier=Tier.RECOMMENDED,
        hive=Hive.HKCU,
        subkey=_DIRECTX_PREFS,
        value_name="DirectXUserGlobalSettings",
        value_type="REG_SZ",
        flag="SwapEffectUpgradeEnable",
        options=(Option("on", "включено", "1"), Option("off", "выключено", "0")),
        absent_means="off",
        recommended="on",
        what=(
            "Переводит игры DirectX 10/11 в окне и в окне без рамки на "
            "flip-модель вывода кадров — ту же, что у полноэкранного режима."
        ),
        why=(
            "Со старой blt-моделью кадр копируется через композитор рабочего "
            "стола, что добавляет задержку и мешает VRR. Flip-модель убирает "
            "копию; переключатель — «Параметры → Дисплей → Графика → "
            "Оптимизации для игр в окне». Остальные настройки в этой строке "
            "реестра не трогаются."
        ),
        risk=RiskLevel.LOW,
        activation=Activation.IMMEDIATE,
        min_build=WINDOWS_11,
        hardware="любая видеокарта с DirectX 11",
    ),
    SettingSpec(
        id="transparency",
        name="Эффекты прозрачности",
        tier=Tier.RECOMMENDED,
        hive=Hive.HKCU,
        subkey=_PERSONALIZE,
        value_name="EnableTransparency",
        value_type="REG_DWORD",
        options=_ON_OFF_DWORD,
        absent_means="on",
        recommended="off",
        what="Отключает размытие и прозрачность панели задач, меню «Пуск» и окон.",
        why=(
            "Прозрачность рисует композитор рабочего стола на видеокарте "
            "постоянно, в том числе поверх игры в окне. Пользы для игры нет, "
            "это только оформление."
        ),
        risk=RiskLevel.MEDIUM,
        activation=Activation.IMMEDIATE,
    ),
    SettingSpec(
        id="window_animations",
        name="Анимация окон",
        tier=Tier.RECOMMENDED,
        hive=Hive.HKCU,
        subkey=_WINDOW_METRICS,
        value_name="MinAnimate",
        value_type="REG_SZ",
        options=(Option("on", "включено", "1"), Option("off", "выключено", "0")),
        absent_means="on",
        recommended="off",
        what="Отключает анимацию сворачивания и разворачивания окон.",
        why=(
            "Альт-таб из игры и обратно проходит без анимации, окно "
            "появляется сразу. Чистое оформление, ничего кроме него не "
            "меняется."
        ),
        risk=RiskLevel.MEDIUM,
        activation=Activation.SIGN_OUT,
    ),
    # -- advanced: real, but the right value depends on the PC ---------------
    SettingSpec(
        id="game_mode",
        name="Игровой режим Windows",
        tier=Tier.ADVANCED,
        hive=Hive.HKCU,
        subkey=_GAMEBAR,
        value_name="AutoGameModeEnabled",
        value_type="REG_DWORD",
        options=_ON_OFF_DWORD,
        absent_means="on",
        recommended="on",
        what=(
            "Когда игра активна, Windows откладывает фоновые задачи "
            "обновлений и уведомления и отдаёт игре приоритет планировщика."
        ),
        why=(
            "Базовое значение — включено: так Windows поставляется, и на "
            "процессорах AMD Ryzen X3D с двумя кристаллами драйвер AMD "
            "опирается на игровой режим, чтобы увести игру на кристалл с "
            "3D-кэшем. Выключать стоит только если на конкретном ПК замер "
            "показал хуже — поэтому здесь это выбор, а не автоматическое "
            "действие."
        ),
        conflict=(
            "Источники расходятся: одни советуют включить, другие — "
            "выключить. Советы выключить в основном опираются на жалобы на "
            "статтеры в ранних сборках Windows 10 (2017–2018)."
        ),
        risk=RiskLevel.MEDIUM,
        activation=Activation.IMMEDIATE,
    ),
    SettingSpec(
        id="hags",
        name="Аппаратное планирование GPU (HAGS)",
        tier=Tier.ADVANCED,
        hive=Hive.HKLM,
        subkey=_GRAPHICS_DRIVERS,
        value_name="HwSchMode",
        value_type="REG_DWORD",
        options=(Option("on", "включено", 2), Option("off", "выключено", 1)),
        absent_means="driver",
        recommended=None,
        what=(
            "Передаёт планирование очередей GPU планировщику видеокарты "
            "вместо Windows."
        ),
        why=(
            "Универсального ответа нет. На NVIDIA RTX 40 и новее HAGS нужен "
            "для генерации кадров DLSS 3; на других картах замеры расходятся "
            "в обе стороны в зависимости от драйвера и игры. Сначала "
            "проверяется, поддерживает ли его видеокарта; дальше — замер на "
            "своём ПК в обоих положениях."
        ),
        conflict="Источники рекомендуют противоположное.",
        risk=RiskLevel.MEDIUM,
        activation=Activation.RESTART,
        min_build=19041,
        hardware="видеокарта и драйвер с WDDM 2.7 и поддержкой HAGS",
        extra={"absent_label": "по умолчанию драйвера"},
    ),
    # -- experimental: documented mechanism, disputed effect -----------------
    SettingSpec(
        id="network_throttling",
        name="NetworkThrottlingIndex",
        tier=Tier.EXPERIMENTAL,
        hive=Hive.HKLM,
        subkey=_SYSTEM_PROFILE,
        value_name="NetworkThrottlingIndex",
        value_type="REG_DWORD",
        options=(
            Option("default", "10 пакетов/мс (Windows)", 10),
            Option("off", "ограничение выключено", 0xFFFFFFFF),
        ),
        absent_means="default",
        recommended=None,
        what=(
            "Пока работает мультимедийная задача MMCSS, Windows ограничивает "
            "прочий сетевой трафик примерно 10 пакетами в миллисекунду."
        ),
        why=(
            "Механизм документирован Microsoft, но предел упирается только "
            "при очень большом потоке мелких пакетов; для обычной онлайн-игры "
            "эффекта может не быть вовсе. Меняйте, если замер показал разницу."
        ),
        risk=RiskLevel.MEDIUM,
        activation=Activation.RESTART,
    ),
    SettingSpec(
        id="system_responsiveness",
        name="SystemResponsiveness",
        tier=Tier.EXPERIMENTAL,
        hive=Hive.HKLM,
        subkey=_SYSTEM_PROFILE,
        value_name="SystemResponsiveness",
        value_type="REG_DWORD",
        options=(
            Option("default", "20% (Windows)", 20),
            Option("low", "10%", 10),
        ),
        absent_means="default",
        recommended=None,
        what=(
            "Доля процессора, которую MMCSS оставляет фоновым задачам, пока "
            "работают потоки мультимедиа."
        ),
        why=(
            "Действует только на потоки, зарегистрированные в MMCSS (звук, "
            "часть видеоплееров), а большинство игр там не регистрируются. "
            "По документации Microsoft значения ниже 10 приравниваются к 20, "
            "поэтому 0 из гайдов даёт обратный обещанному результат."
        ),
        risk=RiskLevel.MEDIUM,
        activation=Activation.RESTART,
    ),
    SettingSpec(
        id="mmcss_games_scheduling",
        name="MMCSS «Games»: Scheduling Category",
        tier=Tier.EXPERIMENTAL,
        hive=Hive.HKLM,
        subkey=_GAMES_TASK,
        value_name="Scheduling Category",
        value_type="REG_SZ",
        options=(
            Option("default", "Medium (Windows)", "Medium"),
            Option("high", "High", "High"),
        ),
        absent_means="default",
        recommended=None,
        what="Категория планирования MMCSS для задачи «Games».",
        why=(
            "Действует только на потоки, которые игра сама зарегистрировала "
            "в MMCSS под задачей «Games», — таких игр немного. High поднимает "
            "их до приоритетов 23–26, выше почти всего в системе. Остальные "
            "значения этой задачи из гайдов не предлагаются: по документации "
            "Microsoft «GPU Priority» и «SFIO Priority» не используются, а "
            "«Priority» при категории High всегда считается равным 2."
        ),
        risk=RiskLevel.MEDIUM,
        activation=Activation.RESTART,
    ),
    SettingSpec(
        id="win32_priority_separation",
        name="Win32PrioritySeparation",
        tier=Tier.EXPERIMENTAL,
        hive=Hive.HKLM,
        subkey=_PRIORITY_CONTROL,
        value_name="Win32PrioritySeparation",
        value_type="REG_DWORD",
        options=(
            Option("default", "2 (Windows)", 2),
            Option("short_variable", "0x26: короткие кванты, буст активного окна", 0x26),
        ),
        absent_means="default",
        recommended=None,
        what=(
            "Длина кванта планировщика и то, насколько активное окно "
            "получает больше процессорного времени, чем фоновые."
        ),
        why=(
            "Меняет поведение планировщика для всех процессов сразу. "
            "Значение 0x26 — самое частое в гайдах, но результаты замеров "
            "противоречивы и зависят от процессора и игры."
        ),
        conflict="Гайды предлагают разные «магические» значения (0x26, 0x28, 0x18).",
        risk=RiskLevel.HIGH,
        activation=Activation.IMMEDIATE,
    ),
)

SETTINGS_BY_ID: dict[str, SettingSpec] = {s.id: s for s in SETTINGS}
SETTINGS_BY_TWEAK_ID: dict[str, SettingSpec] = {s.tweak_id: s for s in SETTINGS}


def windows_build() -> int:
    """The running build number, or 0 when it cannot be determined."""
    try:
        return int(sys.getwindowsversion().build)  # type: ignore[attr-defined]
    except (AttributeError, ValueError):
        return 0


def by_tier(tier: Tier) -> tuple[SettingSpec, ...]:
    return tuple(s for s in SETTINGS if s.tier is tier)


__all__ = [
    "SETTINGS",
    "SETTINGS_BY_ID",
    "SETTINGS_BY_TWEAK_ID",
    "WINDOWS_11",
    "Activation",
    "Option",
    "SettingSpec",
    "Tier",
    "by_tier",
    "windows_build",
]
