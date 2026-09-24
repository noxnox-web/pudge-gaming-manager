"""Exception hierarchy for Pudge Gaming Manager.

Every failure a user can encounter must explain *what* failed, *why*, and
*what to do about it* (rule #36). A bare traceback is never an acceptable
user-facing outcome, so the base exception carries those three fields
structurally rather than encouraging callers to bury them in a message string.
"""

from __future__ import annotations

from typing import Any


class PgmError(Exception):
    """Base for every Pudge Gaming Manager failure.

    Args:
        what: The operation that failed, in plain language.
            e.g. ``'Failed to modify Windows service "Spooler"'``
        reason: Why it failed. e.g. ``"Access denied."``
        remedy: The concrete action that would fix it, or ``None`` when no
            user action can. e.g. ``"Run Pudge Gaming Manager as Administrator."``
        context: Structured detail for logs. Never shown raw to the user.
    """

    def __init__(
        self,
        what: str,
        reason: str = "",
        remedy: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> None:
        self.what = what
        self.reason = reason
        self.remedy = remedy
        self.context: dict[str, Any] = context or {}
        super().__init__(self.user_message())

    def user_message(self) -> str:
        """Render the failure as the operator should see it."""
        lines = [self.what if self.what.endswith((".", "!", "?")) else f"{self.what}."]
        if self.reason:
            lines.append(f"\nReason:   {self.reason}")
        if self.remedy:
            lines.append(f"Required: {self.remedy}")
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        """Serialise for the ``errors`` table and structured logs."""
        return {
            "type": type(self).__name__,
            "what": self.what,
            "reason": self.reason,
            "remedy": self.remedy,
            "context": self.context,
        }


# --------------------------------------------------------------------------
# Privilege
# --------------------------------------------------------------------------


class PrivilegeError(PgmError):
    """An operation required elevation that the current process lacks."""

    def __init__(self, operation: str, context: dict[str, Any] | None = None) -> None:
        super().__init__(
            what=f"Cannot perform: {operation}",
            reason="This operation requires administrator privileges.",
            remedy="Run Pudge Gaming Manager as Administrator.",
            context=context,
        )
        self.operation = operation


# --------------------------------------------------------------------------
# External commands
# --------------------------------------------------------------------------


class CommandError(PgmError):
    """Base for failures running an external command."""


class CommandNotFoundError(CommandError):
    def __init__(self, executable: str, context: dict[str, Any] | None = None) -> None:
        super().__init__(
            what=f"Cannot run required program '{executable}'",
            reason="The program was not found on this system.",
            remedy=(
                f"Confirm that '{executable}' is installed and available on PATH."
            ),
            context=context,
        )
        self.executable = executable


class CommandTimeoutError(CommandError):
    def __init__(
        self, command: str, timeout_s: float, context: dict[str, Any] | None = None
    ) -> None:
        super().__init__(
            what=f"Command did not finish in time: {command}",
            reason=f"No response after {timeout_s:.0f} seconds.",
            remedy=(
                "The system may be under heavy load. Close running games and "
                "try again."
            ),
            context=context,
        )
        self.command = command
        self.timeout_s = timeout_s


class CommandFailedError(CommandError):
    """A command ran to completion but reported failure."""

    def __init__(
        self,
        command: str,
        exit_code: int,
        stderr: str = "",
        remedy: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> None:
        detail = stderr.strip().splitlines()
        reason = detail[0] if detail else f"The command exited with code {exit_code}."
        super().__init__(
            what=f"Command failed: {command}",
            reason=reason,
            remedy=remedy,
            context=context,
        )
        self.command = command
        self.exit_code = exit_code
        self.stderr = stderr


# --------------------------------------------------------------------------
# Windows subsystems
# --------------------------------------------------------------------------


class RegistryError(PgmError):
    """A registry read, write, delete or restore failed."""


class ServiceError(PgmError):
    """A Windows service query or state change failed."""


class ProtectedResourceError(PgmError):
    """An operation was refused because the target is a protected resource.

    Club infrastructure (SmartShell, launchers, anti-cheat) is excluded from
    every destructive operation. This is a refusal, not a malfunction, and it
    has no override flag in v1.0 (rule #47).
    """

    def __init__(
        self, resource: str, reason: str, context: dict[str, Any] | None = None
    ) -> None:
        super().__init__(
            what=f"Refused to modify protected resource '{resource}'",
            reason=reason,
            remedy=(
                "This resource is protected because club software depends on it. "
                "Edit the protected-applications list in Settings if this is wrong."
            ),
            context=context,
        )
        self.resource = resource


# --------------------------------------------------------------------------
# Tweak lifecycle
# --------------------------------------------------------------------------


class TweakError(PgmError):
    """Base for failures in the scan/validate/backup/apply/verify lifecycle."""

    def __init__(
        self,
        tweak_id: str,
        what: str,
        reason: str = "",
        remedy: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(what=what, reason=reason, remedy=remedy, context=context)
        self.tweak_id = tweak_id


class TweakValidationError(TweakError):
    """The tweak cannot be applied safely on this system."""


class TweakBackupError(TweakError):
    """Prior state could not be captured, so the change must not proceed.

    The engine treats this as fatal for the tweak: without a durable backup
    there is no rollback path, and an unrollbackable change is not permitted
    regardless of its risk level.
    """


class TweakApplyError(TweakError):
    """The mutation itself failed."""


class TweakVerificationError(TweakError):
    """The change was applied but re-reading did not confirm it took effect."""


class RollbackError(TweakError):
    """Restoring prior state failed.

    This is the most serious failure in the system: the machine may be in a
    state the operator did not choose. It is always surfaced, never swallowed.
    """

    def __init__(
        self,
        tweak_id: str,
        reason: str,
        context: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            tweak_id=tweak_id,
            what=f"Could not undo change '{tweak_id}'",
            reason=reason,
            remedy=(
                "Restore this setting manually. Its original value is recorded "
                "in the audit log (audit.log, and the audit_log table)."
            ),
            context=context,
        )


# --------------------------------------------------------------------------
# Storage and configuration
# --------------------------------------------------------------------------


class StorageError(PgmError):
    """The local database could not be opened, read or written."""


class ProfileError(PgmError):
    """A profile file is unreadable, invalid, or refers to unknown settings."""


class ProfileSchemaError(ProfileError):
    """A profile failed schema validation and was rejected before use.

    Profiles are untrusted input: they arrive on USB sticks and network shares.
    Rejection happens before any value is read (rule #56).
    """

    def __init__(
        self, path: str, problem: str, context: dict[str, Any] | None = None
    ) -> None:
        super().__init__(
            what=f"Rejected profile '{path}'",
            reason=problem,
            remedy=(
                "Use a profile exported by Pudge Gaming Manager 1.0 or later. "
                "Profiles are not accepted if they contain unknown fields."
            ),
            context=context,
        )
        self.path = path


class UnsupportedPlatformError(PgmError):
    """The operation is not supported on the running Windows version."""

    def __init__(
        self, operation: str, detail: str, context: dict[str, Any] | None = None
    ) -> None:
        super().__init__(
            what=f"Cannot perform: {operation}",
            reason=detail,
            remedy="No action available. This feature requires a different Windows version.",
            context=context,
        )
