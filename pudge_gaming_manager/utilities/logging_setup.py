"""Structured logging (rule #35).

Three sinks with different audiences and lifetimes:

``application.log``
    Everything. Rotated. For diagnosing PGM itself.
``audit.log``
    Every privileged operation and state change, append-only, never rewritten
    by the application. This is the record that answers "what did this tool do
    to my PC?" months later, so it is deliberately separate from the noisy
    application log and is not rotated away aggressively.
``errors.log``
    Warnings and above, so an operator can see what went wrong without
    reading the full log.

Audit records are emitted as one JSON object per line: greppable by a human,
parseable by a machine, and append-safe across concurrent writers.
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import os
import pathlib
import sys
from datetime import datetime, timezone
from typing import Any

from .ownership import untrusted_owner

#: Matches ``database.connection.APP_NAME``: both point at the same
#: ProgramData folder, and it keeps its original name so an upgrade does
#: not strand an existing machine's logs. See the note there.
APP_NAME = "PudgeGamingManager"
AUDIT_LOGGER_NAME = "pgm.audit"

_MAX_BYTES = 5 * 1024 * 1024
_APP_BACKUPS = 5
_AUDIT_BACKUPS = 50  # audit history is worth far more than disk space


def default_log_dir() -> pathlib.Path:
    """Return the log directory, preferring ProgramData on Windows.

    ProgramData is machine-scoped, which is correct for a club PC where the
    administrator and the player are different users.
    """
    if os.name == "nt":
        base = os.environ.get("PROGRAMDATA") or os.environ.get("ALLUSERSPROFILE")
        if base:
            return pathlib.Path(base) / APP_NAME / "logs"
    return pathlib.Path.home() / f".{APP_NAME.lower()}" / "logs"


class _JsonLineFormatter(logging.Formatter):
    """Formats audit records as one JSON object per line."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(
                record.created, tz=timezone.utc
            ).isoformat(timespec="milliseconds"),
            "level": record.levelname,
        }
        # Fields attached via logger.info(..., extra={"audit": {...}})
        audit = getattr(record, "audit", None)
        if isinstance(audit, dict):
            payload.update(audit)
        else:
            payload["message"] = record.getMessage()
        return json.dumps(payload, ensure_ascii=False, default=str)


def setup_logging(
    log_dir: pathlib.Path | None = None,
    *,
    level: int = logging.INFO,
    console: bool = True,
) -> pathlib.Path:
    """Configure the logging tree. Idempotent.

    Returns:
        The directory logs are being written to.

    Never raises on an unwritable directory: a club PC with a locked-down
    ProgramData must still run, just without file logs.
    """
    directory = log_dir or default_log_dir()
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)

    # Idempotence: clear handlers we installed previously.
    for handler in list(root.handlers):
        if getattr(handler, "_pgm", False):
            root.removeHandler(handler)
            handler.close()

    plain = logging.Formatter(
        "%(asctime)s %(levelname)-7s %(name)-38s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    writable = True
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError:
        writable = False

    # A log folder (or audit.log) another account created first could be
    # rewritten by that account; an audit trail it controls is worthless.
    # Run without file logs rather than write into it.
    planted = next(
        (
            p for p in (directory, directory / "audit.log")
            if writable and untrusted_owner(p) is not None
        ),
        None,
    )
    if planted is not None:
        writable = False

    if writable:
        try:
            app_handler = logging.handlers.RotatingFileHandler(
                directory / "application.log",
                maxBytes=_MAX_BYTES,
                backupCount=_APP_BACKUPS,
                encoding="utf-8",
            )
            app_handler.setLevel(level)
            app_handler.setFormatter(plain)
            app_handler._pgm = True  # type: ignore[attr-defined]
            root.addHandler(app_handler)

            error_handler = logging.handlers.RotatingFileHandler(
                directory / "errors.log",
                maxBytes=_MAX_BYTES,
                backupCount=_APP_BACKUPS,
                encoding="utf-8",
            )
            error_handler.setLevel(logging.WARNING)
            error_handler.setFormatter(plain)
            error_handler._pgm = True  # type: ignore[attr-defined]
            root.addHandler(error_handler)
        except OSError:
            writable = False

    if console:
        stream = logging.StreamHandler(sys.stderr)
        stream.setLevel(level)
        stream.setFormatter(plain)
        stream._pgm = True  # type: ignore[attr-defined]
        root.addHandler(stream)

    if planted is not None:
        logging.getLogger(__name__).error(
            "file logging disabled: %s is owned by another account and could "
            "be rewritten by it; delete it as Administrator to restore logs",
            planted,
        )

    _configure_audit_logger(directory if writable else None)
    return directory


def _configure_audit_logger(directory: pathlib.Path | None) -> None:
    audit = logging.getLogger(AUDIT_LOGGER_NAME)
    audit.setLevel(logging.INFO)
    # The audit trail is its own record; it must not be duplicated into the
    # application log, where rotation could discard it.
    audit.propagate = False

    for handler in list(audit.handlers):
        audit.removeHandler(handler)
        handler.close()

    if directory is None:
        return
    try:
        handler = logging.handlers.RotatingFileHandler(
            directory / "audit.log",
            maxBytes=_MAX_BYTES,
            backupCount=_AUDIT_BACKUPS,
            encoding="utf-8",
        )
    except OSError:
        return
    handler.setFormatter(_JsonLineFormatter())
    handler._pgm = True  # type: ignore[attr-defined]
    audit.addHandler(handler)


def audit_event(
    module: str,
    operation: str,
    *,
    target: str = "",
    old_state: Any = None,
    new_state: Any = None,
    result: str = "SUCCESS",
    error: str | None = None,
    **extra: Any,
) -> None:
    """Record one auditable operation.

    The field set matches rule #35: timestamp, module, operation, target,
    old_state, new_state, result, error.
    """
    record = {
        "module": module,
        "operation": operation,
        "target": target,
        "old_state": old_state,
        "new_state": new_state,
        "result": result,
        "error": error,
    }
    record.update(extra)
    logging.getLogger(AUDIT_LOGGER_NAME).info("", extra={"audit": record})


def get_logger(name: str) -> logging.Logger:
    """Return a module logger under the ``pgm`` namespace."""
    return logging.getLogger(name if name.startswith("pgm") else f"pgm.{name}")
