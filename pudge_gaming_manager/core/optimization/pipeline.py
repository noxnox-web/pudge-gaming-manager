"""The OPTIMIZE PC pipeline.

Turns a hardware scan into a set of tweaks, plans them, and executes the
plan the operator approved::

    Scan -> Diagnose -> Plan -> [preview] -> Backup -> Apply -> Verify
                                                                  |
                                                        failed ->  Rollback

The pipeline chooses *which* tweaks are relevant; `TweakEngine` owns the
safety invariants for each one. Keeping those separate means adding a tweak
cannot weaken the guarantees, and changing the selection logic cannot break
rollback.

Only findings the scan actually produced generate tweaks. There is no
"apply everything" list, because a change that addresses no observed problem
is the kind of speculative tweak rule #64 forbids.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ...database.connection import Database
from ...hardware.models import HardwareSnapshot
from ...utilities.logging_setup import get_logger
from ...windows.cleanup.rules import CleanupCategory, default_categories
from ..diagnostics.issues import Issue
from ..scoring.score import GamingScore, compute_score
from .engine import Plan, RunReport, TweakEngine
from .tweak import Tweak, TweakContext
from .tweaks.cleanup import CleanTemporaryFilesTweak
from .tweaks.display import tweaks_for_underperforming_displays

_log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class OptimizationPreview:
    """What the operator confirms before anything is changed."""

    plan: Plan
    score_before: GamingScore

    @property
    def change_count(self) -> int:
        return len(self.plan.applicable)

    @property
    def has_changes(self) -> bool:
        return self.change_count > 0

    def lines(self) -> list[str]:
        return self.plan.preview_lines()

    def with_selection(self, selected: set[int]) -> OptimizationPreview:
        """The same preview with only the operator's chosen changes applied."""
        return OptimizationPreview(
            plan=self.plan.with_selection(selected), score_before=self.score_before
        )


@dataclass(slots=True)
class OptimizationOutcome:
    """The before/after report (rule #40).

    Only measurements that were actually taken appear here. Nothing is
    attributed to the optimization that was not observed both before and
    after.
    """

    run: RunReport
    score_before: GamingScore
    score_after: GamingScore | None = None
    snapshot_after: HardwareSnapshot | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def score_delta(self) -> float | None:
        if self.score_after is None or self.score_after.value is None:
            return None
        if self.score_before.value is None:
            return None
        return self.score_after.value - self.score_before.value

    def report_lines(self) -> list[str]:
        lines = [
            f"Применено: {self.run.applied}",
            f"Ошибок: {self.run.failed}",
            f"Откачено: {self.run.rolled_back}",
            "",
            f"Оценка до: {self.score_before.display()}",
        ]
        if self.score_after is not None:
            lines.append(f"Оценка после: {self.score_after.display()}")
            delta = self.score_delta
            if delta is not None:
                lines.append(f"Изменение: {delta:+.0f}")
        # The dashboard score includes the network; these do not, so they
        # can differ from it. Optimizing never re-measures the network, and
        # counting the old measurement as the "after" state would report a
        # value nobody measured.
        lines.append(
            "Обе оценки — только по железу; сеть после оптимизации не "
            "измеряется заново и в сравнение не входит."
        )
        lines.append("")
        lines.append(
            "Изменённая настройка — это проверенное изменение конфигурации, "
            "а не измеренный прирост производительности."
        )
        for result in self.run.results:
            if result.outcome.value in {"SUCCESS", "FAILED", "ROLLED_BACK"}:
                lines.append(f"  [{result.outcome.value}] {result.name}: {result.detail}")
        if self.run.needs_restart:
            lines.append("")
            lines.append("Для полного применения части изменений нужна перезагрузка.")
        return lines


class OptimizationPipeline:
    """Builds and runs an optimization pass."""

    def __init__(
        self,
        database: Database,
        *,
        allow_risk_above_low: bool = False,
        cleanup_categories: tuple[CleanupCategory, ...] | None = None,
    ) -> None:
        self.database = database
        self.allow_risk_above_low = allow_risk_above_low
        self.cleanup_categories = (
            cleanup_categories if cleanup_categories is not None
            else default_categories()
        )

    # -- selection ---------------------------------------------------------

    def build_tweaks(
        self, snapshot: HardwareSnapshot, issues: tuple[Issue, ...] = ()
    ) -> list[Tweak]:
        """Choose the tweaks that address what this scan actually found.

        Driven by observations, not by a fixed list. A PC with plenty of
        disk space and correctly configured displays produces no tweaks at
        all, and that is the correct outcome rather than a failure.
        """
        tweaks: list[Tweak] = []

        # Displays running below their capability at the current resolution.
        tweaks.extend(tweaks_for_underperforming_displays(snapshot.monitors))

        # Disk pressure on any drive, or simply reclaimable space worth having.
        if self._storage_needs_attention(snapshot, issues):
            tweaks.append(CleanTemporaryFilesTweak(self.cleanup_categories))

        _log.info("optimization plan: %d candidate tweaks", len(tweaks))
        return tweaks

    @staticmethod
    def _storage_needs_attention(
        snapshot: HardwareSnapshot, issues: tuple[Issue, ...]
    ) -> bool:
        if any(i.subsystem == "storage" and i.fixable for i in issues):
            return True
        disk = snapshot.system_disk
        free = disk.free_percent.value if disk else None
        return free is not None and free < 25.0

    # -- planning ----------------------------------------------------------

    def preview(
        self,
        snapshot: HardwareSnapshot,
        issues: tuple[Issue, ...] = (),
        score_before: GamingScore | None = None,
    ) -> OptimizationPreview:
        """Produce the dry-run preview. Changes nothing.

        The preview is mandatory and not skippable: the operator approves
        this exact list before the pipeline touches anything.
        """
        engine = self._engine()
        tweaks = self.build_tweaks(snapshot, issues)
        engine.register(tweaks)
        plan = engine.plan(tweaks)
        return OptimizationPreview(
            plan=plan,
            score_before=score_before or compute_score(snapshot),
        )

    def apply(
        self,
        preview: OptimizationPreview,
        *,
        dry_run: bool = False,
        rescan: object | None = None,
    ) -> OptimizationOutcome:
        """Execute an approved preview.

        Args:
            rescan: A callable returning a fresh :class:`HardwareSnapshot`,
                used to measure the after-state. Injected rather than
                imported so this module does not depend on the scanner.
        """
        engine = self._engine()
        run = engine.apply(preview.plan, dry_run=dry_run)

        outcome = OptimizationOutcome(run=run, score_before=preview.score_before)
        if dry_run:
            outcome.notes.append("Пробный прогон: ничего не изменено.")
            return outcome

        if callable(rescan):
            try:
                snapshot_after = rescan()
                outcome.snapshot_after = snapshot_after
                outcome.score_after = compute_score(snapshot_after)
            except Exception as exc:  # noqa: BLE001
                # A failed rescan must not invalidate a successful run; it
                # only means the after-state is unknown, and saying so is
                # better than reporting a stale score as the new one.
                _log.exception("post-optimization rescan failed")
                outcome.notes.append(
                    f"Состояние «после» не удалось измерить: {exc}"
                )
        else:
            outcome.notes.append(
                "Повторное сканирование не выполнялось, состояние «после» не показано."
            )
        return outcome

    def _engine(self) -> TweakEngine:
        return TweakEngine(
            self.database,
            TweakContext(database=self.database),
            allow_risk_above_low=self.allow_risk_above_low,
        )
