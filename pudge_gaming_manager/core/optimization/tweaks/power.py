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

_log = get_logger(__name__)


class SetPowerPlanTweak(Tweak):
    """Switch the active Windows power scheme to a target plan."""

    id = "power.plan.set_active"
    version = 1
    name = "Активная схема питания"
    description = (
        "Переключает Windows на выбранную схему питания, чтобы процессор не "
        "held in a low performance state during play."
    )
    rationale = (
        "The power scheme controls processor performance-state policy. "
        "Balanced permits low P-states and ramps under load; High "
        "Performance holds a higher minimum. Documented Windows behaviour."
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
        target_name: str = "High performance",
        manager: PowerManager | None = None,
    ) -> None:
        self.target_guid = target_guid.lower()
        self.target_name = target_name
        self._manager = manager

    def _pm(self, ctx: TweakContext) -> PowerManager:
        if self._manager is not None:
            return self._manager
        return PowerManager(runner=ctx.runner)

    # -- lifecycle ---------------------------------------------------------

    def scan(self, ctx: TweakContext) -> TweakState:
        manager = self._pm(ctx)
        active = manager.get_active_scheme()
        current_guid = active.normalized_guid if active else None
        current_label = (active.name or current_guid) if active else "unknown"

        return TweakState(
            current_value=current_guid,
            desired_value=self.target_guid,
            needs_change=current_guid != self.target_guid,
            current_absent=current_guid is None,
            summary=(
                f"Схема питания: {current_label} -> {self.target_name}"
                if current_guid != self.target_guid
                else f"Схема питания уже {self.target_name}"
            ),
        )

    def validate(self, ctx: TweakContext, state: TweakState) -> Validation:
        manager = self._pm(ctx)
        if state.current_absent:
            return Validation.refuse(
                "Не удалось прочитать текущую схему питания, поэтому её нельзя "
                "be restored afterwards."
            )
        if not manager.scheme_exists(self.target_guid):
            return Validation.refuse(
                f"This PC has no power plan '{self.target_name}'. "
                "Ultimate Performance in particular is absent unless added "
                "manually."
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
            return Verification.confirm(active, "Active plan confirmed by powercfg.")
        return Verification.deny(
            active,
            f"Expected {self.target_guid} to be active but Windows reports "
            f"{active or 'nothing'}.",
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
                f"Tried to restore power plan {previous} but it is not active.",
            )
        return ApplyResult(Outcome.SUCCESS, f"Power plan restored to {previous}.")
