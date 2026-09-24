"""Windows service management (rule #52).

The governing rule here is rule #52's own wording: **never disable a service
because its name looks unnecessary.** Every service policy must carry a
rationale, and this module refuses to touch anything on the protected list
regardless of what a profile asks for.

Reading service state needs no elevation. Changing it does, and that is
checked before the call rather than after it fails.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from ...utilities.command_runner import CommandRunner
from ...utilities.exceptions import ProtectedResourceError, ServiceError
from ...utilities.logging_setup import audit_event, get_logger
from ...utilities.powershell_runner import PowerShellRunner
from ...utilities.privileges import require_admin

_log = get_logger(__name__)


class StartupType(str, Enum):
    """Service start mode, as Windows reports it."""

    AUTOMATIC = "Automatic"
    AUTOMATIC_DELAYED = "AutomaticDelayedStart"
    MANUAL = "Manual"
    DISABLED = "Disabled"
    UNKNOWN = "Unknown"


class ServiceState(str, Enum):
    RUNNING = "Running"
    STOPPED = "Stopped"
    PAUSED = "Paused"
    START_PENDING = "StartPending"
    STOP_PENDING = "StopPending"
    UNKNOWN = "Unknown"


#: Services PGM will never modify, whatever a profile or tweak requests.
#:
#: Two groups: Windows components whose loss breaks the machine, and club
#: infrastructure whose loss breaks the business. Matching is by service
#: name, case-insensitive, exact.
#:
#: **Untested against a real SmartShell installation** — no club software is
#: present on the development machine, so the club entries are taken from
#: documented service names and are `@unverified`.
PROTECTED_SERVICES: frozenset[str] = frozenset(
    name.lower()
    for name in (
        # Security. Never disabled for performance (rule #64).
        "WinDefend", "SecurityHealthService", "wscsvc", "MpsSvc", "mpssvc",
        "BFE", "SgrmBroker",
        # Core OS
        "RpcSs", "RpcEptMapper", "DcomLaunch", "LSM", "Power", "PlugPlay",
        "ProfSvc", "Schedule", "EventLog", "Themes", "AudioSrv",
        "AudioEndpointBuilder", "CryptSvc", "TrustedInstaller", "gpsvc",
        "SamSs", "Netlogon", "Dhcp", "Dnscache", "NlaSvc", "nsi",
        "WinHttpAutoProxySvc", "UserManager", "SystemEventsBroker",
        # Graphics and input
        "NVDisplay.ContainerLocalSystem", "AMD External Events Utility",
        "igfxCUIService", "HidServ", "hidserv",
        # Anti-cheat — breaking these gets players banned
        "EasyAntiCheat", "EasyAntiCheat_EOS", "BEService", "vgc", "vgk",
        "FACEIT", "ESEADriver2",
        # Launchers
        "Steam Client Service", "SteamService", "RiotClientService",
        "Battle.net Update Agent", "EpicOnlineServices",
        # Club management software
        "SmartShell", "SmartShellService", "SenetService", "LanGame",
        "GizmoService", "ClubAgent",
    )
)


@dataclass(frozen=True, slots=True)
class ServiceInfo:
    """One service's current configuration."""

    name: str
    display_name: str = ""
    state: ServiceState = ServiceState.UNKNOWN
    startup_type: StartupType = StartupType.UNKNOWN
    can_stop: bool = False
    description: str = ""

    @property
    def is_protected(self) -> bool:
        return self.name.lower() in PROTECTED_SERVICES


@dataclass(frozen=True, slots=True)
class ServiceBackup:
    """A service's state before PGM changed it."""

    name: str
    state: ServiceState
    startup_type: StartupType


# Status and StartType are .NET enums. ConvertTo-Json serialises an enum as
# its *numeric* value, so "Running" arrives as 4 and parses as UNKNOWN.
# ToString() is applied in PowerShell; the resulting names are English on
# every locale, unlike the localised DisplayName.
_QUERY = """
Get-Service -ErrorAction SilentlyContinue |
  Select-Object Name, DisplayName,
    @{ Name = 'Status'
       Expression = { if ($null -ne $_.Status) { $_.Status.ToString() } else { '' } } },
    @{ Name = 'StartType'
       Expression = { if ($null -ne $_.StartType) { $_.StartType.ToString() } else { '' } } },
    @{ Name = 'CanStop'; Expression = { [bool]$_.CanStop } }
"""

_QUERY_ONE = """
Get-Service -Name $ServiceName -ErrorAction SilentlyContinue |
  Select-Object Name, DisplayName,
    @{ Name = 'Status'
       Expression = { if ($null -ne $_.Status) { $_.Status.ToString() } else { '' } } },
    @{ Name = 'StartType'
       Expression = { if ($null -ne $_.StartType) { $_.StartType.ToString() } else { '' } } },
    @{ Name = 'CanStop'; Expression = { [bool]$_.CanStop } }
"""


class ServiceManager:
    """Queries and changes Windows services, refusing protected ones."""

    def __init__(
        self,
        runner: CommandRunner | None = None,
        powershell: PowerShellRunner | None = None,
    ) -> None:
        self.runner = runner or CommandRunner()
        self.powershell = powershell or PowerShellRunner(runner=self.runner)

    # -- read --------------------------------------------------------------

    def list_services(self) -> list[ServiceInfo]:
        """Every service on the machine. Needs no elevation."""
        rows = self.powershell.try_run_json(_QUERY, timeout_s=60)
        return [_to_info(row) for row in rows]

    def get_status(self, name: str) -> ServiceInfo | None:
        """One service, or ``None`` when it is not installed."""
        rows = self.powershell.try_run_json(
            _QUERY_ONE, parameters={"ServiceName": name}, timeout_s=30
        )
        return _to_info(rows[0]) if rows else None

    # -- guards ------------------------------------------------------------

    def _require_modifiable(self, name: str, operation: str) -> ServiceInfo:
        if name.lower() in PROTECTED_SERVICES:
            raise ProtectedResourceError(
                resource=name,
                reason=(
                    "This service is required by Windows, anti-cheat, a game "
                    "launcher or club management software."
                ),
                context={"operation": operation},
            )
        info = self.get_status(name)
        if info is None:
            raise ServiceError(
                what=f"Не удалось выполнить «{operation}» для службы «{name}»",
                reason="Такая служба на этом ПК не установлена.",
                remedy="Проверьте имя службы в настройках.",
            )
        return info

    # -- write -------------------------------------------------------------

    def set_startup_type(self, name: str, startup: StartupType) -> None:
        """Change how a service starts at boot.

        Refuses protected services, and refuses ``DISABLED`` outright for
        anything PGM ships: rule #52 requires a documented rationale per
        service, and there is no blanket permission to disable.
        """
        require_admin(f"change the startup type of service '{name}'")
        info = self._require_modifiable(name, "reconfigure")

        if startup is StartupType.UNKNOWN:
            raise ServiceError(
                what=f"Не удалось перенастроить службу «{name}»",
                reason="Не задан допустимый тип запуска.",
                remedy=None,
            )

        script = (
            "Set-Service -Name $ServiceName -StartupType $StartupType "
            "-ErrorAction Stop"
        )
        self.powershell.run(
            script,
            parameters={"ServiceName": name, "StartupType": startup.value},
            requires_admin=True,
            operation=f"change the startup type of service '{name}'",
            remedy="Запустите Pudge Cleaner от имени администратора.",
        )
        audit_event(
            "services", "set_startup_type", target=name,
            old_state=info.startup_type.value, new_state=startup.value,
        )

    def stop(self, name: str, *, timeout_s: float = 60.0) -> None:
        require_admin(f"stop service '{name}'")
        info = self._require_modifiable(name, "stop")
        if not info.can_stop:
            raise ServiceError(
                what=f"Не удалось остановить службу «{name}»",
                reason="Windows сообщает, что эта служба не принимает команду остановки.",
                remedy="Сделать ничего нельзя.",
            )

        self.powershell.run(
            "Stop-Service -Name $ServiceName -Force -ErrorAction Stop",
            parameters={"ServiceName": name},
            timeout_s=timeout_s,
            requires_admin=True,
            operation=f"stop service '{name}'",
        )
        audit_event(
            "services", "stop", target=name,
            old_state=info.state.value, new_state="Stopped",
        )

    def start(self, name: str, *, timeout_s: float = 60.0) -> None:
        require_admin(f"start service '{name}'")
        info = self._require_modifiable(name, "start")

        self.powershell.run(
            "Start-Service -Name $ServiceName -ErrorAction Stop",
            parameters={"ServiceName": name},
            timeout_s=timeout_s,
            requires_admin=True,
            operation=f"start service '{name}'",
        )
        audit_event(
            "services", "start", target=name,
            old_state=info.state.value, new_state="Running",
        )

    # -- backup / restore --------------------------------------------------

    def backup_state(self, name: str) -> ServiceBackup:
        """Capture a service's state so it can be put back."""
        info = self.get_status(name)
        if info is None:
            raise ServiceError(
                what=f"Не удалось сохранить состояние службы «{name}»",
                reason="Такая служба на этом ПК не установлена.",
                remedy="Проверьте имя службы в настройках.",
            )
        return ServiceBackup(
            name=info.name, state=info.state, startup_type=info.startup_type
        )

    def restore_state(self, backup: ServiceBackup) -> None:
        """Restore startup type first, then running state.

        Order matters: a disabled service cannot be started, so the startup
        type has to be corrected before the state is.
        """
        if backup.startup_type is not StartupType.UNKNOWN:
            self.set_startup_type(backup.name, backup.startup_type)

        current = self.get_status(backup.name)
        if current is None:
            return

        if backup.state is ServiceState.RUNNING and current.state is not ServiceState.RUNNING:
            self.start(backup.name)
        elif backup.state is ServiceState.STOPPED and current.state is ServiceState.RUNNING:
            self.stop(backup.name)


def _to_info(row: dict) -> ServiceInfo:
    return ServiceInfo(
        name=str(row.get("Name") or ""),
        display_name=str(row.get("DisplayName") or ""),
        state=_parse_enum(ServiceState, row.get("Status"), ServiceState.UNKNOWN),
        startup_type=_parse_enum(
            StartupType, row.get("StartType"), StartupType.UNKNOWN
        ),
        can_stop=bool(row.get("CanStop")),
    )


def _parse_enum(enum_class, raw, default):
    """Match a PowerShell enum name case-insensitively."""
    text = str(raw or "").strip().lower()
    if not text:
        return default
    for member in enum_class:
        if member.value.lower() == text:
            return member
    return default
