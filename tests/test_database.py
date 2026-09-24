"""Tests for the persistence layer."""

from __future__ import annotations

import pathlib
import sqlite3

import pytest

from pudge_gaming_manager.database.connection import Database, utc_now
from pudge_gaming_manager.database.schema import SCHEMA_VERSION
from pudge_gaming_manager.utilities.exceptions import StorageError


@pytest.fixture()
def db(tmp_path: pathlib.Path) -> Database:
    return Database(tmp_path / "test.db")


# -- migrations ------------------------------------------------------------


def test_migrations_apply_on_open(db: Database) -> None:
    assert db.current_version() == SCHEMA_VERSION


def test_migrate_is_idempotent(db: Database) -> None:
    first = db.migrate()
    second = db.migrate()
    assert first == second == SCHEMA_VERSION


def test_all_expected_tables_exist(db: Database) -> None:
    rows = db.query("SELECT name FROM sqlite_master WHERE type='table'")
    tables = {r["name"] for r in rows}
    expected = {
        "schema_migrations", "system_info", "scan_results", "tweaks",
        "optimization_runs", "tweak_applications", "backups", "snapshots",
        "profiles", "games", "maintenance_jobs", "benchmark_results",
        "errors", "audit_log",
    }
    assert expected <= tables, f"missing: {sorted(expected - tables)}"


def test_reopening_an_existing_database_keeps_data(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "persist.db"
    first = Database(path)
    first.record_audit("test", "write", target="x")
    first.close()

    second = Database(path)
    assert len(second.query("SELECT * FROM audit_log")) == 1


# -- audit log is evidence -------------------------------------------------


def test_audit_log_rejects_update(db: Database) -> None:
    db.record_audit("power", "set_plan", target="High performance")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        db.connection.execute("UPDATE audit_log SET result='TAMPERED'")


def test_audit_log_rejects_delete(db: Database) -> None:
    db.record_audit("power", "set_plan", target="High performance")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        db.connection.execute("DELETE FROM audit_log")


def test_audit_serialises_structured_state(db: Database) -> None:
    db.record_audit(
        "registry", "write", target="HKLM\\X",
        old_state={"value": 0}, new_state={"value": 1},
    )
    row = db.query_one("SELECT old_state, new_state FROM audit_log")
    assert row is not None
    assert '"value": 0' in row["old_state"]
    assert '"value": 1' in row["new_state"]


# -- the absent-vs-empty distinction --------------------------------------


def test_backup_distinguishes_absent_from_empty(db: Database) -> None:
    """The distinction rollback correctness depends on (ARCHITECTURE §5)."""
    db.execute(
        "INSERT INTO backups (backup_id, tweak_id, tweak_version, created_at, "
        "scope, target, old_value, old_value_absent, state) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        ("b1", "t", 1, utc_now(), "REGISTRY", "HKLM\\Was\\Empty", "", 0, "APPLIED"),
    )
    db.execute(
        "INSERT INTO backups (backup_id, tweak_id, tweak_version, created_at, "
        "scope, target, old_value, old_value_absent, state) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        ("b2", "t", 1, utc_now(), "REGISTRY", "HKLM\\Was\\Missing", None, 1, "APPLIED"),
    )

    empty = db.query_one("SELECT * FROM backups WHERE backup_id='b1'")
    missing = db.query_one("SELECT * FROM backups WHERE backup_id='b2'")
    assert empty is not None and missing is not None
    assert empty["old_value"] == "" and empty["old_value_absent"] == 0
    assert missing["old_value"] is None and missing["old_value_absent"] == 1


def test_interrupted_runs_are_findable(db: Database) -> None:
    """A row left in APPLYING is how a crash mid-change is detected."""
    for backup_id, state in (("a", "APPLIED"), ("b", "APPLYING"), ("c", "PENDING")):
        db.execute(
            "INSERT INTO backups (backup_id, tweak_id, tweak_version, created_at, "
            "scope, target, state) VALUES (?,?,?,?,?,?,?)",
            (backup_id, "t", 1, utc_now(), "REGISTRY", "HKLM\\X", state),
        )
    stuck = db.query("SELECT backup_id FROM backups WHERE state='APPLYING'")
    assert [r["backup_id"] for r in stuck] == ["b"]


# -- transactions ----------------------------------------------------------


def test_transaction_rolls_back_on_error(db: Database) -> None:
    with pytest.raises(RuntimeError):
        with db.transaction() as conn:
            conn.execute(
                "INSERT INTO snapshots (snapshot_id, created_at, name) VALUES (?,?,?)",
                ("s1", utc_now(), "half-written"),
            )
            raise RuntimeError("boom")

    assert db.query("SELECT * FROM snapshots") == []


def test_transaction_commits_on_success(db: Database) -> None:
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO snapshots (snapshot_id, created_at, name) VALUES (?,?,?)",
            ("s1", utc_now(), "kept"),
        )
    assert len(db.query("SELECT * FROM snapshots")) == 1


# -- errors ----------------------------------------------------------------


def test_record_error_persists_remedy(db: Database) -> None:
    from pudge_gaming_manager.utilities.exceptions import PrivilegeError

    db.record_error("power", PrivilegeError("change the active power plan"))
    row = db.query_one("SELECT * FROM errors")
    assert row is not None
    assert row["error_type"] == "PrivilegeError"
    assert "Administrator" in row["remedy"]


def test_bad_sql_raises_actionable_storage_error(db: Database) -> None:
    with pytest.raises(StorageError) as excinfo:
        db.execute("SELECT * FROM table_that_does_not_exist")
    assert excinfo.value.remedy


# -- planted data folder (another account created it first) -----------------

_FOREIGN_SID = "S-1-5-21-111-222-333-1005"


def test_a_data_folder_owned_by_another_account_is_refused(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pudge_gaming_manager.utilities import ownership
    from pudge_gaming_manager.utilities.exceptions import StorageError

    monkeypatch.setattr(ownership, "owner_sid", lambda _p: _FOREIGN_SID)
    monkeypatch.setattr(ownership, "current_user_sid", lambda: "S-1-5-21-9-9-9-1001")
    with pytest.raises(StorageError) as excinfo:
        Database(tmp_path / "planted" / "pgm.db")
    assert _FOREIGN_SID in excinfo.value.reason


@pytest.mark.parametrize(
    "owner",
    ["S-1-5-32-544", "S-1-5-18", "S-1-5-21-9-9-9-1001", None],
    ids=["administrators", "system", "current-user", "unreadable"],
)
def test_trusted_or_unreadable_owners_are_accepted(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, owner
) -> None:
    from pudge_gaming_manager.utilities import ownership

    monkeypatch.setattr(ownership, "owner_sid", lambda _p: owner)
    monkeypatch.setattr(ownership, "current_user_sid", lambda: "S-1-5-21-9-9-9-1001")
    Database(tmp_path / "ok" / "pgm.db").close()


def test_a_planted_log_folder_disables_file_logging(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pudge_gaming_manager.utilities import logging_setup, ownership

    monkeypatch.setattr(ownership, "owner_sid", lambda _p: _FOREIGN_SID)
    monkeypatch.setattr(ownership, "current_user_sid", lambda: "S-1-5-21-9-9-9-1001")
    logging_setup.setup_logging(tmp_path / "logs", console=False)
    assert not (tmp_path / "logs" / "application.log").exists()
    assert not (tmp_path / "logs" / "audit.log").exists()
