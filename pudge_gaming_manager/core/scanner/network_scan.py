"""The network half of a scan.

Read-only and unprivileged, like the hardware scan. It answers three things:
what connects this PC (cable or Wi-Fi, at what speed), where its internet
traffic actually goes (directly, or through a VPN/tunnel), and how the
local network and the path beyond it respond.

The two latency measurements run concurrently; together with the routing
query this takes about as long as the hardware scan it runs beside.
"""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

from ...network.adapter.routing import read_routing
from ...network.latency import icmp
from ...network.models import NetworkSnapshot, PingStats
from ...utilities.command_runner import CommandRunner
from ...utilities.exceptions import PgmError
from ...utilities.logging_setup import get_logger
from ...utilities.powershell_runner import PowerShellRunner

_log = get_logger(__name__)

#: A well-known anycast resolver, used as a stable reference point beyond
#: the club network. It is not a game server; latency to a particular game
#: depends on where that game's servers are.
INTERNET_REFERENCE = "1.1.1.1"
INTERNET_LABEL = "internet reference (1.1.1.1)"

PingFunction = Callable[..., PingStats]


class NetworkScanner:
    """Produces a :class:`NetworkSnapshot`."""

    def __init__(
        self,
        powershell: PowerShellRunner | None = None,
        ping: PingFunction = icmp.ping,
    ) -> None:
        self.powershell = powershell or PowerShellRunner(CommandRunner())
        self._ping = ping

    def scan(self) -> NetworkSnapshot:
        """Never raises; what cannot be read is reported as unavailable."""
        try:
            routing = read_routing(self.powershell, INTERNET_REFERENCE)
        except PgmError as exc:
            _log.warning("network routing query failed: %s", exc.what)
            return NetworkSnapshot(
                unavailable_reason=(
                    f"Network configuration could not be read: {exc.reason or exc.what}"
                )
            )

        if routing.path is None and routing.uplink is None:
            return NetworkSnapshot()

        warnings: list[str] = []
        with ThreadPoolExecutor(max_workers=2) as pool:
            gateway_job = (
                pool.submit(self._measure, routing.gateway, "gateway", False, warnings)
                if routing.gateway
                else None
            )
            # With no route out there is nothing to measure; the finding for
            # the missing route says so, and a ping would only add a second,
            # vaguer "no reply" for the same cause.
            internet_job = (
                pool.submit(
                    self._measure, INTERNET_REFERENCE, INTERNET_LABEL, True, warnings
                )
                if routing.path is not None
                else None
            )
            gateway_ping = gateway_job.result() if gateway_job else None
            internet_ping = internet_job.result() if internet_job else None

        if gateway_ping is not None and routing.gateway_rerouted:
            # The replies came back through a tunnel, not the network card:
            # they time the tunnel (or its fabricated answers), not the LAN.
            gateway_ping = replace(
                gateway_ping,
                unreliable_reason=(
                    "traffic to the router is routed through another adapter "
                    "(typically a VPN), so these round trips do not measure "
                    "the local network"
                ),
            )

        return NetworkSnapshot(
            uplink=routing.uplink,
            gateway=routing.gateway,
            path=routing.path,
            gateway_ping=gateway_ping,
            internet_ping=internet_ping,
            warnings=tuple(warnings),
        )

    def _measure(
        self, target: str, label: str, canary: bool, warnings: list[str]
    ) -> PingStats | None:
        try:
            return self._ping(target, label, canary=canary)
        except (OSError, ValueError) as exc:
            # list.append is atomic, so two workers may report here safely.
            warnings.append(f"Latency to the {label} could not be measured: {exc}")
            _log.warning("ping %s failed: %s", target, exc)
            return None


__all__ = ["INTERNET_REFERENCE", "NetworkScanner"]
