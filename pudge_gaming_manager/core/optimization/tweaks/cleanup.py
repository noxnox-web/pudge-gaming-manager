"""Cleanup as a tweak, so disk reclamation goes through the same pipeline.

Why ``BackupScope.NONE``
------------------------
Deleting a temporary file cannot be undone, and the engine normally refuses
to apply anything it cannot reverse. This is the documented exception: the
categories the cleaner is allowed to touch hold data the system regenerates
on demand, so there is nothing to restore. That exemption is narrow and it
is the *rules* module, not this tweak, that decides what qualifies.

The safety here is therefore front-loaded: the scan is the review step, the
preview shows exactly what will go, and every file is re-validated at
deletion time.
"""

from __future__ import annotations

from ....utilities.logging_setup import get_logger
from ....windows.cleanup.engine import CleanupEngine
from ....utilities.formatting import format_size
from ....windows.cleanup.rules import CleanupCategory, CleanupRisk, default_categories
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

#: Cleanup risk maps onto the engine's risk ladder. MEDIUM cleanup (browser
#: caches) inherits MEDIUM, so it needs explicit opt-in like any other
#: MEDIUM change.
_RISK_MAP = {
    CleanupRisk.SAFE: RiskLevel.SAFE,
    CleanupRisk.LOW: RiskLevel.LOW,
    CleanupRisk.MEDIUM: RiskLevel.MEDIUM,
}


class CleanTemporaryFilesTweak(Tweak):
    """Reclaim disk space by removing regenerable temporary data."""

    id = "storage.cleanup.temporary"
    version = 1
    name = "Remove temporary files"
    description = (
        "Deletes temporary files, shader caches and crash dumps that "
        "Windows and applications regenerate on demand."
    )
    rationale = (
        "Only allowlisted directories are touched, and only data the system "
        "recreates by itself. A nearly full system drive causes stutter, "
        "failed shader-cache writes and failed Windows updates."
    )
    risk = RiskLevel.SAFE
    subsystem = "storage"
    scope = BackupScope.NONE
    requires_admin = False

    def __init__(
        self,
        categories: tuple[CleanupCategory, ...] | None = None,
        engine: CleanupEngine | None = None,
    ) -> None:
        self.categories = categories if categories is not None else default_categories()
        self._engine = engine or CleanupEngine(self.categories)
        self._report = None
        self._reclaimed = 0

        # Inherit the highest risk among the selected categories, so a
        # selection including browser caches is gated accordingly.
        highest = max(
            (_RISK_MAP[c.risk] for c in self.categories),
            key=lambda r: r.rank,
            default=RiskLevel.SAFE,
        )
        self.risk = highest

    # -- lifecycle ---------------------------------------------------------

    def scan(self, ctx: TweakContext) -> TweakState:
        self._report = self._engine.scan()
        total = self._report.total_bytes
        return TweakState(
            current_value=total,
            desired_value=0,
            needs_change=total > 0,
            summary=(
                f"Remove {self._report.size_display} of temporary files "
                f"({self._report.total_files} files)"
                if total
                else "No temporary files to remove"
            ),
        )

    def validate(self, ctx: TweakContext, state: TweakState) -> Validation:
        if self._report is None:
            return Validation.refuse("Cleanup has not been scanned yet.")
        if not any(c.available for c in self._report.categories):
            return Validation.refuse(
                "None of the selected cleanup categories are available on "
                "this PC."
            )
        return Validation.allow()

    def drift_since_plan(self, ctx: TweakContext, planned: TweakState) -> str | None:
        """Never stale in the sense the default check means.

        Temp folders change every second, so re-scanning would always report
        a difference and block cleanup forever. Instead, every file is
        re-validated at the moment of deletion (containment, protection, age,
        and a handle-based delete), which is the stronger guarantee here.
        """
        return None

    def backup(self, ctx: TweakContext, state: TweakState) -> BackupRecord:
        # Never called: the engine skips backup for BackupScope.NONE.
        raise NotImplementedError(
            "Temporary-file cleanup is not reversible and takes no backup."
        )

    def apply(self, ctx: TweakContext, state: TweakState) -> ApplyResult:
        if self._report is None:
            return ApplyResult(Outcome.FAILED, "Cleanup was not scanned.")

        result = self._engine.clean(self._report, dry_run=ctx.dry_run)
        self._reclaimed = result.deleted_bytes

        detail = (
            f"Removed {result.size_display} in {result.deleted_files} files."
        )
        if result.failed_files:
            detail += f" {result.failed_files} file(s) were in use and kept."
        if result.refused_files:
            detail += f" {result.refused_files} changed since the scan and were kept."
        return ApplyResult(Outcome.SUCCESS, detail)

    def verify(self, ctx: TweakContext, state: TweakState) -> Verification:
        """Confirm by rescanning: the remaining total must have dropped.

        Files held open by a running process legitimately survive, so this
        checks that space was actually reclaimed rather than demanding the
        categories be empty.
        """
        if self._report is None:
            return Verification.deny(None, "Cleanup was not scanned.")

        after = self._engine.scan()
        before = int(state.current_value or 0)
        if after.total_bytes < before or self._reclaimed > 0:
            return Verification.confirm(
                after.total_bytes,
                f"Reclaimed {format_size(before - after.total_bytes)}; "
                f"{after.size_display} remains in files currently in use.",
            )
        return Verification.deny(
            after.total_bytes,
            "No space was reclaimed; every candidate file was locked or "
            "changed during the operation.",
        )

    def rollback(self, ctx: TweakContext, backup: BackupRecord) -> ApplyResult:
        # Unreachable: with BackupScope.NONE the engine never creates a
        # backup, and it only rolls back when one exists.
        return ApplyResult(
            Outcome.FAILED,
            "Deleted temporary files cannot be restored. They are "
            "regenerated by Windows and the applications that created them.",
        )
