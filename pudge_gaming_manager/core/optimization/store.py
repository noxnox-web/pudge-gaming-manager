"""Everything the tweak engine writes down.

Split from ``engine.py`` so the engine reads as the change lifecycle alone,
and every row it produces is defined in one place: backups (written *before*
the mutation they protect), runs, per-tweak applications, the tweak catalogue,
and the audit trail.

Auditing writes to **both** sinks: the ``audit.log`` JSON-lines file, and the
``audit_log`` table, which a trigger makes append-only. The table is the
tamper-resistant record; the file is readable without a SQLite tool.
"""

from __future__ import annotations

from typing import Any, Sequence

from ...database.connection import Database, utc_now
from ...utilities.logging_setup import audit_event, get_logger
from ...utilities.privileges import is_admin
from .tweak import BackupRecord, BackupScope, Outcome, Phase, Tweak, TweakState

_log = get_logger(__name__)


def _text(value: Any) -> str | None:
    return None if value is None else str(value)


class RunStore:
    """Persists the engine's backups, runs, applications and audit rows."""

    def __init__(self, database: Database) -> None:
        self.database = database

    # -- backups -------------------------------------------------------------

    def persist_backup(self, backup: BackupRecord, run_id: str) -> None:
        """Write the backup row *before* the mutation it protects."""
        self.database.execute(
            "INSERT INTO backups (backup_id, run_id, tweak_id, tweak_version, "
            "created_at, scope, target, old_value, old_value_absent, "
            "old_value_kind, new_value, state) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                backup.backup_id, run_id, backup.tweak_id, backup.tweak_version,
                utc_now(), backup.scope.value, backup.target,
                _text(backup.old_value), 1 if backup.old_value_absent else 0,
                backup.old_value_kind, _text(backup.new_value), "APPLYING",
            ),
        )

    def mark_backup(self, backup_id: str, state: str, *, finished: bool = False) -> None:
        """Move a backup to APPLIED, RESTORED or FAILED."""
        if finished:
            self.database.execute(
                "UPDATE backups SET state=?, restored_at=? WHERE backup_id=?",
                (state, utc_now(), backup_id),
            )
        else:
            self.database.execute(
                "UPDATE backups SET state=? WHERE backup_id=?", (state, backup_id)
            )

    # -- runs and applications -------------------------------------------------

    def open_run(self, run_id: str, *, dry_run: bool, planned: int) -> None:
        self.database.execute(
            "INSERT OR REPLACE INTO optimization_runs (run_id, started_at, "
            "dry_run, status, planned_count) VALUES (?,?,?,?,?)",
            (run_id, utc_now(), 1 if dry_run else 0, "RUNNING", planned),
        )

    def close_run(
        self, run_id: str, *, applied: int, failed: int, rolled_back: int
    ) -> None:
        self.database.execute(
            "UPDATE optimization_runs SET finished_at=?, status=?, "
            "applied_count=?, failed_count=?, rolled_back=? WHERE run_id=?",
            (
                utc_now(), "COMPLETED" if failed == 0 else "FAILED",
                applied, failed, rolled_back, run_id,
            ),
        )

    def record_application(
        self,
        run_id: str,
        tweak: Tweak,
        phase: Phase,
        outcome: Outcome,
        state: TweakState,
        error: str | None,
    ) -> None:
        self.database.execute(
            "INSERT INTO tweak_applications (run_id, tweak_id, tweak_version, "
            "started_at, finished_at, phase, result, before_state, "
            "after_state, error) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                run_id, tweak.id, tweak.version, utc_now(), utc_now(),
                phase.value, outcome.value,
                _text(state.current_value), _text(state.desired_value), error,
            ),
        )

    # -- catalogue -------------------------------------------------------------

    def register(self, tweaks: Sequence[Tweak]) -> None:
        """Record tweak metadata so history stays readable across versions."""
        for tweak in tweaks:
            self.database.execute(
                "INSERT OR REPLACE INTO tweaks (tweak_id, version, name, "
                "description, rationale, risk, subsystem, reversible) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (
                    tweak.id, tweak.version, tweak.name, tweak.description,
                    tweak.rationale, tweak.risk.value, tweak.subsystem,
                    0 if tweak.scope is BackupScope.NONE else 1,
                ),
            )

    # -- audit -----------------------------------------------------------------

    def audit(
        self,
        module: str,
        operation: str,
        *,
        target: str,
        old_state: Any = None,
        new_state: Any = None,
        result: str,
        error: str | None = None,
    ) -> None:
        """Record one change in the audit file *and* the append-only table.

        A failure to write the table is logged, never raised: the change has
        already happened, and the file record still exists. Aborting here
        would skip the rollback bookkeeping that follows.
        """
        audit_event(
            module, operation, target=target, old_state=old_state,
            new_state=new_state, result=result, error=error,
        )
        try:
            self.database.record_audit(
                module, operation, target=target, old_state=old_state,
                new_state=new_state, result=result, error=error,
                elevated=is_admin(),
            )
        except Exception as exc:  # noqa: BLE001 - see docstring
            _log.error("audit_log table write failed for %s: %s", target, exc)


__all__ = ["RunStore"]
