"""Turning off pointer acceleration.

Rationale (rule #64)
--------------------
With acceleration on, Windows multiplies pointer travel once the mouse
crosses a speed threshold, so the same physical movement lands the cursor in
a different place depending on how fast it was made. Aiming is a motor skill
built on the opposite assumption — that a given hand movement always produces
the same result — which is why every competitive first-person game and every
mouse vendor's own guidance turns it off.

**What is claimed:** pointer travel becomes proportional to mouse travel, and
PGM verifies that Windows reports it so.
**What is not claimed:** better aim, or any effect on frame rate. It removes
a source of inconsistency; the player still has to hit the target.

Risk (rule #38)
---------------
MEDIUM, not LOW. A player who has used acceleration for years will feel the
difference immediately, and on a shared club PC that is a user-visible change
to someone else's machine. It is fully reversible and its previous values are
recorded, but it is not the kind of change that should be applied because
nobody unticked it — so it arrives unticked, behind the preview's explicit
opt-in.
"""

from __future__ import annotations

from ....utilities.logging_setup import get_logger
from ....windows.input import mouse
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


class DisableMouseAccelerationTweak(Tweak):
    """Set pointer movement to be proportional to mouse movement."""

    id = "input.mouse.disable_acceleration"
    version = 1
    name = "Ускорение мыши"
    description = (
        "Выключает «Повышенная точность установки указателя», чтобы одно и "
        "то же движение мышью всегда давало одно и то же перемещение "
        "курсора."
    )
    rationale = (
        "При включённом ускорении Windows домножает перемещение курсора, "
        "когда мышь пересекает порог скорости: одинаковое движение рукой "
        "даёт разный результат в зависимости от того, насколько резко оно "
        "сделано. Прицеливание — моторный навык, построенный на обратном "
        "допущении, поэтому в соревновательных играх ускорение выключают."
    )
    risk = RiskLevel.MEDIUM
    subsystem = "input"
    scope = BackupScope.REGISTRY
    """SPI_SETMOUSE with SPIF_UPDATEINIFILE persists to the user profile,
    so the prior values are restored the same way they were set."""

    requires_admin = False
    """A per-user setting: it changes this account's pointer, not the PC's."""

    # -- lifecycle ---------------------------------------------------------

    def scan(self, ctx: TweakContext) -> TweakState:
        current = mouse.read()
        if current is None:
            return TweakState(
                needs_change=False,
                current_absent=True,
                summary="Не удалось прочитать настройку мыши",
            )
        return TweakState(
            current_value=current.as_text(),
            desired_value=mouse.DISABLED.as_text(),
            needs_change=current.enabled,
            summary=(
                "Выключить ускорение мыши"
                if current.enabled
                else "Ускорение мыши уже выключено"
            ),
        )

    def validate(self, ctx: TweakContext, state: TweakState) -> Validation:
        if state.current_absent:
            return Validation.refuse(
                "Windows не сообщил текущую настройку мыши, поэтому вернуть "
                "её обратно будет нечем."
            )
        return Validation.allow()

    def backup(self, ctx: TweakContext, state: TweakState) -> BackupRecord:
        return BackupRecord(
            backup_id=BackupRecord.new_id(),
            tweak_id=self.id,
            tweak_version=self.version,
            scope=BackupScope.REGISTRY,
            target="SPI_SETMOUSE",
            old_value=state.current_value,
            old_value_absent=False,
            old_value_kind="MOUSE_PARAMS",
            new_value=mouse.DISABLED.as_text(),
        )

    def apply(self, ctx: TweakContext, state: TweakState) -> ApplyResult:
        if ctx.dry_run:
            return ApplyResult(
                Outcome.SUCCESS, "Пробный прогон: настройка не изменена."
            )
        if not mouse.write(mouse.DISABLED):
            return ApplyResult(
                Outcome.FAILED, "Windows отклонил изменение настройки мыши."
            )
        return ApplyResult(Outcome.SUCCESS, "Ускорение мыши выключено.")

    def verify(self, ctx: TweakContext, state: TweakState) -> Verification:
        """Believe the read-back, not the return value of the call.

        ``SystemParametersInfo`` can report success and leave the setting
        alone; only re-reading it proves anything.
        """
        current = mouse.read()
        if current is None:
            return Verification.deny(
                None, "Настройку мыши не удалось перечитать после изменения."
            )
        if not current.enabled:
            return Verification.confirm(
                current.as_text(), "Windows подтверждает: ускорение выключено."
            )
        return Verification.deny(
            current.as_text(),
            f"Ускорение всё ещё включено (Windows сообщает {current.as_text()}).",
        )

    def rollback(self, ctx: TweakContext, backup: BackupRecord) -> ApplyResult:
        previous = mouse.MouseAcceleration.from_text(str(backup.old_value))
        if previous is None:
            return ApplyResult(
                Outcome.FAILED,
                f"Сохранённая настройка «{backup.old_value}» не читается.",
            )
        if not mouse.write(previous):
            return ApplyResult(
                Outcome.FAILED, "Не удалось вернуть прежнюю настройку мыши."
            )

        # A rollback is verified too: restoring and not checking would leave
        # the operator believing the pointer was put back.
        restored = mouse.read()
        if restored is None or restored.as_text() != previous.as_text():
            return ApplyResult(
                Outcome.FAILED,
                f"Пытались вернуть {previous.as_text()}, а Windows сообщает "
                f"{restored.as_text() if restored else 'ничего'}.",
            )
        return ApplyResult(
            Outcome.SUCCESS, f"Настройка мыши восстановлена: {previous.as_text()}."
        )


__all__ = ["DisableMouseAccelerationTweak"]
