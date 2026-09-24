"""SQLite schema for Pudge Cleaner (rule #34).

Design notes that matter
------------------------
``backups.old_value_absent``
    Distinguishes "the value existed and was empty" from "the value did not
    exist". Restoring the wrong one leaves the machine in a state it was never
    in. Storing only ``old_value`` cannot express the difference, so the column
    exists to make the mistake unrepresentable.

``backups.state``
    A backup is written in APPLYING *before* mutation and moves to APPLIED
    only once the change is verified (or to RESTORED / FAILED if it is undone).
    A row still in APPLYING at startup means the process died mid-change; the
    dashboard reports it with the original value (core.optimization.recovery)
    rather than rolling it back unattended.

``audit_log``
    Append-only by convention and by trigger. Nothing in the application
    updates or deletes from it.

No table stores a secret. PGM holds no credential, which is the cheapest way
to never leak one.
"""

from __future__ import annotations

SCHEMA_VERSION = 1

#: Ordered DDL statements. Each migration appends; none rewrites history.
MIGRATIONS: dict[int, tuple[str, ...]] = {
    1: (
        # -- versioning ---------------------------------------------------
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version     INTEGER PRIMARY KEY,
            applied_at  TEXT    NOT NULL
        )
        """,
        # -- hardware / system snapshots ----------------------------------
        """
        CREATE TABLE IF NOT EXISTS system_info (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            captured_at  TEXT    NOT NULL,
            machine_name TEXT    NOT NULL,
            os_caption   TEXT    NOT NULL,
            os_build     TEXT    NOT NULL,
            payload      TEXT    NOT NULL  -- full scan as JSON
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_system_info_captured
            ON system_info (captured_at DESC)
        """,
        """
        CREATE TABLE IF NOT EXISTS scan_results (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id       TEXT    NOT NULL,
            captured_at  TEXT    NOT NULL,
            subsystem    TEXT    NOT NULL,   -- 'hardware.cpu', 'network', ...
            status       TEXT    NOT NULL,   -- GOOD | WARNING | CRITICAL | UNAVAILABLE
            payload      TEXT    NOT NULL
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_scan_results_run
            ON scan_results (run_id, subsystem)
        """,
        # -- tweak catalogue ----------------------------------------------
        """
        CREATE TABLE IF NOT EXISTS tweaks (
            tweak_id     TEXT    NOT NULL,
            version      INTEGER NOT NULL,
            name         TEXT    NOT NULL,
            description  TEXT    NOT NULL,
            rationale    TEXT    NOT NULL,
            risk         TEXT    NOT NULL,   -- SAFE|LOW|MEDIUM|HIGH|CRITICAL
            subsystem    TEXT    NOT NULL,
            reversible   INTEGER NOT NULL DEFAULT 1,
            PRIMARY KEY (tweak_id, version)
        )
        """,
        # -- optimization runs --------------------------------------------
        """
        CREATE TABLE IF NOT EXISTS optimization_runs (
            run_id        TEXT    PRIMARY KEY,
            started_at    TEXT    NOT NULL,
            finished_at   TEXT,
            profile_name  TEXT,
            dry_run       INTEGER NOT NULL DEFAULT 0,
            status        TEXT    NOT NULL,  -- RUNNING|COMPLETED|FAILED|ABORTED
            planned_count INTEGER NOT NULL DEFAULT 0,
            applied_count INTEGER NOT NULL DEFAULT 0,
            failed_count  INTEGER NOT NULL DEFAULT 0,
            rolled_back   INTEGER NOT NULL DEFAULT 0,
            score_before  REAL,
            score_after   REAL,
            notes         TEXT
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS tweak_applications (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id        TEXT    NOT NULL REFERENCES optimization_runs(run_id),
            tweak_id      TEXT    NOT NULL,
            tweak_version INTEGER NOT NULL,
            started_at    TEXT    NOT NULL,
            finished_at   TEXT,
            phase         TEXT    NOT NULL,  -- SCAN|VALIDATE|BACKUP|APPLY|VERIFY|ROLLBACK
            result        TEXT    NOT NULL,  -- SUCCESS|FAILED|SKIPPED|ROLLED_BACK
            before_state  TEXT,
            after_state   TEXT,
            error         TEXT
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_tweak_applications_run
            ON tweak_applications (run_id)
        """,
        # -- rollback -------------------------------------------------------
        """
        CREATE TABLE IF NOT EXISTS backups (
            backup_id         TEXT    PRIMARY KEY,
            run_id            TEXT,
            tweak_id          TEXT    NOT NULL,
            tweak_version     INTEGER NOT NULL,
            created_at        TEXT    NOT NULL,
            scope             TEXT    NOT NULL,  -- REGISTRY|SERVICE|POWER|FILE|DISPLAY
            target            TEXT    NOT NULL,  -- the key/service/path touched
            old_value         TEXT,
            old_value_absent  INTEGER NOT NULL DEFAULT 0,
            old_value_kind    TEXT,              -- REG_DWORD, REG_SZ, ...
            new_value         TEXT,
            state             TEXT    NOT NULL,  -- PENDING|APPLYING|APPLIED|RESTORED|FAILED
            restored_at       TEXT,
            snapshot_id       TEXT
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_backups_state
            ON backups (state)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_backups_run
            ON backups (run_id, created_at DESC)
        """,
        """
        CREATE TABLE IF NOT EXISTS snapshots (
            snapshot_id  TEXT    PRIMARY KEY,
            created_at   TEXT    NOT NULL,
            name         TEXT    NOT NULL,
            description  TEXT,
            item_count   INTEGER NOT NULL DEFAULT 0
        )
        """,
        # -- profiles -------------------------------------------------------
        """
        CREATE TABLE IF NOT EXISTS profiles (
            name            TEXT    PRIMARY KEY,
            schema_version  INTEGER NOT NULL,
            is_golden       INTEGER NOT NULL DEFAULT 0,
            created_at      TEXT    NOT NULL,
            updated_at      TEXT    NOT NULL,
            source_path     TEXT,
            payload         TEXT    NOT NULL
        )
        """,
        # -- games ----------------------------------------------------------
        """
        CREATE TABLE IF NOT EXISTS games (
            game_id      TEXT    PRIMARY KEY,
            name         TEXT    NOT NULL,
            platform     TEXT    NOT NULL,   -- steam|epic|riot|battlenet|msstore|manual
            install_path TEXT,
            executable   TEXT,
            detected_at  TEXT    NOT NULL,
            payload      TEXT
        )
        """,
        # -- maintenance ------------------------------------------------------
        """
        CREATE TABLE IF NOT EXISTS maintenance_jobs (
            job_id        TEXT    PRIMARY KEY,
            name          TEXT    NOT NULL,
            trigger       TEXT    NOT NULL,  -- DAILY|WEEKLY|ON_BOOT|AFTER_SESSION
            enabled       INTEGER NOT NULL DEFAULT 1,
            last_run_at   TEXT,
            last_result   TEXT,
            next_run_at   TEXT,
            attempt_count INTEGER NOT NULL DEFAULT 0,
            payload       TEXT
        )
        """,
        # -- benchmark ---------------------------------------------------------
        """
        CREATE TABLE IF NOT EXISTS benchmark_results (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id       TEXT,
            measured_at  TEXT    NOT NULL,
            phase        TEXT    NOT NULL,  -- BEFORE|AFTER|STANDALONE
            cpu_score    REAL,
            gpu_score    REAL,
            ram_score    REAL,
            storage_score REAL,
            network_score REAL,
            gaming_score REAL,
            payload      TEXT
        )
        """,
        # -- errors -------------------------------------------------------------
        """
        CREATE TABLE IF NOT EXISTS errors (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            occurred_at TEXT    NOT NULL,
            module      TEXT    NOT NULL,
            error_type  TEXT    NOT NULL,
            what        TEXT    NOT NULL,
            reason      TEXT,
            remedy      TEXT,
            context     TEXT
        )
        """,
        # -- audit --------------------------------------------------------------
        """
        CREATE TABLE IF NOT EXISTS audit_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            occurred_at TEXT    NOT NULL,
            module      TEXT    NOT NULL,
            operation   TEXT    NOT NULL,
            target      TEXT,
            old_state   TEXT,
            new_state   TEXT,
            result      TEXT    NOT NULL,
            error       TEXT,
            username    TEXT,
            elevated    INTEGER NOT NULL DEFAULT 0
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_audit_occurred
            ON audit_log (occurred_at DESC)
        """,
        # The audit trail is evidence. Make tampering fail loudly rather than
        # relying on every future caller remembering not to.
        """
        CREATE TRIGGER IF NOT EXISTS audit_log_no_update
        BEFORE UPDATE ON audit_log
        BEGIN
            SELECT RAISE(ABORT, 'audit_log is append-only');
        END
        """,
        """
        CREATE TRIGGER IF NOT EXISTS audit_log_no_delete
        BEFORE DELETE ON audit_log
        BEGIN
            SELECT RAISE(ABORT, 'audit_log is append-only');
        END
        """,
    ),
}


def statements_for(version: int) -> tuple[str, ...]:
    """Return the DDL for one migration version."""
    return MIGRATIONS.get(version, ())
