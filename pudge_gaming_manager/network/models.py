"""What the network scan found.

Two different questions are kept apart on purpose:

* **The uplink** — the physical adapter that connects this PC to the club
  network: cable or Wi-Fi, negotiated speed, duplex, gateway.
* **The path** — the adapter Windows actually sends internet traffic
  through. Usually the same adapter; with a VPN or tunnel it is not, and a
  tunnel reporting "100 Gbps" must never be mistaken for the network card.

Anything that could not be read is ``None`` with a reason, never a zero.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class LinkKind(str, Enum):
    WIRED = "WIRED"
    WIRELESS = "WIRELESS"
    OTHER = "OTHER"


#: NDIS_PHYSICAL_MEDIUM values (ntddndis.h). Numeric, so the result does not
#: depend on the Windows display language.
_WIRED_MEDIA = {14}  # NdisPhysicalMedium802_3
_WIRELESS_MEDIA = {1, 8, 9}  # WirelessLan, WirelessWan, Native802_11


def link_kind(ndis_physical_medium: int) -> LinkKind:
    if ndis_physical_medium in _WIRED_MEDIA:
        return LinkKind.WIRED
    if ndis_physical_medium in _WIRELESS_MEDIA:
        return LinkKind.WIRELESS
    return LinkKind.OTHER


@dataclass(frozen=True, slots=True)
class AdapterInfo:
    """One network adapter, as Windows reports it."""

    if_index: int
    name: str
    description: str
    kind: LinkKind
    link_speed_bps: int | None
    full_duplex: bool | None
    is_virtual: bool
    """A VPN, tunnel or virtual switch rather than a network card."""

    @property
    def link_speed_mbps(self) -> int | None:
        if not self.link_speed_bps:
            return None
        return self.link_speed_bps // 1_000_000

    @property
    def kind_label(self) -> str:
        return {
            LinkKind.WIRED: "Ethernet",
            LinkKind.WIRELESS: "Wi-Fi",
            LinkKind.OTHER: "Other",
        }[self.kind]


@dataclass(frozen=True, slots=True)
class PingStats:
    """Echo replies from one target.

    Round-trip times come from ``IcmpSendEcho``, which reports whole
    milliseconds, so a LAN hop typically reads 0 ms — meaning "under 1 ms",
    not "instant".
    """

    target: str
    label: str
    sent: int
    rtts_ms: tuple[float, ...]
    """One entry per reply received."""

    unreliable_reason: str = ""
    """Set when the replies cannot be trusted as a measurement, e.g. because
    something on this PC answers echoes itself. The numbers are then kept
    for the log but must not be presented or scored."""

    @property
    def trustworthy(self) -> bool:
        return not self.unreliable_reason

    @property
    def received(self) -> int:
        return len(self.rtts_ms)

    @property
    def reachable(self) -> bool:
        return self.received > 0

    @property
    def loss_percent(self) -> float:
        return 100.0 * (self.sent - self.received) / self.sent if self.sent else 0.0

    @property
    def avg_ms(self) -> float | None:
        return sum(self.rtts_ms) / self.received if self.rtts_ms else None

    @property
    def max_ms(self) -> float | None:
        return max(self.rtts_ms) if self.rtts_ms else None

    @property
    def jitter_ms(self) -> float | None:
        """Mean difference between consecutive replies (RFC 3550 in spirit)."""
        if len(self.rtts_ms) < 2:
            return None
        steps = [abs(b - a) for a, b in zip(self.rtts_ms, self.rtts_ms[1:])]
        return sum(steps) / len(steps)

    def summary(self) -> str:
        if not self.trustworthy:
            return f"not measurable: {self.unreliable_reason}"
        if not self.reachable:
            return f"no reply from {self.target} ({self.sent} sent)"
        avg = self.avg_ms or 0.0
        avg_text = "<1 ms" if avg < 1 else f"{avg:.0f} ms"
        jitter = self.jitter_ms
        jitter_text = f", jitter {jitter:.1f} ms" if jitter is not None else ""
        return f"{avg_text} avg, {self.loss_percent:.0f}% loss{jitter_text}"


@dataclass(frozen=True, slots=True)
class NetworkSnapshot:
    """Everything the network scan established."""

    uplink: AdapterInfo | None = None
    gateway: str | None = None
    path: AdapterInfo | None = None
    """The adapter internet traffic leaves through, as Windows routes it."""

    gateway_ping: PingStats | None = None
    internet_ping: PingStats | None = None
    warnings: tuple[str, ...] = ()
    unavailable_reason: str = ""
    """Set when nothing about the network could be read."""

    @property
    def connected(self) -> bool:
        return self.path is not None or self.uplink is not None

    @property
    def tunnelled(self) -> bool:
        """Internet traffic leaves through a virtual adapter, not the uplink."""
        return (
            self.path is not None
            and self.path.is_virtual
            and (self.uplink is None or self.path.if_index != self.uplink.if_index)
        )
