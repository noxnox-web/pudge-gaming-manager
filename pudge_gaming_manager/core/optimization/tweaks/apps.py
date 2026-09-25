"""Removing applications a club PC does not need.

These are the only tweaks in the program that take something away and
cannot put it back, so the honesty they owe the operator is different in
kind from the rest.

Why ``BackupScope.NONE``
------------------------
Not because there is nothing to lose — there is — but because there is
nothing this program can save that would bring it back. ``Remove-AppxPackage``
deletes the package and its local data; restoring means a fresh install from
the Store, which needs a network and an account and does not return the
data. Recording a "backup" that cannot restore would be worse than admitting
there is none, so the record says what was removed and the rollback says
plainly that it cannot be undone.

The preview marks every ``BackupScope.NONE`` row НЕОБРАТИМО, which is what
an operator actually needs to see before approving one.

Why MEDIUM
----------
An uninstalled application is a user-visible change to somebody else's
machine. MEDIUM keeps these behind the preview's explicit opt-in, so they
are never applied because nobody unticked them.
"""

from __future__ import annotations

from ....utilities.command_runner import CommandRunner
from ....utilities.logging_setup import get_logger
from ....utilities.powershell_runner import PowerShellRunner
from ....utilities.privileges import is_admin
from ....windows.apps import catalogue, inventory, onedrive
from ..tweak import (
    ApplyResult,
    BackupRecord,
    BackupScope,
    Outcome,
    RiskLevel,
    Tweak,
    TweakContext,
    TweakState,
    Validation,
    Verification,
)

_log = get_logger(__name__)


class RemoveAppGroupTweak(Tweak):
    """Uninstall the packages of one catalogue group."""

    abstract_base = True
    scope = BackupScope.NONE
    subsystem = "apps"
    risk = RiskLevel.MEDIUM
    requires_admin = False
    """Uninstalling for this user does not need elevation. Stopping the
    package returning for *new* users does, and is attempted separately —
    its failure is reported, not fatal."""

    group: catalogue.AppGroup

    def __init__(
        self,
        inventory_source: inventory.AppInventory | None = None,
        powershell: PowerShellRunner | None = None,
    ) -> None:
        self._inventory = inventory_source or inventory.AppInventory()
        self._powershell = powershell or PowerShellRunner(CommandRunner())
        self._present: list[inventory.InstalledApp] = []

    # -- lifecycle ---------------------------------------------------------

    def scan(self, ctx: TweakContext) -> TweakState:
        if self._inventory.error:
            return TweakState(
                needs_change=False,
                current_absent=True,
                summary=f"{self.name}: список приложений не прочитан",
            )

        self._present = self._inventory.find(self.group.packages)
        names = ", ".join(app.name for app in self._present)
        return TweakState(
            current_value=names,
            desired_value="",
            needs_change=bool(self._present),
            summary=(
                f"{self.name}: удалить {len(self._present)} — {names}"
                if self._present
                else f"{self.name}: нечего удалять"
            ),
        )

    def validate(self, ctx: TweakContext, state: TweakState) -> Validation:
        if state.current_absent:
            return Validation.refuse(
                "Не удалось получить список установленных приложений, "
                "поэтому неизвестно, что именно было бы удалено."
            )
        return Validation.allow()

    def backup(self, ctx: TweakContext, state: TweakState) -> BackupRecord:
        # Never called: the engine skips backup for BackupScope.NONE.
        raise NotImplementedError(
            "Удаление приложения необратимо и не бэкапится."
        )

    def apply(self, ctx: TweakContext, state: TweakState) -> ApplyResult:
        if ctx.dry_run:
            return ApplyResult(Outcome.SUCCESS, "Пробный прогон: ничего не удалено.")

        removed: list[str] = []
        failed: list[str] = []
        for app in self._present:
            ok, detail = inventory.remove(app, self._powershell)
            (removed if ok else failed).append(app.name if ok else detail)

        deprovisioned = self._deprovision(removed)

        if not removed:
            return ApplyResult(
                Outcome.FAILED,
                "Ничего не удалено. " + ("; ".join(failed) if failed else ""),
            )

        detail = f"Удалено приложений: {len(removed)} ({', '.join(removed)})."
        if deprovisioned:
            detail += f" Не вернутся у новых пользователей: {deprovisioned}."
        elif not is_admin():
            detail += (
                " У новых пользователей приложения появятся снова — для "
                "этого нужны права администратора."
            )
        if failed:
            detail += f" Не удалось: {'; '.join(failed)}."
        return ApplyResult(Outcome.SUCCESS, detail)

    def _deprovision(self, removed: list[str]) -> int:
        """Stop the removed packages reaching new profiles. Best effort."""
        if not removed or not is_admin():
            return 0
        provisioned = self._inventory.provisioned()
        count = 0
        for name in removed:
            package = provisioned.get(name.lower())
            if package and inventory.deprovision(package, self._powershell):
                count += 1
        return count

    def verify(self, ctx: TweakContext, state: TweakState) -> Verification:
        """Re-enumerate rather than trust the uninstall command.

        A package held open by a running process survives, and reporting it
        as removed would leave the operator believing a process stopped
        that is still running.
        """
        fresh = inventory.AppInventory(powershell=self._powershell)
        remaining = fresh.find(self.group.packages)
        if not remaining:
            return Verification.confirm("", "Ни одного из этих приложений больше нет.")
        names = ", ".join(app.name for app in remaining)
        return Verification.deny(names, f"Осталось установленным: {names}.")

    def rollback(self, ctx: TweakContext, backup: BackupRecord) -> ApplyResult:
        # Unreachable: with BackupScope.NONE the engine never creates a
        # backup, so it never calls this.
        return ApplyResult(
            Outcome.FAILED,
            "Удалённое приложение нельзя вернуть этой программой — его "
            "нужно установить заново из Магазина Windows.",
        )


class RemoveXboxAppsTweak(RemoveAppGroupTweak):
    """Xbox app and Game Bar, but not the Xbox Live sign-in broker."""

    id = "apps.remove.xbox"
    version = 1
    name = "Приложения Xbox"
    description = (
        "Удаляет приложение Xbox и игровую панель. Вход в Xbox Live не "
        "затрагивается: компонент, через который игры авторизуются, "
        "остаётся на месте."
    )
    rationale = (
        f"{catalogue.XBOX.rationale} Намеренно не удаляется "
        "Microsoft.XboxIdentityProvider: через него игры входят в Xbox "
        "Live, и без него не запускаются игры Game Pass, Minecraft, Forza "
        "и часть игр из Steam. Удалить его на игровом ПК — сломать ровно "
        "то, ради чего этот ПК стоит."
    )
    group = catalogue.XBOX


class RemoveWidgetsTweak(RemoveAppGroupTweak):
    """The Windows 11 widgets board."""

    id = "apps.remove.widgets"
    version = 1
    name = "Виджеты Windows"
    description = (
        "Удаляет панель виджетов Windows 11 и её фоновый процесс. "
        "На Windows 10 этого приложения нет, и твик пропускается."
    )
    rationale = catalogue.WIDGETS.rationale
    group = catalogue.WIDGETS


class RemoveTeamsConsumerTweak(RemoveAppGroupTweak):
    """The consumer Teams Windows 11 pins as "Chat"."""

    id = "apps.remove.teams_consumer"
    version = 1
    name = "Teams (личная версия)"
    description = (
        "Удаляет личный Teams, закреплённый в Windows 11 как «Чат». "
        "Рабочий Teams, если он установлен отдельно, не затрагивается."
    )
    rationale = catalogue.TEAMS.rationale
    group = catalogue.TEAMS


class RemoveUnusedAppsTweak(RemoveAppGroupTweak):
    """A named list of preinstalled Store applications."""

    id = "apps.remove.unused"
    version = 1
    name = "Неиспользуемые приложения Windows"
    description = (
        "Удаляет предустановленные приложения Магазина, перечисленные "
        "поимённо: новости, погода, пасьянс, устаревшие плееры, "
        "просмотрщики 3D и прочее, чему на клубном ПК нечего делать."
    )
    rationale = catalogue.UNUSED.rationale
    group = catalogue.UNUSED


class RemoveOneDriveTweak(Tweak):
    """Uninstall the OneDrive sync client, leaving its files alone."""

    id = "apps.remove.onedrive"
    version = 1
    name = "OneDrive"
    description = (
        "Удаляет клиент синхронизации OneDrive. Папка OneDrive с файлами "
        "остаётся на диске, а копии в облаке — в облаке."
    )
    rationale = (
        "Клиент OneDrive запускается при входе и постоянно висит в памяти, "
        "следя за папкой и синхронизируя её. На клубном ПК, где профиль "
        "стирается между сессиями, синхронизировать нечего. Удаляется "
        "штатным деинсталлятором — тем же, который вызывают «Параметры». "
        "Файлы не трогаются: удалять чужие документы ради одного процесса "
        "программа не будет."
    )
    risk = RiskLevel.MEDIUM
    subsystem = "apps"
    scope = BackupScope.NONE
    requires_admin = False

    def __init__(self, runner: CommandRunner | None = None) -> None:
        self._runner = runner or CommandRunner()

    def scan(self, ctx: TweakContext) -> TweakState:
        setup = onedrive.find_setup()
        return TweakState(
            current_value=str(setup) if setup else "",
            desired_value="",
            needs_change=setup is not None,
            summary=(
                "OneDrive: удалить клиент синхронизации"
                if setup
                else "OneDrive не установлен"
            ),
        )

    def validate(self, ctx: TweakContext, state: TweakState) -> Validation:
        return Validation.allow()

    def backup(self, ctx: TweakContext, state: TweakState) -> BackupRecord:
        raise NotImplementedError("Удаление OneDrive необратимо и не бэкапится.")

    def apply(self, ctx: TweakContext, state: TweakState) -> ApplyResult:
        if ctx.dry_run:
            return ApplyResult(Outcome.SUCCESS, "Пробный прогон: ничего не удалено.")
        ok, detail = onedrive.uninstall(self._runner)
        return ApplyResult(Outcome.SUCCESS if ok else Outcome.FAILED, detail)

    def verify(self, ctx: TweakContext, state: TweakState) -> Verification:
        if onedrive.is_installed():
            return Verification.deny(
                "installed", "Деинсталлятор OneDrive всё ещё на месте."
            )
        return Verification.confirm("", "OneDrive больше не установлен.")

    def rollback(self, ctx: TweakContext, backup: BackupRecord) -> ApplyResult:
        return ApplyResult(
            Outcome.FAILED,
            "OneDrive нужно установить заново с сайта Microsoft — этой "
            "программой вернуть его нельзя.",
        )


#: Built together so they share one inventory: ``Get-AppxPackage`` costs
#: about two seconds, and five tweaks asking separately would put that on
#: the preview five times.
def app_tweaks() -> list[Tweak]:
    """Every application-removal tweak, sharing a single inventory.

    The enumeration starts here rather than at the first scan, so it runs
    beside the tweaks the engine scans before reaching these.
    """
    shared = inventory.AppInventory()
    shared.prefetch()
    return [
        RemoveXboxAppsTweak(shared),
        RemoveWidgetsTweak(shared),
        RemoveTeamsConsumerTweak(shared),
        RemoveUnusedAppsTweak(shared),
        RemoveOneDriveTweak(),
    ]


__all__ = [
    "RemoveAppGroupTweak",
    "RemoveOneDriveTweak",
    "RemoveTeamsConsumerTweak",
    "RemoveUnusedAppsTweak",
    "RemoveWidgetsTweak",
    "RemoveXboxAppsTweak",
    "app_tweaks",
]
