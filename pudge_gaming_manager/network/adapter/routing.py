"""Which adapter connects this PC, and which one its traffic really uses.

One PowerShell call returns the adapters, the IPv4 default routes and the
route Windows picks for a given destination (``Find-NetRoute`` asks the
routing engine itself, so VPN route metrics never have to be second-guessed).
Only numeric and identifier fields are read, so the result is the same on a
Russian or an English Windows.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from typing import Any

from ...utilities.logging_setup import get_logger
from ...utilities.powershell_runner import PowerShellRunner
from ..models import AdapterInfo, LinkKind, link_kind

_log = get_logger(__name__)

_QUERY = r"""
$path = Find-NetRoute -RemoteIPAddress $Target -ErrorAction SilentlyContinue |
    Select-Object -First 1
[pscustomobject]@{
  PathIfIndex = if ($path) { [int]$path.InterfaceIndex } else { $null }
  Routes = @(Get-NetRoute -AddressFamily IPv4 -DestinationPrefix '0.0.0.0/0' `
      -ErrorAction SilentlyContinue | ForEach-Object {
    $hop = [string]$_.NextHop
    $via = $null
    if ($hop -and $hop -ne '0.0.0.0') {
      $toHop = Find-NetRoute -RemoteIPAddress $hop -ErrorAction SilentlyContinue |
          Select-Object -First 1
      if ($toHop) { $via = [int]$toHop.InterfaceIndex }
    }
    [pscustomobject]@{
      IfIndex = [int]$_.ifIndex
      NextHop = $hop
      Metric  = [int]$_.RouteMetric + [int]$_.InterfaceMetric
      ViaIfIndex = $via
    }
  })
  Adapters = @(Get-NetAdapter -ErrorAction SilentlyContinue |
      Where-Object { $_.Status -eq 'Up' } | ForEach-Object {
    [pscustomobject]@{
      IfIndex     = [int]$_.ifIndex
      Name        = [string]$_.Name
      Description = [string]$_.InterfaceDescription
      Medium      = [int]$_.NdisPhysicalMedium
      SpeedBps    = [uint64]$_.ReceiveLinkSpeed
      FullDuplex  = $_.FullDuplex
      Virtual     = [bool]$_.Virtual
      Hardware    = [bool]$_.HardwareInterface
    }
  })
}
"""


@dataclass(frozen=True, slots=True)
class Routing:
    """The parsed answer."""

    uplink: AdapterInfo | None
    gateway: str | None
    path: AdapterInfo | None
    gateway_via_if_index: int | None = None
    """The interface Windows sends traffic *to the gateway* through."""

    @property
    def gateway_rerouted(self) -> bool:
        """Traffic to the router does not leave through the network card.

        A VPN in "strict route" mode captures even LAN traffic, and tunnel
        software commonly answers ping itself — so a round trip to the router
        would measure the tunnel, not the LAN.
        """
        return (
            self.uplink is not None
            and self.gateway_via_if_index is not None
            and self.gateway_via_if_index != self.uplink.if_index
        )


def read_routing(powershell: PowerShellRunner, target: str) -> Routing:
    """Query Windows. Raises ``PgmError`` if PowerShell itself fails."""
    rows = powershell.run_json(
        _QUERY,
        parameters={"Target": str(ipaddress.IPv4Address(target))},
        depth=4,
        timeout_s=30,
        operation="read network routing",
    )
    return parse_routing(rows[0] if rows else {})


def parse_routing(data: dict[str, Any]) -> Routing:
    adapters = {
        a.if_index: a for a in (_adapter(row) for row in _rows(data, "Adapters")) if a
    }

    path = adapters.get(data.get("PathIfIndex"))  # type: ignore[arg-type]

    # The uplink is the physical adapter with the best default route. A VPN
    # adapter can own the best route overall; it is still not the uplink.
    best: tuple[int, AdapterInfo, str, int | None] | None = None
    for route in _rows(data, "Routes"):
        adapter = adapters.get(route.get("IfIndex"))  # type: ignore[arg-type]
        gateway = _gateway(route.get("NextHop"))
        if adapter is None or adapter.is_virtual or gateway is None:
            continue
        metric = int(route.get("Metric") or 0)
        via = route.get("ViaIfIndex")
        if best is None or metric < best[0]:
            best = (metric, adapter, gateway, via if isinstance(via, int) else None)

    if best is None:
        # No default route. A physical adapter that is up still connects the
        # PC to a network — a LAN-only tournament has no gateway by design —
        # so report it as the uplink, without a gateway, rather than claiming
        # the PC is disconnected. Wired is preferred over wireless.
        physical = sorted(
            (a for a in adapters.values() if not a.is_virtual),
            key=lambda a: (a.kind is not LinkKind.WIRED, a.if_index),
        )
        return Routing(uplink=physical[0] if physical else None, gateway=None, path=path)
    return Routing(
        uplink=best[1], gateway=best[2], path=path, gateway_via_if_index=best[3]
    )


def _rows(data: dict[str, Any], key: str) -> list[dict[str, Any]]:
    return [r for r in (data.get(key) or []) if isinstance(r, dict)]


def _adapter(row: dict[str, Any]) -> AdapterInfo | None:
    try:
        if_index = int(row["IfIndex"])
    except (KeyError, TypeError, ValueError):
        return None
    speed = row.get("SpeedBps")
    duplex = row.get("FullDuplex")
    return AdapterInfo(
        if_index=if_index,
        name=str(row.get("Name") or ""),
        description=str(row.get("Description") or ""),
        kind=link_kind(int(row.get("Medium") or 0)),
        link_speed_bps=int(speed) if isinstance(speed, (int, float)) and speed > 0 else None,
        full_duplex=duplex if isinstance(duplex, bool) else None,
        # A virtual switch or tunnel may not set Virtual; the absence of a
        # hardware interface is the more dependable signal.
        is_virtual=bool(row.get("Virtual")) or not bool(row.get("Hardware", True)),
    )


def _gateway(next_hop: object) -> str | None:
    """A usable IPv4 gateway, or ``None`` for on-link and malformed hops."""
    try:
        address = ipaddress.IPv4Address(str(next_hop))
    except ValueError:
        return None
    return None if address.is_unspecified else str(address)
