"""Emptying the Recycle Bin as a tweak, so OPTIMIZE PC can offer it.

Why ``BackupScope.NONE``
------------------------
The same documented exception the temporary-file cleanup takes: there is
nothing to back up, because the items are already deleted. The safety is
front-loaded instead — the scan reports the exact size and item count, and
the mandatory preview shows that row before anything runs.

Why ``RiskLevel.LOW`` rather than MEDIUM
----------------------------------------
This was the judgement call. MEDIUM would mean the tweak is never offered
at all: the pipeline runs with ``allow_risk_above_low=False``, so a MEDIUM
tweak is dropped from the plan before the operator ever sees it — "safe" in
the sense that a feature nobody can reach is safe.

LOW is defensible on the facts. Every item in the bin is a file the user
already chose to delete; Windows' own Storage Sense empties it on a
schedule by default and under disk pressure without asking; and nothing is
removed here without appearing, with its size, in a preview the operator
confirms. What is lost is the undo for a deletion someone already made, and
that is stated plainly in the description rather than glossed.

An operator who does not want it unticks the row in the preview.
"""

from __future__ import annotations

from ....utilities.formatting import format_size
from ....windows.cleanup import recycle_bin
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


class EmptyRecycleBinTweak(Tweak):
    """Empty the Recycle Bin on every drive."""

    id = "storage.recycle_bin.empty"
    version = 1
    name = "Очистка корзины"
    description = (
        "Безвозвратно удаляет всё, что лежит в корзине, на всех дисках. "
        "Восстановить эти файлы после очистки будет нельзя."
    )
    rationale = (
        "В корзине лежат файлы, которые пользователь уже удалил; корзина "
        "лишь держит их до тех пор, пока не понадобится место. На клубном "
        "ПК она копит содержимое от сессии к сессии. Windows и сама "
        "очищает её по расписанию через «Контроль памяти»."
    )
    risk = RiskLevel.LOW
    subsystem = "storage"
    scope = BackupScope.NONE
    requires_admin = False
    """``SHEmptyRecycleBinW`` empties the bins the calling user can see.
    Another user's bin on the same PC is not touched, and does not need to
    be for the space to come back on a single-account club machine."""

    def __init__(self) -> None:
        self._before = recycle_bin.RecycleBinState()

    # -- lifecycle ---------------------------------------------------------

    def scan(self, ctx: TweakContext) -> TweakState:
        self._before = recycle_bin.query()
        if not self._before.available:
            return TweakState(
                needs_change=False,
                current_absent=True,
                summary=f"Корзина недоступна: {self._before.unavailable_reason}",
            )
        return TweakState(
            current_value=self._before.size_bytes,
            desired_value=0,
            needs_change=self._before.has_content,
            summary=(
                f"Очистить корзину: {self._before.size_display} в "
                f"{self._before.item_count} объектах"
                if self._before.has_content
                else "Корзина уже пуста"
            ),
        )

    def validate(self, ctx: TweakContext, state: TweakState) -> Validation:
        if state.current_absent:
            return Validation.refuse(
                "Windows не сообщил состояние корзины, поэтому очищать её "
                "вслепую нельзя."
            )
        return Validation.allow()

    def backup(self, ctx: TweakContext, state: TweakState) -> BackupRecord:
        # Never called: the engine skips backup for BackupScope.NONE.
        raise NotImplementedError("Очистка корзины необратима и не бэкапится.")

    def apply(self, ctx: TweakContext, state: TweakState) -> ApplyResult:
        if ctx.dry_run:
            return ApplyResult(
                Outcome.SUCCESS, "Пробный прогон: корзина не очищена."
            )
        ok, detail = recycle_bin.empty()
        return ApplyResult(Outcome.SUCCESS if ok else Outcome.FAILED, detail)

    def verify(self, ctx: TweakContext, state: TweakState) -> Verification:
        """Confirm by re-reading the bin, not by trusting the API's return.

        Items held open by another process legitimately survive, and saying
        so is better than reporting a clean sweep that did not happen.
        """
        after = recycle_bin.query()
        if not after.available:
            return Verification.deny(
                None, f"Состояние корзины не читается: {after.unavailable_reason}"
            )
        if after.item_count == 0:
            freed = max(int(state.current_value or 0) - after.size_bytes, 0)
            return Verification.confirm(
                after.size_bytes, f"Корзина пуста, освобождено {format_size(freed)}."
            )
        return Verification.deny(
            after.size_bytes,
            f"В корзине осталось объектов: {after.item_count} — вероятно, "
            "они заняты другим процессом.",
        )

    def rollback(self, ctx: TweakContext, backup: BackupRecord) -> ApplyResult:
        # Unreachable: with BackupScope.NONE the engine never creates a backup.
        return ApplyResult(
            Outcome.FAILED,
            "Очищенную корзину восстановить нельзя.",
        )


__all__ = ["EmptyRecycleBinTweak"]
