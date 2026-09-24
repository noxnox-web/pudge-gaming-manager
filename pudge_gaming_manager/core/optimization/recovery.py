"""Changes a previous run started but never finished.

A backup row is written in state ``APPLYING`` before a change and moves on
only once the change is verified (``APPLIED``) or undone (``RESTORED`` /
``FAILED``). A row still in ``APPLYING`` at startup therefore means the
process died somewhere between saving the old value and confirming the new
one — a power cut, a crash, a killed task.

PGM does **not** roll these back automatically at startup. An unattended
change nobody previewed is exactly what the safety model forbids, and the
setting may since have been fixed by hand. Instead the operator is shown
each interrupted change with its original value, so they can decide, and the
rows are marked as reported so the warning is not repeated.
"""

from __future__ import annotations

from dataclasses import dataclass

from ...database.connection import Database, utc_now


@dataclass(frozen=True, slots=True)
class InterruptedChange:
    """One change that was started but never confirmed or undone."""

    backup_id: str
    tweak_id: str
    target: str
    old_value: str | None
    old_value_absent: bool
    started_at: str

    def line(self) -> str:
        original = "did not exist" if self.old_value_absent else repr(self.old_value)
        return (
            f"{self.tweak_id} on {self.target} (started {self.started_at}); "
            f"original value: {original}"
        )


def find_interrupted(database: Database) -> list[InterruptedChange]:
    rows = database.query(
        "SELECT backup_id, tweak_id, target, old_value, old_value_absent, "
        "created_at FROM backups WHERE state='APPLYING' ORDER BY created_at"
    )
    return [
        InterruptedChange(
            backup_id=row["backup_id"],
            tweak_id=row["tweak_id"],
            target=row["target"],
            old_value=row["old_value"],
            old_value_absent=bool(row["old_value_absent"]),
            started_at=row["created_at"],
        )
        for row in rows
    ]


def acknowledge(database: Database, changes: list[InterruptedChange]) -> None:
    """Mark reported changes so the warning is shown once, and audit it.

    The row keeps its original value; only the state moves to ``FAILED``,
    which is what an unconfirmed change is.
    """
    for change in changes:
        database.execute(
            "UPDATE backups SET state='FAILED', restored_at=? "
            "WHERE backup_id=? AND state='APPLYING'",
            (utc_now(), change.backup_id),
        )
        database.record_audit(
            "recovery", "interrupted_change",
            target=change.target,
            old_state=None if change.old_value_absent else change.old_value,
            result="FAILED",
            error="The process ended before this change was verified; "
                  "reported to the operator at the next start.",
        )


__all__ = ["InterruptedChange", "acknowledge", "find_interrupted"]
