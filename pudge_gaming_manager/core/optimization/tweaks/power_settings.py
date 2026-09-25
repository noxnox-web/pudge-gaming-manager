"""Individual power settings, as tweaks.

The existing power tweak switches the whole scheme, which moves dozens of
values at once and tells the operator nothing about which of them mattered.
These two change one setting each, so the preview can name it, the
verification can read it back, and the rollback puts back exactly that.
"""

from __future__ import annotations

from ....utilities.logging_setup import get_logger
from ....windows.power import settings as power_settings
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


class PowerSettingTweak(Tweak):
    """Base for a tweak that sets one index in the active power scheme.

    Subclasses name the subgroup and setting GUIDs and the index they want.
    """

    abstract_base = True
    scope = BackupScope.POWER
    subsystem = "power"
    requires_admin = False
    """``powercfg`` edits the current user's active scheme; changing an
    index in it does not need elevation on a standard desktop."""

    subgroup: str = ""
    setting: str = ""
    desired_index: int = 0

    #: Index -> label, for the preview. Keys not listed print as numbers.
    states: dict[int, str] = {}

    def __init__(self, settings: power_settings.PowerSettings | None = None) -> None:
        self._settings = settings or power_settings.PowerSettings()

    def describe(self, index: int | None) -> str:
        if index is None:
            return "неизвестно"
        return self.states.get(index, str(index))

    # -- lifecycle ---------------------------------------------------------

    def scan(self, ctx: TweakContext) -> TweakState:
        current = self._settings.read(self.subgroup, self.setting)
        if current is None:
            # The setting is not published on this machine. Not a failure:
            # desktops and laptops expose different subgroups, and absent
            # means there is nothing here to change.
            return TweakState(
                needs_change=False,
                current_absent=True,
                summary=f"{self.name}: нет такой настройки на этом ПК",
            )
        return TweakState(
            current_value=current.ac,
            desired_value=self.desired_index,
            needs_change=current.ac != self.desired_index,
            summary=(
                f"{self.name}: {self.describe(current.ac)} -> "
                f"{self.describe(self.desired_index)}"
                if current.ac != self.desired_index
                else f"{self.name}: уже {self.describe(self.desired_index)}"
            ),
        )

    def validate(self, ctx: TweakContext, state: TweakState) -> Validation:
        if state.current_absent:
            return Validation.refuse(
                f"На этом ПК нет настройки «{self.name}»."
            )
        return Validation.allow()

    def backup(self, ctx: TweakContext, state: TweakState) -> BackupRecord:
        return BackupRecord(
            backup_id=BackupRecord.new_id(),
            tweak_id=self.id,
            tweak_version=self.version,
            scope=BackupScope.POWER,
            target=f"{self.subgroup}\\{self.setting}",
            old_value=state.current_value,
            old_value_absent=False,
            old_value_kind="POWER_INDEX",
            new_value=self.desired_index,
        )

    def apply(self, ctx: TweakContext, state: TweakState) -> ApplyResult:
        if ctx.dry_run:
            return ApplyResult(Outcome.SUCCESS, "Пробный прогон: настройка не изменена.")
        if not self._settings.set_ac_checked(
            self.subgroup, self.setting, self.desired_index
        ):
            return ApplyResult(
                Outcome.FAILED, f"powercfg не принял изменение «{self.name}»."
            )
        return ApplyResult(
            Outcome.SUCCESS,
            f"{self.name}: {self.describe(self.desired_index)}.",
        )

    def verify(self, ctx: TweakContext, state: TweakState) -> Verification:
        current = self._settings.read(self.subgroup, self.setting)
        if current is None:
            return Verification.deny(None, f"Настройку «{self.name}» не удалось перечитать.")
        if current.ac == self.desired_index:
            return Verification.confirm(
                current.ac, "powercfg подтверждает новое значение."
            )
        return Verification.deny(
            current.ac,
            f"Ожидалось {self.describe(self.desired_index)}, а powercfg "
            f"сообщает {self.describe(current.ac)}.",
        )

    def rollback(self, ctx: TweakContext, backup: BackupRecord) -> ApplyResult:
        previous = int(backup.old_value)
        if not self._settings.set_ac_checked(self.subgroup, self.setting, previous):
            return ApplyResult(
                Outcome.FAILED,
                f"Не удалось вернуть «{self.name}» в {self.describe(previous)}.",
            )
        return ApplyResult(
            Outcome.SUCCESS, f"{self.name}: восстановлено {self.describe(previous)}."
        )


class DisableUsbSelectiveSuspendTweak(PowerSettingTweak):
    """Stop Windows powering down idle USB ports.

    Rationale (rule #64)
    --------------------
    Selective suspend lets Windows put an idle USB device into a low-power
    state and wake it on use. Waking takes time, and the devices a player
    holds — mouse, keyboard, headset — go idle between actions constantly.
    The mechanism and its setting are documented by Microsoft.

    **What is claimed:** idle USB devices are no longer suspended.
    **What is not claimed:** a millisecond figure. The cost of a wake
    depends on the device and its driver; what is certain is that the
    suspend/resume cycle stops happening.

    It costs a little idle power, which on a club PC that is plugged into
    the wall is not a trade worth thinking about.
    """

    id = "power.usb.disable_selective_suspend"
    version = 1
    name = "Отключение USB-портов для экономии"
    description = (
        "Запрещает Windows усыплять простаивающие USB-устройства — мышь, "
        "клавиатуру, гарнитуру — чтобы их не приходилось будить."
    )
    rationale = (
        "Windows может переводить простаивающее USB-устройство в режим "
        "пониженного потребления и будить при обращении. Пробуждение "
        "занимает время, а устройства игрока простаивают между действиями "
        "постоянно. Механизм и настройка документированы Microsoft. Плата "
        "— немного энергии в простое, что для ПК, включённого в розетку, "
        "несущественно."
    )
    risk = RiskLevel.LOW

    subgroup = power_settings.SUBGROUP_USB
    setting = power_settings.SETTING_USB_SELECTIVE_SUSPEND
    desired_index = 0
    states = {0: "запрещено", 1: "разрешено"}


class DisablePcieAspmTweak(PowerSettingTweak):
    """Stop PCI Express links dropping into low-power states.

    Rationale (rule #64)
    --------------------
    Active State Power Management lets a PCIe link — the one the graphics
    card and the NVMe drive sit on — enter a low-power state when idle and
    come back when traffic resumes. Coming back has a latency cost, paid at
    exactly the moments a game starts needing the bus again. The setting is
    documented and exposed in the Windows power options.

    **What is claimed:** the links stay in their full-power state.
    **What is not claimed:** a frame-rate or load-time number.
    """

    id = "power.pcie.disable_aspm"
    version = 1
    name = "Энергосбережение PCI Express"
    description = (
        "Запрещает шине PCI Express уходить в энергосберегающее состояние, "
        "из которого её приходится выводить при обращении к видеокарте или "
        "накопителю."
    )
    rationale = (
        "Управление питанием канала PCI Express переводит шину в "
        "пониженное состояние при простое и возвращает при появлении "
        "трафика; возврат стоит задержки ровно в тот момент, когда игра "
        "снова обращается к видеокарте или накопителю. Настройка "
        "документирована и доступна в параметрах электропитания Windows."
    )
    risk = RiskLevel.LOW

    subgroup = power_settings.SUBGROUP_PCIE
    setting = power_settings.SETTING_PCIE_ASPM
    desired_index = 0
    states = {0: "выключено", 1: "умеренное", 2: "максимальное"}


__all__ = [
    "DisablePcieAspmTweak",
    "DisableUsbSelectiveSuspendTweak",
    "PowerSettingTweak",
]
