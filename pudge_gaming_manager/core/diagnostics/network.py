"""Findings from the network scan.

Every limit here is a PGM heuristic, labelled as such (rule #8): there is no
vendor document that says how much LAN jitter is too much for a club.

Absence of data is still not evidence (rule #64). A gateway that does not
answer ping, with the internet answering fine, is a router that filters
ICMP — not a broken LAN — and produces no finding.
"""

from __future__ import annotations

from ...hardware.models import ThresholdKind
from ...network.models import LinkKind, NetworkSnapshot, PingStats
from .model import Issue, Severity

#: A gigabit port negotiating less almost always means a damaged cable or
#: one wired with only two pairs.
EXPECTED_WIRED_MBPS = 1000

#: The first hop on a wired LAN answers in well under a millisecond.
GATEWAY_LATENCY_WARNING_MS = {LinkKind.WIRED: 5.0, LinkKind.WIRELESS: 20.0}

#: Any loss to the gateway is abnormal on a wired LAN.
GATEWAY_LOSS_WARNING_PERCENT = 1.0
GATEWAY_LOSS_CRITICAL_PERCENT = 20.0

INTERNET_LOSS_WARNING_PERCENT = 5.0
INTERNET_JITTER_WARNING_MS = 15.0
INTERNET_LATENCY_WARNING_MS = 100.0

_HEURISTIC = ThresholdKind.HEURISTIC


def detect_network_issues(network: NetworkSnapshot) -> list[Issue]:
    if network.unavailable_reason:
        return []
    if not network.connected:
        return [
            Issue(
                id="network.disconnected",
                title="No network connection",
                detail="Windows has no active network adapter with a route out.",
                severity=Severity.CRITICAL,
                subsystem="network",
                fixable=False,
                fix_hint="Check the cable, the switch port and the adapter driver.",
            )
        ]

    issues: list[Issue] = []
    if network.path is None and network.gateway is None:
        issues.append(
            Issue(
                id="network.no_default_route",
                title="No route to the internet",
                detail=(
                    "The network card is connected, but Windows has no default "
                    "gateway, so nothing beyond the local network is reachable. "
                    "That is expected on a LAN-only event; otherwise the router "
                    "or DHCP did not hand out a gateway."
                ),
                severity=Severity.WARNING,
                subsystem="network",
                fixable=False,
                fix_hint="Check the router and that this PC gets its address by DHCP.",
            )
        )
    issues.extend(_link_issues(network))
    issues.extend(_path_issues(network))
    issues.extend(_gateway_issues(network))
    issues.extend(_internet_issues(network.internet_ping))
    return issues


def _link_issues(network: NetworkSnapshot) -> list[Issue]:
    uplink = network.uplink
    if uplink is None:
        return []
    found: list[Issue] = []

    if uplink.kind is LinkKind.WIRELESS:
        found.append(
            Issue(
                id="network.wireless",
                title="This PC is on Wi-Fi",
                detail=(
                    f"{uplink.name} ({uplink.description}) is a wireless link. "
                    "Wi-Fi shares airtime with every nearby device, which shows "
                    "up in games as latency spikes a cable does not have."
                ),
                severity=Severity.WARNING,
                subsystem="network",
                fixable=False,
                fix_hint="Connect the PC with an Ethernet cable.",
                threshold_kind=_HEURISTIC,
            )
        )

    speed = uplink.link_speed_mbps
    if uplink.kind is LinkKind.WIRED and speed is not None and speed < EXPECTED_WIRED_MBPS:
        found.append(
            Issue(
                id="network.link_speed",
                title=f"Ethernet link is running at {speed} Mbps",
                detail=(
                    f"{uplink.name} negotiated {speed} Mbps. On a gigabit "
                    "network this usually means a damaged cable, a cable with "
                    "only two pairs wired, or a 100 Mbps switch port."
                ),
                severity=Severity.WARNING,
                subsystem="network",
                fixable=False,
                fix_hint="Replace the patch cable or try another switch port.",
                threshold_kind=_HEURISTIC,
            )
        )

    if uplink.kind is LinkKind.WIRED and uplink.full_duplex is False:
        found.append(
            Issue(
                id="network.half_duplex",
                title="Ethernet link is half duplex",
                detail=(
                    f"{uplink.name} can send or receive, but not both at once. "
                    "That is a negotiation fault, and it causes collisions and "
                    "retransmits under load."
                ),
                severity=Severity.WARNING,
                subsystem="network",
                fixable=False,
                fix_hint="Set the adapter and switch port to auto-negotiate.",
            )
        )
    return found


def _path_issues(network: NetworkSnapshot) -> list[Issue]:
    if not network.tunnelled or network.path is None:
        return []
    path = network.path
    return [
        Issue(
            id="network.tunnelled",
            title=f"Internet traffic goes through '{path.name}'",
            detail=(
                f"Windows routes internet traffic through {path.name} "
                f"({path.description}), a virtual adapter, rather than the "
                "network card. That is typically a VPN or tunnel; game traffic "
                "then takes the tunnel's route and latency. Ignore this if it "
                "is intended."
            ),
            severity=Severity.WARNING,
            subsystem="network",
            fixable=False,
            fix_hint="Disconnect the VPN or exclude games from it.",
            threshold_kind=_HEURISTIC,
        )
    ]


def _gateway_issues(network: NetworkSnapshot) -> list[Issue]:
    stats = network.gateway_ping
    uplink = network.uplink
    if stats is None or not stats.trustworthy or not stats.reachable:
        return []
    found: list[Issue] = []

    loss = stats.loss_percent
    if loss >= GATEWAY_LOSS_WARNING_PERCENT:
        found.append(
            Issue(
                id="network.gateway_loss",
                title=f"{loss:.0f}% packet loss to the router",
                detail=(
                    f"{stats.received} of {stats.sent} echoes to the gateway "
                    f"{stats.target} came back. Loss on the first hop points at "
                    "the cable, the switch or the router, not the internet."
                ),
                severity=(
                    Severity.CRITICAL
                    if loss >= GATEWAY_LOSS_CRITICAL_PERCENT
                    else Severity.WARNING
                ),
                subsystem="network",
                fixable=False,
                fix_hint="Check the cable and switch port for this PC.",
                threshold_kind=_HEURISTIC,
            )
        )

    limit = GATEWAY_LATENCY_WARNING_MS.get(uplink.kind if uplink else LinkKind.OTHER)
    average = stats.avg_ms
    if limit is not None and average is not None and average > limit:
        found.append(
            Issue(
                id="network.gateway_latency",
                title=f"Router responds in {average:.0f} ms",
                detail=(
                    f"The gateway {stats.target} averages {average:.0f} ms "
                    f"(max {stats.max_ms:.0f} ms). A local hop should take a "
                    f"few milliseconds at most (heuristic limit {limit:.0f} ms)."
                ),
                severity=Severity.WARNING,
                subsystem="network",
                fixable=False,
                fix_hint="Check for a saturated or overloaded router.",
                threshold_kind=_HEURISTIC,
            )
        )
    return found


def _internet_issues(stats: PingStats | None) -> list[Issue]:
    if stats is None or not stats.trustworthy:
        return []
    if not stats.reachable:
        return [
            Issue(
                id="network.internet_unreachable",
                title="No reply from the internet",
                detail=(
                    f"{stats.label} did not answer {stats.sent} echoes. Either "
                    "the internet is unreachable from this PC, or this network "
                    "blocks ping — this check cannot tell which."
                ),
                severity=Severity.WARNING,
                subsystem="network",
                fixable=False,
                fix_hint="Open a website to see whether the internet works.",
            )
        ]

    found: list[Issue] = []
    if stats.loss_percent >= INTERNET_LOSS_WARNING_PERCENT:
        found.append(
            _internet_issue(
                "network.internet_loss",
                f"{stats.loss_percent:.0f}% packet loss to the internet",
                f"{stats.received} of {stats.sent} echoes to {stats.label} came back.",
            )
        )
    jitter = stats.jitter_ms
    if jitter is not None and jitter > INTERNET_JITTER_WARNING_MS:
        found.append(
            _internet_issue(
                "network.internet_jitter",
                f"Unstable latency: {jitter:.0f} ms jitter",
                f"Round trips to {stats.label} vary by {jitter:.0f} ms on "
                "average between consecutive echoes.",
            )
        )
    average = stats.avg_ms
    if average is not None and average > INTERNET_LATENCY_WARNING_MS:
        found.append(
            _internet_issue(
                "network.internet_latency",
                f"High latency to the internet: {average:.0f} ms",
                f"{stats.label} averages {average:.0f} ms. It is a reference "
                "point, not a game server, but a slow path to it is usually a "
                "slow path everywhere.",
            )
        )
    return found


def _internet_issue(issue_id: str, title: str, detail: str) -> Issue:
    return Issue(
        id=issue_id,
        title=title,
        detail=detail,
        severity=Severity.WARNING,
        subsystem="network",
        fixable=False,
        fix_hint="Check the club's uplink and whether other PCs are affected.",
        threshold_kind=_HEURISTIC,
    )
