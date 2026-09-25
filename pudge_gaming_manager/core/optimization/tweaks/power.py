"""Power-plan tweaks.

Concrete tweaks live in ``core`` because a Tweak participates in the engine
lifecycle, which is orchestration. ``windows/power/manager.py`` provides the
raw capability; this module decides when and why to use it. The import-graph
test rejects the reverse arrangement.

Rationale (rule #64)
--------------------
The Windows power scheme controls processor performance-state policy. Under
the Balanced scheme the processor is allowed to drop to a low performance
state and ramps back up in response to load, which adds latency to bursty
workloads. The High Performance scheme holds a higher minimum processor
state. This is a documented Windows power-policy behaviour, not a folk tweak.

**What is claimed:** the active power scheme changes, and PGM verifies it.
**What is not claimed:** a specific frame-rate improvement. That is measured
separately (rule #65).

The change is fully reversible: the previous scheme GUID is recorded before
the switch and restored on rollback.
"""

from __future__ import annotations

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
from ....utilities.exceptions import PgmError
from ....utilities.logging_setup import get_logger
from ....windows.power.manager import BuiltInScheme, PowerManager
from ....windows.power.settings import (
    SETTING_PROCESSOR_MIN_STATE,
    SUBGROUP_PROCESSOR,
    PowerSettings,
)

_log = get_logger(__name__)

#: The minimum processor state, in percent, that High Performance holds and
#: that this switch exists to obtain. A scheme already at this value gains
#: nothing from being replaced.
_FULL_MIN_PROCESSOR_STATE = 100


class SetPowerPlanTweak(Tweak):
    """Switch the active Windows power scheme to a target plan."""

    id = "power.plan.set_active"
    version = 1
    name = "Активная схема питания"
    description = (
        "Переключает Windows на выбранную схему питания, чтобы процессор не "
        "удерживался в низком состоянии производительности во время игры."
    )
    rationale = (
        "Схема питания управляет политикой P-состояний процессора. "
        "«Сбалансированная» разрешает низкие состояния и поднимает частоту "
        "уже под нагрузкой; «Высокая производительность» держит более "
        "высокий минимум. Это документированное поведение Windows, а не "
        "народный твик."
    )
    risk = RiskLevel.LOW
    subsystem = "power"
    scope = BackupScope.POWER
    requires_admin = False
    """`powercfg /setactive` changes the current user's active scheme and
    does not require elevation on a standard desktop. Validated at runtime
    rather than assumed: if it fails with access denied, the engine rolls
    back and reports the remedy."""

    def __init__(
        self,
        target_guid: str = BuiltInScheme.HIGH_PERFORMANCE.value,
        target_name: str = "Высокая производительность",
        manager: PowerManager | None = None,
        settings: PowerSettings | None = None,
    ) -> None:
        self.target_guid = target_guid.lower()
        self.target_name = target_name
        self._manager = manager
        self._settings = settings or PowerSettings()

    def _pm(self, ctx: TweakContext) -> PowerManager:
        if self._manager is not None:
            return self._manager
        return PowerManager(runner=ctx.runner)

    # -- lifecycle ---------------------------------------------------------

    def _satisfied_by(self, current_guid: str | None) -> bool:
        """True when switching would gain nothing, or would lose something.

        Comparing GUIDs does not work, and finding that out cost a bug:
        enabling Ultimate Performance does not activate the documented
        GUID, it creates a *copy* of that scheme with a fresh one. The
        development machine was running such a copy, and the tweak cheerfully
        offered to "optimise" it by moving it down to High Performance.

        So the question is asked of the machine instead of a table. What
        this switch exists to obtain is a processor that is not allowed to
        drop below full speed; if the active scheme already holds the
        minimum processor state at 100%, replacing it achieves nothing
        measurable and risks discarding whatever else the operator set.

        A scheme whose settings cannot be read is not assumed to be
        satisfactory: unknown means the change is still offered, and the
        operator decides.
        """
        if current_guid is None:
            return False
        if current_guid == self.target_guid:
            return True

        observed = self._settings.read(
            SUBGROUP_PROCESSOR, SETTING_PROCESSOR_MIN_STATE
        )
        return observed is not None and observed.ac >= _FULL_MIN_PROCESSOR_STATE

    def scan(self, ctx: TweakContext) -> TweakState:
        manager = self._pm(ctx)
        active = manager.get_active_scheme()
        current_guid = active.normalized_guid if active else None
        current_label = (active.name or current_guid) if active else "неизвестно"
        satisfied = self._satisfied_by(current_guid)

        if satisfied and current_guid != self.target_guid:
            summary = (
                f"Схема питания: {current_label} — процессор уже не "
                f"опускается ниже 100%, менять не нужно"
            )
        elif satisfied:
            summary = f"Схема питания уже {self.target_name}"
        else:
            summary = f"Схема питания: {current_label} -> {self.target_name}"

        return TweakState(
            current_value=current_guid,
            desired_value=self.target_guid,
            needs_change=not satisfied,
            current_absent=current_guid is None,
            summary=summary,
        )

    def validate(self, ctx: TweakContext, state: TweakState) -> Validation:
        manager = self._pm(ctx)
        if state.current_absent:
            return Validation.refuse(
                "Не удалось прочитать текущую схему питания, поэтому вернуть "
                "её обратно будет нечем."
            )
        if not manager.scheme_exists(self.target_guid):
            return Validation.refuse(
                f"На этом ПК нет схемы питания «{self.target_name}». "
                "«Максимальная производительность», в частности, "
                "отсутствует, пока её не добавят вручную."
            )
        return Validation.allow()

    def backup(self, ctx: TweakContext, state: TweakState) -> BackupRecord:
        if state.current_value is None:
            # The engine treats a backup failure as fatal for this tweak,
            # which is the correct outcome: without the previous GUID there
            # is no way back.
            raise PgmError(
                what="Не удалось сохранить текущую схему питания",
                reason="Windows не сообщил активную схему питания.",
                remedy="Откройте параметры питания Windows, выберите схему и повторите.",
            )
        return BackupRecord(
            backup_id=BackupRecord.new_id(),
            tweak_id=self.id,
            tweak_version=self.version,
            scope=BackupScope.POWER,
            target="active_power_scheme",
            old_value=state.current_value,
            old_value_absent=False,
            old_value_kind="GUID",
            new_value=self.target_guid,
        )

    def apply(self, ctx: TweakContext, state: TweakState) -> ApplyResult:
        self._pm(ctx).set_active(self.target_guid)
        return ApplyResult(
            Outcome.SUCCESS, f"Активная схема питания: {self.target_name}."
        )

    def verify(self, ctx: TweakContext, state: TweakState) -> Verification:
        active = self._pm(ctx).get_active_guid()
        if active == self.target_guid:
            return Verification.confirm(
                active, "powercfg подтверждает активную схему."
            )
        return Verification.deny(
            active,
            f"Ожидалась активная схема {self.target_guid}, а Windows сообщает "
            f"{active or 'ничего'}.",
        )

    def rollback(self, ctx: TweakContext, backup: BackupRecord) -> ApplyResult:
        previous = str(backup.old_value)
        manager = self._pm(ctx)
        manager.set_active(previous)

        # Rollback is verified too. A rollback that silently failed would
        # leave the operator believing the machine was restored.
        if manager.get_active_guid() != previous.lower():
            return ApplyResult(
                Outcome.FAILED,
                f"Пытались вернуть схему питания {previous}, но активной она "
                "не стала.",
            )
        return ApplyResult(
            Outcome.SUCCESS, f"Схема питания восстановлена: {previous}."
        )
