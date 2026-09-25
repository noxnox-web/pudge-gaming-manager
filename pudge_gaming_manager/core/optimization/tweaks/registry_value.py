"""One registry value, set and put back — the shape several tweaks share.

Three shipped tweaks are the same operation over different values: read a
DWORD, write a different one, verify the read-back, and on rollback either
restore the old number or delete the value if there was none. Writing that
lifecycle three times would mean three chances to get the absent-value case
wrong, and that case is the one that matters: a value PGM created and then
restored as ``0`` is not the same as a value that never existed, because
Windows reads a missing entry as "use the default", which is frequently not
zero.

Subclasses declare *what* to change. The lifecycle below, including every
safety step, is written once.

Everything here goes through :class:`RegistryManager`, so the key allowlist
applies: a subclass naming a key outside it is refused before the key is
even opened, and no tweak can widen that by declaring a path.
"""

from __future__ import annotations

from typing import Any

from ....utilities.exceptions import PgmError
from ....utilities.logging_setup import get_logger
from ....windows.registry.manager import Hive, RegistryManager, RegistryValue
from ..tweak import (
    ApplyResult,
    BackupRecord,
    BackupScope,
    Outcome,
    Tweak,
    TweakContext,
    TweakState,
    Validation,
    Verification,
)

_log = get_logger(__name__)

#: Marker recorded in a backup when the value did not exist beforehand.
ABSENT = "<absent>"


class RegistryValueTweak(Tweak):
    """Base for a tweak that sets a single registry value.

    Subclasses set :attr:`hive`, :attr:`subkey`, :attr:`value_name`,
    :attr:`desired_data` and :attr:`value_type`, plus the usual identity and
    rationale a tweak owes the operator.
    """

    abstract_base = True
    scope = BackupScope.REGISTRY

    #: Where the value lives.
    hive: Hive = Hive.HKLM
    subkey: str = ""
    value_name: str = ""

    #: What it should become, and how it is typed.
    desired_data: Any = 0
    value_type: str = "REG_DWORD"

    #: True when Windows only picks the new value up after a restart. The
    #: engine surfaces this in the report, so nobody is told a change is in
    #: force when it is queued.
    restart_required: bool = False

    def __init__(self, registry: RegistryManager | None = None) -> None:
        self._registry = registry or RegistryManager()

    # -- helpers -----------------------------------------------------------

    def _read(self) -> RegistryValue:
        return self._registry.read(self.hive, self.subkey, self.value_name)

    def describe(self, data: Any) -> str:
        """Render a value for the operator. Overridden for named states."""
        return str(data)

    # -- lifecycle ---------------------------------------------------------

    def scan(self, ctx: TweakContext) -> TweakState:
        try:
            current = self._read()
        except PgmError as exc:
            return TweakState(
                needs_change=False,
                current_absent=True,
                summary=f"{self.name}: не удалось прочитать — {exc.reason or exc.what}",
            )

        observed = ABSENT if current.absent else current.data
        matches = (not current.absent) and current.data == self.desired_data
        return TweakState(
            current_value=observed,
            desired_value=self.desired_data,
            needs_change=not matches,
            # Absent is a legitimate state, not a failure to read: it means
            # Windows is using its default. ``current_absent`` is reserved
            # for "could not be determined", which is a different thing and
            # blocks the change.
            current_absent=False,
            summary=(
                f"{self.name}: {self.describe(observed)} -> "
                f"{self.describe(self.desired_data)}"
                if not matches
                else f"{self.name}: уже {self.describe(self.desired_data)}"
            ),
        )

    def validate(self, ctx: TweakContext, state: TweakState) -> Validation:
        if state.current_absent:
            return Validation.refuse(
                f"Не удалось прочитать текущее значение «{self.value_name}», "
                "поэтому вернуть его обратно будет нечем."
            )
        return Validation.allow(requires_admin=self.hive.requires_admin)

    def backup(self, ctx: TweakContext, state: TweakState) -> BackupRecord:
        current = self._read()
        return BackupRecord(
            backup_id=BackupRecord.new_id(),
            tweak_id=self.id,
            tweak_version=self.version,
            scope=BackupScope.REGISTRY,
            target=f"{self.hive.value}\\{self.subkey}\\{self.value_name}",
            old_value=ABSENT if current.absent else current.data,
            old_value_absent=current.absent,
            old_value_kind=current.type_name or self.value_type,
            new_value=self.desired_data,
        )

    def apply(self, ctx: TweakContext, state: TweakState) -> ApplyResult:
        if ctx.dry_run:
            return ApplyResult(Outcome.SUCCESS, "Пробный прогон: значение не изменено.")
        try:
            self._registry.write(
                self.hive,
                self.subkey,
                self.value_name,
                self.desired_data,
                self.value_type,
            )
        except PgmError as exc:
            return ApplyResult(Outcome.FAILED, exc.reason or exc.what)

        detail = f"{self.name}: {self.describe(self.desired_data)}."
        if self.restart_required:
            detail += " Вступит в силу после перезагрузки."
        return ApplyResult(
            Outcome.SUCCESS, detail, needs_restart=self.restart_required
        )

    def verify(self, ctx: TweakContext, state: TweakState) -> Verification:
        """Confirm by reading the value back, not by trusting the write.

        The read-back proves the value is stored. For a setting Windows
        only consults at boot that is all it can prove, and the report says
        a restart is needed rather than implying the change is live.
        """
        try:
            current = self._read()
        except PgmError as exc:
            return Verification.deny(None, exc.reason or exc.what)

        if not current.absent and current.data == self.desired_data:
            note = (
                "Значение записано; вступит в силу после перезагрузки."
                if self.restart_required
                else "Windows подтверждает новое значение."
            )
            return Verification.confirm(current.data, note)
        return Verification.deny(
            ABSENT if current.absent else current.data,
            f"Ожидалось {self.describe(self.desired_data)}, а реестр сообщает "
            f"{self.describe(ABSENT if current.absent else current.data)}.",
        )

    def rollback(self, ctx: TweakContext, backup: BackupRecord) -> ApplyResult:
        try:
            if backup.old_value_absent:
                # Deleted, never written back as zero: Windows treats a
                # missing value as "use the default", and that default is
                # often not zero. Writing one would be a different setting
                # wearing the old one's name.
                self._registry.delete_value(
                    self.hive, self.subkey, self.value_name
                )
            else:
                self._registry.write(
                    self.hive,
                    self.subkey,
                    self.value_name,
                    backup.old_value,
                    str(backup.old_value_kind or self.value_type),
                )
        except PgmError as exc:
            return ApplyResult(Outcome.FAILED, exc.reason or exc.what)

        # Rollback is verified too.
        try:
            restored = self._read()
        except PgmError as exc:
            return ApplyResult(Outcome.FAILED, exc.reason or exc.what)

        if backup.old_value_absent:
            if restored.absent:
                return ApplyResult(Outcome.SUCCESS, f"{self.name}: значение удалено.")
            return ApplyResult(
                Outcome.FAILED,
                f"{self.name}: значение должно было исчезнуть, но оно на месте.",
            )
        if restored.data == backup.old_value:
            return ApplyResult(
                Outcome.SUCCESS,
                f"{self.name}: восстановлено {self.describe(backup.old_value)}.",
            )
        return ApplyResult(
            Outcome.FAILED,
            f"{self.name}: пытались вернуть "
            f"{self.describe(backup.old_value)}, а реестр сообщает "
            f"{self.describe(restored.data)}.",
        )


__all__ = ["ABSENT", "RegistryValueTweak"]
