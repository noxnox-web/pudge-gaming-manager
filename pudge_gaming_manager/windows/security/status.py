"""Security and update state, shown and never changed.

Some optimisation guides tell the reader to turn Defender off, disable
Windows Update, or switch off Memory Integrity for a few frames. Others say
the opposite. PGM takes no side by acting: every item here is read-only.
The operator sees where the PC stands — which is worth knowing, since a
disabled Defender on a club PC is a finding, not a tweak — and any change
is made by a person, in Windows' own Settings.

Sources, in order of preference: the registry values Windows' own Settings
pages read, then CIM classes, never localised text.
"""

from __future__ import annotations

import winreg
from dataclasses import dataclass, field

from ...utilities.command_runner import CommandRunner
from ...utilities.exceptions import PgmError
from ...utilities.logging_setup import get_logger
from ...utilities.powershell_runner import PowerShellRunner

_log = get_logger(__name__)

_HVCI = r"SYSTEM\CurrentControlSet\Control\DeviceGuard\Scenarios\HypervisorEnforcedCodeIntegrity"
_SECURE_BOOT = r"SYSTEM\CurrentControlSet\Control\SecureBoot\State"
_REBOOT_REQUIRED = (
    r"SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired"
)
_UPDATE_UX = r"SOFTWARE\Microsoft\WindowsUpdate\UX\Settings"

_CIM = r"""
$r = [ordered]@{}
try {
  $g = Get-CimInstance -Namespace root\Microsoft\Windows\DeviceGuard `
    -ClassName Win32_DeviceGuard -ErrorAction Stop
  $r.VbsStatus = [int]$g.VirtualizationBasedSecurityStatus
  $r.ServicesRunning = [string](@($g.SecurityServicesRunning) -join ',')
} catch { $r.VbsError = $_.Exception.Message }
try {
  $t = Get-CimInstance -Namespace root\cimv2\Security\MicrosoftTpm `
    -ClassName Win32_Tpm -ErrorAction Stop
  if ($t) {
    $r.TpmPresent = $true
    $r.TpmEnabled = [bool]$t.IsEnabled_InitialValue
    $r.TpmSpec = [string]$t.SpecVersion
  } else { $r.TpmPresent = $false }
} catch { $r.TpmError = $_.Exception.Message }
try {
  $m = Get-MpComputerStatus -ErrorAction Stop
  $r.DefenderService = [bool]$m.AMServiceEnabled
  $r.DefenderRealtime = [bool]$m.RealTimeProtectionEnabled
} catch { $r.DefenderError = $_.Exception.Message }
try {
  $r.Antivirus = [string](@(Get-CimInstance -Namespace root\SecurityCenter2 `
    -ClassName AntiVirusProduct -ErrorAction Stop |
    ForEach-Object { $_.displayName }) -join ', ')
} catch { }
try {
  $s = Get-Service -Name wuauserv -ErrorAction Stop
  $r.UpdateService = [string]$s.StartType
} catch { }
[pscustomobject]$r
"""


@dataclass(frozen=True, slots=True)
class Finding:
    """One line of the security picture."""

    name: str
    value: str
    ok: bool | None
    """``True`` fine, ``False`` worth attention, ``None`` informational."""

    note: str = ""


@dataclass(frozen=True, slots=True)
class SecurityStatus:
    findings: tuple[Finding, ...] = field(default_factory=tuple)


def _reg_value(path: str, name: str) -> object | None:
    try:
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE, path, 0,
            winreg.KEY_READ | winreg.KEY_WOW64_64KEY,
        ) as key:
            return winreg.QueryValueEx(key, name)[0]
    except OSError:
        return None


def _reg_key_exists(path: str) -> bool:
    try:
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE, path, 0,
            winreg.KEY_READ | winreg.KEY_WOW64_64KEY,
        ):
            return True
    except OSError:
        return False


def build(cim: dict, registry: dict) -> SecurityStatus:
    """Assemble the findings. Pure, so the wording is testable."""
    findings: list[Finding] = []

    running = {s for s in str(cim.get("ServicesRunning") or "").split(",") if s}
    hvci_configured = registry.get("hvci_enabled")
    if "VbsError" in cim and hvci_configured is None:
        findings.append(Finding("Целостность памяти (HVCI)", "не удалось прочитать", None))
    else:
        on = "2" in running
        findings.append(
            Finding(
                "Целостность памяти (HVCI)",
                "работает" if on else (
                    "включена, ждёт перезагрузки" if hvci_configured == 1 else "выключена"
                ),
                None,
                "Защита ядра от вредоносных драйверов. Гайды спорят, стоит ли "
                "выключать её ради кадров; программа её не меняет — это "
                "решение о безопасности, а не настройка производительности.",
            )
        )

    secure_boot = registry.get("secure_boot")
    findings.append(
        Finding(
            "Secure Boot",
            "не прочитано" if secure_boot is None else ("включён" if secure_boot else "выключен"),
            None if secure_boot is None else bool(secure_boot),
            "Нужен античитам Vanguard (Valorant) и FACEIT на Windows 11."
            if not secure_boot else "",
        )
    )

    if cim.get("TpmPresent") is False:
        findings.append(Finding("TPM", "не найден", False, "Нужен Windows 11 и ряду античитов."))
    elif cim.get("TpmPresent"):
        spec = str(cim.get("TpmSpec") or "").split(",")[0].strip()
        findings.append(
            Finding(
                "TPM",
                f"{'включён' if cim.get('TpmEnabled') else 'выключен'}"
                + (f", версия {spec}" if spec else ""),
                bool(cim.get("TpmEnabled")),
            )
        )
    else:
        findings.append(Finding("TPM", "не прочитано (нужны права администратора)", None))

    antivirus = str(cim.get("Antivirus") or "")
    if "DefenderError" in cim:
        findings.append(
            Finding(
                "Защитник Windows", "не отвечает", None,
                f"Установленный антивирус: {antivirus}." if antivirus else "",
            )
        )
    else:
        realtime = bool(cim.get("DefenderRealtime"))
        findings.append(
            Finding(
                "Защитник Windows",
                "защита в реальном времени включена" if realtime
                else "защита в реальном времени ВЫКЛЮЧЕНА",
                realtime or bool(antivirus and "defender" not in antivirus.lower()),
                "Программа Защитник не отключает: на клубном ПК, куда "
                "приносят флешки и ставят что угодно, это не оптимизация.",
            )
        )

    reboot = registry.get("update_reboot_pending", False)
    pause = registry.get("update_paused_until")
    service = str(cim.get("UpdateService") or "")
    update_value = "ждёт перезагрузки для установки" if reboot else "перезагрузка не требуется"
    if pause:
        update_value += f"; приостановлены до {str(pause)[:10]}"
    if service.lower() == "disabled":
        update_value += "; служба ОТКЛЮЧЕНА"
    findings.append(
        Finding(
            "Центр обновления Windows",
            update_value,
            not reboot and service.lower() != "disabled",
            "Программа обновления не отключает. Перед игровой сессией "
            "установку можно приостановить в «Параметрах»; отключённая "
            "служба — это ПК без исправлений безопасности.",
        )
    )
    return SecurityStatus(tuple(findings))


def read(powershell: PowerShellRunner | None = None) -> SecurityStatus:
    """Read the live state. Never raises."""
    registry = {
        "hvci_enabled": _reg_value(_HVCI, "Enabled"),
        "secure_boot": _reg_value(_SECURE_BOOT, "UEFISecureBootEnabled"),
        "update_reboot_pending": _reg_key_exists(_REBOOT_REQUIRED),
        "update_paused_until": _reg_value(_UPDATE_UX, "PauseUpdatesExpiryTime"),
    }
    runner = powershell or PowerShellRunner(CommandRunner())
    try:
        rows = runner.run_json(_CIM, timeout_s=90, operation="security status")
        cim = rows[0] if rows else {}
    except PgmError as exc:
        _log.warning("security status query failed: %s", exc.what)
        cim = {"VbsError": exc.what, "DefenderError": exc.what}
    return build(cim, registry)


__all__ = ["Finding", "SecurityStatus", "build", "read"]
