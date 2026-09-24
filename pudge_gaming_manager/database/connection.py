"""SQLite connection management and migrations.

WAL mode is enabled so the background agent can read while the GUI writes
without either blocking the other — on a club PC these are separate processes
touching the same database.
"""

from __future__ import annotations

import json
import os
import pathlib
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator

from ..utilities.exceptions import StorageError
from ..utilities.ownership import untrusted_owner
from .schema import MIGRATIONS, SCHEMA_VERSION

APP_NAME = "PudgeGamingManager"
DB_FILENAME = "pgm.db"


def default_database_path() -> pathlib.Path:
    """Machine-scoped database location.

    ProgramData, not the user profile: the administrator who optimises the PC
    and the player who uses it are different Windows accounts, and the
    configuration history belongs to the machine.
    """
    if os.name == "nt":
        base = os.environ.get("PROGRAMDATA") or os.environ.get("ALLUSERSPROFILE")
        if base:
            return pathlib.Path(base) / APP_NAME / DB_FILENAME
    return pathlib.Path.home() / f".{APP_NAME.lower()}" / DB_FILENAME


def utc_now() -> str:
    """Timestamp in ISO-8601 UTC, the single time format used in storage."""
    return datetime.now(tz=timezone.utc).isoformat(timespec="seconds")


class Database:
    """Owns the SQLite connection and applies migrations on open.

    One instance per process. Connections are per-thread because SQLite
    objects are not safe to share across threads, and the GUI scans on a
    worker thread while the UI thread reads.
    """

    def __init__(self, path: pathlib.Path | str | None = None) -> None:
        self.path = pathlib.Path(path) if path is not None else default_database_path()
        self._local = threading.local()
        self._migration_lock = threading.Lock()
        self._prepare_directory()
        self.migrate()

    # -- lifecycle ---------------------------------------------------------

    def _prepare_directory(self) -> None:
        if str(self.path) == ":memory:":
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise StorageError(
                what=f"Cannot create the data folder '{self.path.parent}'",
                reason=str(exc),
                remedy=(
                    "Run Pudge Gaming Manager as Administrator, or choose a "
                    "different data folder in Settings."
                ),
            ) from exc
        self._refuse_planted_storage()

    def _refuse_planted_storage(self) -> None:
        """Refuse a data folder or database another account created first.

        Any user may create folders in ProgramData, and a folder's creator
        owns it. Whoever owns this folder can rewrite the backups rollback
        restores and the audit trail, so it must belong to the system, the
        Administrators group, or the account running PGM now.
        """
        for target in (self.path.parent, self.path):
            owner = untrusted_owner(target)
            if owner is not None:
                raise StorageError(
                    what=f"Refused to use '{target}'",
                    reason=(
                        f"It is owned by another account ({owner}), so that "
                        "account could rewrite PGM's backups and audit log."
                    ),
                    remedy=(
                        "Delete this folder as Administrator and start Pudge "
                        "Gaming Manager again; it will be recreated safely."
                    ),
                    context={"path": str(target), "owner": owner},
                )

    @property
    def connection(self) -> sqlite3.Connection:
        """The calling thread's connection, opened on first use."""
        existing = getattr(self._local, "conn", None)
        if existing is not None:
            return existing

        try:
            conn = sqlite3.connect(
                str(self.path),
                timeout=15.0,
                isolation_level=None,  # explicit transactions only
                check_same_thread=False,
            )
        except sqlite3.Error as exc:
            raise StorageError(
                what=f"Cannot open the database '{self.path}'",
                reason=str(exc),
                remedy=(
                    "Check that the file is not read-only and that another "
                    "copy of Pudge Gaming Manager is not holding it."
                ),
            ) from exc

        conn.row_factory = sqlite3.Row
        # WAL: concurrent reader (agent) + writer (GUI) without blocking.
        # In-memory databases do not support WAL.
        if str(self.path) != ":memory:":
            conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA synchronous=FULL")  # a backup row must survive power loss
        self._local.conn = conn
        return conn

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None

    # -- transactions ------------------------------------------------------

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Run a block inside one transaction, rolling back on any exception.

        ``IMMEDIATE`` takes the write lock up front so two processes cannot
        both read-then-write and produce a lost update.
        """
        conn = self.connection
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        conn.execute("COMMIT")

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> sqlite3.Cursor:
        try:
            return self.connection.execute(sql, params)
        except sqlite3.Error as exc:
            raise StorageError(
                what="A database operation failed",
                reason=str(exc),
                remedy="Check available disk space, then restart the application.",
                context={"sql": sql[:300]},
            ) from exc

    def query(self, sql: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
        return list(self.execute(sql, params).fetchall())

    def query_one(self, sql: str, params: tuple[Any, ...] = ()) -> sqlite3.Row | None:
        return self.execute(sql, params).fetchone()

    # -- migrations --------------------------------------------------------

    def current_version(self) -> int:
        row = self.query_one(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name='schema_migrations'"
        )
        if row is None:
            return 0
        result = self.query_one("SELECT MAX(version) AS v FROM schema_migrations")
        return int(result["v"]) if result and result["v"] is not None else 0

    def migrate(self) -> int:
        """Apply pending migrations. Returns the resulting schema version.

        Each version is applied inside its own transaction, so a failure
        halfway leaves the database at the last complete version rather than
        in a half-migrated state.
        """
        with self._migration_lock:
            version = self.current_version()
            for target in sorted(MIGRATIONS):
                if target <= version:
                    continue
                with self.transaction() as conn:
                    for statement in MIGRATIONS[target]:
                        conn.execute(statement)
                    conn.execute(
                        "INSERT OR REPLACE INTO schema_migrations "
                        "(version, applied_at) VALUES (?, ?)",
                        (target, utc_now()),
                    )
                version = target
            return version

    # -- shared helpers ----------------------------------------------------

    def record_audit(
        self,
        module: str,
        operation: str,
        *,
        target: str = "",
        old_state: Any = None,
        new_state: Any = None,
        result: str = "SUCCESS",
        error: str | None = None,
        username: str | None = None,
        elevated: bool = False,
    ) -> None:
        """Append one row to the append-only audit log."""
        self.execute(
            "INSERT INTO audit_log (occurred_at, module, operation, target, "
            "old_state, new_state, result, error, username, elevated) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                utc_now(),
                module,
                operation,
                target,
                _as_text(old_state),
                _as_text(new_state),
                result,
                error,
                username or os.environ.get("USERNAME", ""),
                1 if elevated else 0,
            ),
        )

    def record_error(self, module: str, error: Any) -> None:
        """Persist a :class:`PgmError` (or any exception) for later diagnosis."""
        as_dict = getattr(error, "to_dict", None)
        data = (
            as_dict()
            if callable(as_dict)
            else {
                "type": type(error).__name__,
                "what": str(error),
                "reason": "",
                "remedy": None,
                "context": {},
            }
        )
        self.execute(
            "INSERT INTO errors (occurred_at, module, error_type, what, "
            "reason, remedy, context) VALUES (?,?,?,?,?,?,?)",
            (
                utc_now(),
                module,
                data["type"],
                data["what"],
                data.get("reason"),
                data.get("remedy"),
                _as_text(data.get("context")),
            ),
        )


def _as_text(value: Any) -> str | None:
    """Serialise a value for a TEXT column, JSON-encoding structures."""
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float, bool)):
        return str(value)
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(value)


__all__ = ["Database", "default_database_path", "utc_now", "SCHEMA_VERSION"]
