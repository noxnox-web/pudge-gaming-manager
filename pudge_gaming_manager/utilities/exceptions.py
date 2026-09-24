"""Exception hierarchy for Pudge Cleaner.

Every failure a user can encounter must explain *what* failed, *why*, and
*what to do about it* (rule #36). A bare traceback is never an acceptable
user-facing outcome, so the base exception carries those three fields
structurally rather than encouraging callers to bury them in a message string.
"""

from __future__ import annotations

from typing import Any


class PgmError(Exception):
    """Base for every Pudge Cleaner failure.

    Args:
        what: The operation that failed, in plain language.
            e.g. ``'Не удалось изменить службу Windows «Spooler»'``
        reason: Why it failed. e.g. ``"Отказано в доступе."``
        remedy: The concrete action that would fix it, or ``None`` when no
            user action can. e.g. ``"Запустите Pudge Cleaner от имени
            администратора."``
        context: Structured detail for logs. Never shown raw to the user.

    These three are written in Russian: they are the text the operator
    reads, and the interface around them is Russian. Docstrings, log lines
    and exception class names stay in English, for whoever reads the code.
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
            lines.append(f"\nПричина:  {self.reason}")
        if self.remedy:
            lines.append(f"Нужно:    {self.remedy}")
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
            what=f"Не удалось выполнить: {operation}",
            reason="Для этой операции нужны права администратора.",
            remedy="Запустите Pudge Cleaner от имени администратора.",
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
            what=f"Не удалось запустить нужную программу «{executable}»",
            reason="Программа не найдена в системе.",
            remedy=(
                f"Проверьте, что «{executable}» установлена и доступна в PATH."
            ),
            context=context,
        )
        self.executable = executable


class CommandTimeoutError(CommandError):
    def __init__(
        self, command: str, timeout_s: float, context: dict[str, Any] | None = None
    ) -> None:
        super().__init__(
            what=f"Команда не завершилась вовремя: {command}",
            reason=f"Ответа нет уже {timeout_s:.0f} с.",
            remedy=(
                "Возможно, система сильно загружена. Закройте запущенные игры "
                "и повторите."
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
        reason = (
            detail[0] if detail else f"Команда завершилась с кодом {exit_code}."
        )
        super().__init__(
            what=f"Команда завершилась ошибкой: {command}",
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
            what=f"Отказано в изменении защищённого ресурса «{resource}»",
            reason=reason,
            remedy=(
                "Ресурс защищён, потому что от него зависит клубное ПО. "
                "Если это ошибка, измените список защищённых приложений в "
                "настройках."
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
            what=f"Не удалось отменить изменение «{tweak_id}»",
            reason=reason,
            remedy=(
                "Верните эту настройку вручную. Исходное значение записано в "
                "журнале аудита (файл audit.log и таблица audit_log)."
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
            what=f"Профиль «{path}» отклонён",
            reason=problem,
            remedy=(
                "Используйте профиль, экспортированный Pudge Cleaner 1.0 или "
                "новее. Профили с неизвестными полями не принимаются."
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
            what=f"Не удалось выполнить: {operation}",
            reason=detail,
            remedy=(
                "Сделать ничего нельзя: этой возможности нужна другая версия "
                "Windows."
            ),
            context=context,
        )
