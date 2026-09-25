"""Physical network adapters, their drivers, and their advanced properties.

The «Дополнительно» tab of an adapter in Device Manager, read through
``Get-NetAdapterAdvancedProperty``. Properties are identified by their
*registry keyword*, never their display name: display names are localised
and vendor-worded ("Energy-Efficient Ethernet", "Энергосберегающий
Ethernet", "Green Ethernet"), while the standardised NDIS keywords such as
``*EEE`` and ``*InterruptModeration`` are the same on every machine.

Changing a property goes through ``Set-NetAdapterAdvancedProperty`` with
``-NoRestart``: the new value is stored at once and the driver picks it up
when the adapter next restarts. Restarting it here would drop the network
mid-session on a club PC, which is a worse outcome than a change that
waits for the next boot — and the report says it waits.
"""

from __future__ import annotations

from dataclasses import dataclass

from ...utilities.command_runner import CommandRunner
from ...utilities.logging_setup import audit_event, get_logger
from ...utilities.powershell_runner import PowerShellRunner

_log = get_logger(__name__)

_QUERY = r"""
Get-NetAdapter -Physical -ErrorAction Stop | ForEach-Object {
  $a = $_
  $props = @(Get-NetAdapterAdvancedProperty -Name $a.Name -AllProperties `
      -ErrorAction SilentlyContinue | Where-Object { $_.RegistryKeyword } |
    ForEach-Object {
      [pscustomobject]@{
        Keyword = [string]$_.RegistryKeyword
        Display = [string]$_.DisplayName
        Value = [string](@($_.RegistryValue) -join ',')
        DisplayValue = [string]$_.DisplayValue
        Valid = [string](@($_.ValidRegistryValues) -join ',')
      }
    })
  [pscustomobject]@{
    Name = [string]$a.Name
    Description = [string]$a.InterfaceDescription
    Status = [string]$a.Status
    LinkSpeed = [string]$a.LinkSpeed
    Wired = ([string]$a.PhysicalMediaType -eq '802.3')
    PnpId = [string]$a.PnPDeviceID
    DriverVersion = [string]$a.DriverVersion
    DriverDate = [string]$a.DriverDate
    DriverProvider = [string]$a.DriverProvider
    NdisVersion = [string]$a.NdisVersion
    Properties = $props
  }
}
"""

_SET = r"""
Set-NetAdapterAdvancedProperty -Name $Adapter -RegistryKeyword $Keyword `
  -RegistryValue $Value -NoRestart -ErrorAction Stop
"""

#: Keywords that turn Energy-Efficient Ethernet on or off. ``*EEE`` is the
#: NDIS standard; the rest are vendor spellings of the same feature. In all
#: of them ``0`` means off — and a keyword is only touched when the driver
#: lists ``0`` among its valid values.
EEE_KEYWORDS: tuple[str, ...] = (
    "*EEE",
    "EEELinkAdvertisement",  # Intel
    "EnableGreenEthernet",   # Realtek
    "AdvancedEEE",           # Realtek
)

#: Shown in the audit with an explanation, never changed: the sources
#: disagree on each, and the right value depends on the NIC, the driver and
#: the rest of the machine.
DIAGNOSTIC_KEYWORDS: dict[str, str] = {
    "*InterruptModeration": "Модерация прерываний",
    "*RSS": "Receive Side Scaling",
    "*JumboPacket": "Jumbo-кадры",
    "*FlowControl": "Управление потоком",
    "*ReceiveBuffers": "Буферы приёма",
    "*TransmitBuffers": "Буферы передачи",
    "*LsoV2IPv4": "Разгрузка LSO (IPv4)",
    "*IPChecksumOffloadIPv4": "Разгрузка контрольных сумм IPv4",
    "*WakeOnMagicPacket": "Пробуждение по Magic Packet",
}


@dataclass(frozen=True, slots=True)
class AdvancedProperty:
    keyword: str
    display: str
    value: str
    display_value: str
    valid: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class NetworkAdapter:
    name: str
    description: str
    status: str
    link_speed: str
    wired: bool
    pnp_id: str
    driver_version: str
    driver_date: str
    driver_provider: str
    ndis_version: str
    properties: tuple[AdvancedProperty, ...] = ()

    @property
    def is_up(self) -> bool:
        return self.status.lower() == "up"

    def prop(self, keyword: str) -> AdvancedProperty | None:
        for p in self.properties:
            if p.keyword.lower() == keyword.lower():
                return p
        return None

    def eee_properties(self) -> list[AdvancedProperty]:
        """EEE switches this driver has and can turn off."""
        return [
            p for kw in EEE_KEYWORDS
            if (p := self.prop(kw)) is not None and "0" in p.valid
        ]


def _parse(row: dict) -> NetworkAdapter:
    raw_props = row.get("Properties") or []
    if isinstance(raw_props, dict):
        raw_props = [raw_props]
    props = tuple(
        AdvancedProperty(
            keyword=str(p.get("Keyword") or ""),
            display=str(p.get("Display") or ""),
            value=str(p.get("Value") or ""),
            display_value=str(p.get("DisplayValue") or ""),
            valid=tuple(v for v in str(p.get("Valid") or "").split(",") if v),
        )
        for p in raw_props
        if isinstance(p, dict)
    )
    return NetworkAdapter(
        name=str(row.get("Name") or ""),
        description=str(row.get("Description") or ""),
        status=str(row.get("Status") or ""),
        link_speed=str(row.get("LinkSpeed") or ""),
        wired=bool(row.get("Wired")),
        pnp_id=str(row.get("PnpId") or ""),
        driver_version=str(row.get("DriverVersion") or ""),
        driver_date=str(row.get("DriverDate") or ""),
        driver_provider=str(row.get("DriverProvider") or ""),
        ndis_version=str(row.get("NdisVersion") or ""),
        properties=props,
    )


def adapters(powershell: PowerShellRunner | None = None) -> list[NetworkAdapter]:
    """Physical adapters with their properties. Raises :class:`PgmError`."""
    runner = powershell or PowerShellRunner(CommandRunner())
    rows = runner.run_json(_QUERY, depth=4, timeout_s=90, operation="network adapters")
    return [_parse(r) for r in rows]


def set_property(
    adapter: str, keyword: str, value: str,
    powershell: PowerShellRunner | None = None,
) -> None:
    """Store a new value for one property. Raises :class:`PgmError`."""
    runner = powershell or PowerShellRunner(CommandRunner())
    runner.run(
        _SET,
        parameters={"Adapter": adapter, "Keyword": keyword, "Value": value},
        timeout_s=60,
        requires_admin=True,
        operation=f"set {keyword} on {adapter}",
    )
    audit_event(
        "network", "set_adapter_property",
        target=f"{adapter}\\{keyword}", new_state={"value": value},
    )


__all__ = [
    "DIAGNOSTIC_KEYWORDS",
    "EEE_KEYWORDS",
    "AdvancedProperty",
    "NetworkAdapter",
    "adapters",
    "set_property",
]
