"""Enumerating and removing Store applications.

Removing an application is not like changing a setting: it cannot be undone
by this program. ``Remove-AppxPackage`` deletes the package and its local
data, and putting it back means a fresh install from the Store — which
needs a network, an account, and does not bring the data back. So the
safety here cannot be "we can restore it afterwards"; it has to be
"we never remove something we were not explicitly told to".

Three guards
------------
1. **An explicit catalogue.** Only names listed in ``catalogue.py`` are ever
   considered. There is no "remove everything unused" mode, because nothing
   in an inventory tells you whether a person uses an application.
2. **Store-signed only.** ``SignatureKind`` distinguishes an application
   from an operating-system component. ``Microsoft.Windows.ShellExperienceHost``
   and ``Microsoft.XboxGameCallableUI`` are ``System``: they are parts of
   the shell, they are not what anybody means by "an app", and removing one
   breaks the desktop. A ``System`` package is refused here even if some
   future catalogue entry names it.
3. **Never a framework.** Runtime packages other applications link against.

Provisioned packages
--------------------
Removing a package uninstalls it for the current user. The *provisioned*
copy is what Windows installs into each new profile, so on a club PC where
profiles are recreated, leaving it means the application returns. Removing
that needs elevation and is done separately, so a failure to do so is
reported rather than silently leaving the job half done.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field

from ...utilities.exceptions import PgmError
from ...utilities.command_runner import CommandRunner
from ...utilities.logging_setup import audit_event, get_logger
from ...utilities.powershell_runner import PowerShellRunner

_log = get_logger(__name__)

#: Signature kinds that are operating-system components rather than apps.
#: ``Get-AppxPackage`` reports ``System`` for these.
PROTECTED_SIGNATURE_KINDS = frozenset({"System"})

_INVENTORY_QUERY = r"""
@(Get-AppxPackage | Where-Object { -not $_.IsFramework } | ForEach-Object {
  [pscustomobject]@{
    Name      = [string]$_.Name
    Full      = [string]$_.PackageFullName
    Signature = [string]$_.SignatureKind
  }
})
"""

_PROVISIONED_QUERY = r"""
@(Get-AppxProvisionedPackage -Online -ErrorAction SilentlyContinue |
  ForEach-Object {
    [pscustomobject]@{
      Name = [string]$_.DisplayName
      Full = [string]$_.PackageName
    }
  })
"""


@dataclass(frozen=True, slots=True)
class InstalledApp:
    """One installed package, as Windows reports it."""

    name: str
    full_name: str
    signature: str

    @property
    def is_system_component(self) -> bool:
        """True for a part of Windows rather than an application."""
        return self.signature in PROTECTED_SIGNATURE_KINDS


@dataclass(slots=True)
class AppInventory:
    """What is installed, fetched once and shared by every app tweak.

    ``Get-AppxPackage`` costs about two seconds, and a pass offers several
    app tweaks. Each one asking separately would put that on the preview
    five times over, so the first scan fetches and the rest read.
    """

    powershell: PowerShellRunner | None = None
    _apps: dict[str, InstalledApp] | None = field(default=None, init=False)
    _provisioned: dict[str, str] | None = field(default=None, init=False)
    _error: str = field(default="", init=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False)
    _prefetch: threading.Thread | None = field(default=None, init=False)

    def _runner(self) -> PowerShellRunner:
        if self.powershell is None:
            self.powershell = PowerShellRunner(CommandRunner())
        return self.powershell

    @property
    def error(self) -> str:
        """Why the inventory is unavailable, or empty when it is fine."""
        self.installed()
        return self._error

    def prefetch(self) -> None:
        """Start the enumeration now, in the background.

        ``Get-AppxPackage`` costs about two seconds, and the plan scans
        several other tweaks — the cleanup sweep, three powercfg queries,
        four registry reads — before it reaches the application ones.
        Starting the call when the plan is built lets it run beside those
        instead of after them, which is the difference between the preview
        taking five seconds and taking three.

        Safe to call more than once; the second call finds a thread
        already running and returns.
        """
        with self._lock:
            if self._apps is not None or self._prefetch is not None:
                return
            self._prefetch = threading.Thread(
                target=self.installed, name="pgm-app-inventory", daemon=True
            )
            self._prefetch.start()

    def installed(self) -> dict[str, InstalledApp]:
        """Name -> package, for every non-framework package of this user.

        Fetched once. A caller arriving while the prefetch is still running
        waits for it rather than starting a second PowerShell process.
        """
        thread = self._prefetch
        if thread is not None and thread is not threading.current_thread():
            thread.join()

        if self._apps is not None:
            return self._apps

        try:
            rows = self._runner().run_json(
                _INVENTORY_QUERY, timeout_s=120, operation="list installed apps"
            )
        except PgmError as exc:
            self._error = exc.reason or exc.what
            _log.warning("could not list installed apps: %s", self._error)
            self._apps = {}
            return self._apps

        self._apps = {}
        for row in rows:
            name = str(row.get("Name") or "")
            if not name:
                continue
            self._apps[name.lower()] = InstalledApp(
                name=name,
                full_name=str(row.get("Full") or ""),
                signature=str(row.get("Signature") or ""),
            )
        return self._apps

    def provisioned(self) -> dict[str, str]:
        """Display name -> package name, for packages given to new users."""
        if self._provisioned is not None:
            return self._provisioned

        try:
            rows = self._runner().try_run_json(_PROVISIONED_QUERY, timeout_s=120)
        except PgmError:
            rows = []
        self._provisioned = {
            str(row.get("Name") or "").lower(): str(row.get("Full") or "")
            for row in (rows or [])
            if row.get("Name")
        }
        return self._provisioned

    def find(self, names: tuple[str, ...]) -> list[InstalledApp]:
        """The catalogue entries that are present and removable here.

        A ``System``-signed package matching a catalogue name is left out
        and logged: the catalogue should not have named it, and refusing is
        cheaper than discovering what depended on it.
        """
        installed = self.installed()
        found: list[InstalledApp] = []
        for name in names:
            app = installed.get(name.lower())
            if app is None:
                continue
            if app.is_system_component:
                _log.warning(
                    "refusing %s: signed as %s, an OS component rather than "
                    "an app", app.name, app.signature,
                )
                continue
            found.append(app)
        return found


def remove(app: InstalledApp, powershell: PowerShellRunner) -> tuple[bool, str]:
    """Uninstall one package for the current user.

    Refuses a system component here as well as in :meth:`AppInventory.find`:
    this function is reachable on its own, and a guard that only exists on
    one path is a guard with a way around it.
    """
    if app.is_system_component:
        return False, f"{app.name}: компонент Windows, удалять нельзя."

    try:
        powershell.run(
            "Get-AppxPackage -Name $Name | Remove-AppxPackage -ErrorAction Stop",
            parameters={"Name": app.name},
            timeout_s=180,
            operation=f"remove {app.name}",
        )
    except PgmError as exc:
        audit_event(
            "apps", "remove", target=app.name, result="FAILED",
            error=exc.reason or exc.what,
        )
        return False, f"{app.name}: {exc.reason or exc.what}"

    audit_event("apps", "remove", target=app.full_name or app.name, result="SUCCESS")
    return True, ""


def deprovision(package_name: str, powershell: PowerShellRunner) -> bool:
    """Stop Windows installing a package into new user profiles.

    Needs elevation. Reported as a partial result rather than a failure:
    the application is gone for this user either way, and on a club PC the
    difference shows up only when the next profile is created.
    """
    try:
        powershell.run(
            "Remove-AppxProvisionedPackage -Online -PackageName $Package "
            "-ErrorAction Stop | Out-Null",
            parameters={"Package": package_name},
            timeout_s=180,
            operation=f"deprovision {package_name}",
        )
    except PgmError as exc:
        _log.info("could not deprovision %s: %s", package_name, exc.what)
        return False
    audit_event("apps", "deprovision", target=package_name, result="SUCCESS")
    return True


__all__ = [
    "PROTECTED_SIGNATURE_KINDS",
    "AppInventory",
    "InstalledApp",
    "deprovision",
    "remove",
]
