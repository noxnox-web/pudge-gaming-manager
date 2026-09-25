"""A Windows restore point before PGM changes anything.

PGM's own backups restore each setting it changed. A restore point is the
second net underneath: it covers the registry and system files as a whole,
and it works even if PGM itself is gone. It is not a substitute for the
per-change backups — it cannot undo one setting and keep the rest — so it
is taken *in addition*, and failing to take one never blocks a run.

Why it can fail, and why that is reported, not hidden
-----------------------------------------------------
* System Protection is off for the system drive (common on club images).
* Windows refuses more than one restore point in 24 hours by default
  (``SystemRestorePointCreationFrequency``); ``Checkpoint-Computer`` then
  writes a warning and creates nothing.

Both look like success from the exit code, so the newest restore point's
sequence number is compared before and after. Only a new number counts.
"""

from __future__ import annotations

from ...utilities.command_runner import CommandRunner
from ...utilities.exceptions import PgmError
from ...utilities.logging_setup import audit_event, get_logger
from ...utilities.powershell_runner import PowerShellRunner
from ...utilities.privileges import is_admin

_log = get_logger(__name__)

_SCRIPT = r"""
function Get-Newest {
  $points = @(Get-ComputerRestorePoint -ErrorAction SilentlyContinue)
  if ($points.Count -eq 0) { return 0 }
  return [int](($points | Measure-Object -Property SequenceNumber -Maximum).Maximum)
}
$before = Get-Newest
$err = ''
try {
  Checkpoint-Computer -Description $Desc -RestorePointType 'MODIFY_SETTINGS' `
    -WarningAction SilentlyContinue -ErrorAction Stop
} catch { $err = $_.Exception.Message }
[pscustomobject]@{ Before = $before; After = (Get-Newest); Error = $err }
"""


def create(
    description: str = "Pudge Cleaner: перед оптимизацией",
    powershell: PowerShellRunner | None = None,
) -> tuple[bool, str]:
    """Try to create a restore point. Returns ``(created, message)``."""
    if not is_admin():
        return False, "Точка восстановления не создана: нужны права администратора."

    runner = powershell or PowerShellRunner(CommandRunner())
    try:
        rows = runner.run_json(
            _SCRIPT,
            parameters={"Desc": description},
            timeout_s=300,
            operation="create restore point",
        )
    except PgmError as exc:
        return False, f"Точка восстановления не создана: {exc.reason or exc.what}"

    row = rows[0] if rows else {}
    before = int(row.get("Before") or 0)
    after = int(row.get("After") or 0)
    error = str(row.get("Error") or "")

    created = after > before
    audit_event(
        "restore_point", "create", target=description,
        result="SUCCESS" if created else "SKIPPED", error=error or None,
    )
    if created:
        return True, "Создана точка восстановления Windows."
    if error:
        return False, (
            f"Точка восстановления не создана: {error} Обычно это значит, что "
            "защита системы выключена для диска C:."
        )
    return False, (
        "Точка восстановления не создана: Windows разрешает не больше одной "
        "в сутки, и сегодня она уже была. Изменения всё равно сохраняются в "
        "собственный журнал отката программы."
    )


__all__ = ["create"]
