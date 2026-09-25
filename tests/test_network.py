"""Tests for the network subsystem: measurement, routing, findings, score.

No test sends a real echo or starts PowerShell. The routing fixtures are
taken from a real machine whose internet traffic runs through a VPN tunnel
— the case a naive implementation gets wrong by reporting the tunnel's
"100 Gbps" as the network card.
"""

from __future__ import annotations

import pytest

from pudge_gaming_manager.core.diagnostics.network import detect_network_issues
from pudge_gaming_manager.core.scanner.network_scan import NetworkScanner
from pudge_gaming_manager.core.scoring.score import score_network
from pudge_gaming_manager.network.adapter.routing import parse_routing
from pudge_gaming_manager.network.latency import icmp
from pudge_gaming_manager.network.models import (
    AdapterInfo,
    LinkKind,
    NetworkSnapshot,
    PingStats,
    link_kind,
)
from pudge_gaming_manager.utilities.exceptions import PgmError

ETHERNET_ROW = {
    "IfIndex": 7, "Name": "Ethernet",
    "Description": "Realtek PCIe GbE Family Controller", "Medium": 14,
    "SpeedBps": 1_000_000_000, "FullDuplex": True, "Virtual": False,
    "Hardware": True,
}
VPN_ROW = {
    "IfIndex": 12, "Name": "DurevVPN", "Description": "sing-tun Tunnel",
    "Medium": 0, "SpeedBps": 100_000_000_000, "FullDuplex": True,
    "Virtual": True, "Hardware": False,
}


def _adapter(**overrides) -> AdapterInfo:
    base = dict(
        if_index=7, name="Ethernet", description="NIC", kind=LinkKind.WIRED,
        link_speed_bps=1_000_000_000, full_duplex=True, is_virtual=False,
    )
    base.update(overrides)
    return AdapterInfo(**base)  # type: ignore[arg-type]


def _ping(label: str, rtts: tuple[float, ...], sent: int = 10, **kw) -> PingStats:
    return PingStats(target="192.168.1.1", label=label, sent=sent, rtts_ms=rtts, **kw)


def _network(**overrides) -> NetworkSnapshot:
    uplink = _adapter()
    base = dict(
        uplink=uplink, gateway="192.168.1.1", path=uplink,
        gateway_ping=_ping("gateway", (0.0,) * 10),
        internet_ping=_ping("internet reference", (20.0,) * 10),
    )
    base.update(overrides)
    return NetworkSnapshot(**base)  # type: ignore[arg-type]


def _ids(network: NetworkSnapshot) -> set[str]:
    return {i.id for i in detect_network_issues(network)}


# -- measurement -------------------------------------------------------------


class _ScriptedEcho:
    def __init__(self, replies: dict[str, list[float | None]]) -> None:
        self.replies = {k: list(v) for k, v in replies.items()}
        self.calls: list[str] = []

    def __call__(self, address: str, _timeout_ms: int) -> float | None:
        self.calls.append(address)
        queue = self.replies.get(address, [])
        return queue.pop(0) if queue else None


def _measure(echo, **kw) -> PingStats:
    return icmp.measure(echo, "1.1.1.1", "internet", sleep=lambda _s: None, **kw)


def test_measure_collects_statistics() -> None:
    stats = _measure(_ScriptedEcho({"1.1.1.1": [10, 12, None, 10, 14]}), count=5)
    assert stats.sent == 5
    assert stats.received == 4
    assert stats.loss_percent == pytest.approx(20.0)
    assert stats.avg_ms == pytest.approx(11.5)
    assert stats.max_ms == 14
    # consecutive replies 10,12,10,14 -> steps 2,2,4
    assert stats.jitter_ms == pytest.approx(8 / 3)


def test_measure_gives_up_on_a_silent_target() -> None:
    echo = _ScriptedEcho({})
    stats = _measure(echo, count=10, give_up_after=3)
    assert stats.sent == 3
    assert not stats.reachable
    assert "нет ответа" in stats.summary()


def test_late_loss_does_not_trigger_the_early_exit() -> None:
    stats = _measure(_ScriptedEcho({"1.1.1.1": [5, None, None, None, 5]}), count=5)
    assert stats.sent == 5


def test_canary_reply_marks_the_path_as_fabricating() -> None:
    """A reply from TEST-NET-1 cannot be real; the VPN answered it."""
    echo = _ScriptedEcho({icmp.UNROUTABLE_CANARY: [0.0], "1.1.1.1": [0.0] * 10})
    stats = _measure(echo, canary=True)
    assert not stats.trustworthy
    assert "не маршрутизируется" in stats.summary()
    assert echo.calls == [icmp.UNROUTABLE_CANARY]  # target never measured


def test_silent_canary_lets_the_measurement_proceed() -> None:
    echo = _ScriptedEcho({"1.1.1.1": [20.0] * 10})
    stats = _measure(echo, canary=True)
    assert stats.trustworthy
    assert stats.received == 10


def test_sub_millisecond_is_not_shown_as_zero() -> None:
    assert _ping("gateway", (0.0, 0.0)).summary().startswith("<1 мс")


def test_ping_target_must_be_an_ipv4_literal() -> None:
    with pytest.raises(ValueError):
        icmp._ipv4("192.168.1.1; calc.exe")


# -- adapters and routing ------------------------------------------------------


@pytest.mark.parametrize(
    ("medium", "kind"),
    [(14, LinkKind.WIRED), (9, LinkKind.WIRELESS), (1, LinkKind.WIRELESS),
     (0, LinkKind.OTHER)],
)
def test_link_kind_is_read_from_the_numeric_medium(medium, kind) -> None:
    assert link_kind(medium) is kind


def test_vpn_path_is_not_mistaken_for_the_network_card() -> None:
    routing = parse_routing({
        "PathIfIndex": 12,
        "Routes": [
            {"IfIndex": 12, "NextHop": "172.17.0.2", "Metric": 0},
            {"IfIndex": 7, "NextHop": "192.168.100.1", "Metric": 25},
        ],
        "Adapters": [VPN_ROW, ETHERNET_ROW],
    })
    assert routing.uplink is not None and routing.uplink.name == "Ethernet"
    assert routing.uplink.link_speed_mbps == 1000
    assert routing.gateway == "192.168.100.1"
    assert routing.path is not None and routing.path.name == "DurevVPN"
    snapshot = NetworkSnapshot(uplink=routing.uplink, path=routing.path)
    assert snapshot.tunnelled


def test_direct_connection_is_not_tunnelled() -> None:
    routing = parse_routing({
        "PathIfIndex": 7,
        "Routes": [{"IfIndex": 7, "NextHop": "192.168.100.1", "Metric": 25}],
        "Adapters": [ETHERNET_ROW],
    })
    assert not NetworkSnapshot(uplink=routing.uplink, path=routing.path).tunnelled


def test_best_metric_wins_among_physical_adapters() -> None:
    wifi = {**ETHERNET_ROW, "IfIndex": 9, "Name": "Wi-Fi", "Medium": 9}
    routing = parse_routing({
        "Routes": [
            {"IfIndex": 9, "NextHop": "10.0.0.1", "Metric": 50},
            {"IfIndex": 7, "NextHop": "192.168.100.1", "Metric": 25},
        ],
        "Adapters": [wifi, ETHERNET_ROW],
    })
    assert routing.uplink is not None and routing.uplink.name == "Ethernet"


@pytest.mark.parametrize("hop", ["0.0.0.0", "", None, "fe80::1", "not-an-ip"])
def test_unusable_next_hops_are_ignored(hop) -> None:
    """An unusable hop is not a gateway; the card is still the uplink."""
    routing = parse_routing({
        "Routes": [{"IfIndex": 7, "NextHop": hop, "Metric": 0}],
        "Adapters": [ETHERNET_ROW],
    })
    assert routing.gateway is None
    assert routing.uplink is not None and routing.uplink.name == "Ethernet"


def test_adapter_without_hardware_interface_is_virtual() -> None:
    """A Hyper-V switch may not set Virtual, but it has no hardware interface."""
    switch = {**ETHERNET_ROW, "Virtual": False, "Hardware": False}
    routing = parse_routing({
        "Routes": [{"IfIndex": 7, "NextHop": "192.168.1.1", "Metric": 0}],
        "Adapters": [switch],
    })
    assert routing.uplink is None


def test_empty_answer_parses_to_nothing() -> None:
    routing = parse_routing({})
    assert (routing.uplink, routing.gateway, routing.path) == (None, None, None)


# -- scanner -----------------------------------------------------------------


class _FakePowerShell:
    def __init__(self, rows=None, error: PgmError | None = None) -> None:
        self.rows, self.error = rows, error

    def run_json(self, *_a, **_kw):
        if self.error:
            raise self.error
        return self.rows


def _routing_rows(**overrides) -> list[dict]:
    data = {
        "PathIfIndex": 7,
        "Routes": [{"IfIndex": 7, "NextHop": "192.168.100.1", "Metric": 25}],
        "Adapters": [ETHERNET_ROW],
    }
    data.update(overrides)
    return [data]


def test_scanner_reports_an_unreadable_configuration() -> None:
    scanner = NetworkScanner(
        _FakePowerShell(error=PgmError("PowerShell failed", "timed out")),
        ping=lambda *a, **k: pytest.fail("must not ping"),
        use_wmi=False,
    )
    network = scanner.scan()
    assert "timed out" in network.unavailable_reason
    assert detect_network_issues(network) == []


def test_scanner_measures_gateway_and_internet() -> None:
    calls: list[tuple[str, bool]] = []

    def fake_ping(target, label, *, canary):
        calls.append((target, canary))
        return PingStats(target, label, 10, (1.0,) * 10)

    network = NetworkScanner(_FakePowerShell(_routing_rows()), ping=fake_ping, use_wmi=False).scan()
    assert sorted(calls) == [("1.1.1.1", True), ("192.168.100.1", False)]
    assert network.gateway_ping is not None and network.internet_ping is not None


def test_scanner_turns_a_ping_failure_into_a_warning() -> None:
    def failing_ping(target, label, *, canary):
        raise OSError(5, "IcmpCreateFile failed")

    network = NetworkScanner(_FakePowerShell(_routing_rows()), ping=failing_ping, use_wmi=False).scan()
    assert network.gateway_ping is None and network.internet_ping is None
    assert len(network.warnings) == 2


def test_scanner_skips_pings_when_disconnected() -> None:
    scanner = NetworkScanner(
        _FakePowerShell(_routing_rows(PathIfIndex=None, Routes=[], Adapters=[])),
        ping=lambda *a, **k: pytest.fail("must not ping"),
        use_wmi=False,
    )
    assert "network.disconnected" in _ids(scanner.scan())


# -- findings ------------------------------------------------------------------


def test_healthy_wired_connection_has_no_findings() -> None:
    assert _ids(_network()) == set()


def test_wifi_is_reported() -> None:
    assert "network.wireless" in _ids(_network(uplink=_adapter(kind=LinkKind.WIRELESS)))


def test_slow_and_half_duplex_links_are_reported() -> None:
    ids = _ids(_network(uplink=_adapter(link_speed_bps=100_000_000, full_duplex=False)))
    assert {"network.link_speed", "network.half_duplex"} <= ids


def test_unknown_link_speed_is_not_a_finding() -> None:
    assert _ids(_network(uplink=_adapter(link_speed_bps=None, full_duplex=None))) == set()


def test_tunnel_is_reported() -> None:
    tunnel = _adapter(if_index=12, name="VPN", is_virtual=True, kind=LinkKind.OTHER)
    assert "network.tunnelled" in _ids(_network(path=tunnel))


def test_heavy_gateway_loss_is_critical() -> None:
    issues = detect_network_issues(
        _network(gateway_ping=_ping("gateway", (0.0,) * 5, sent=10))
    )
    loss = next(i for i in issues if i.id == "network.gateway_loss")
    assert loss.severity.value == "CRITICAL"


def test_gateway_that_filters_ping_is_not_a_broken_lan() -> None:
    assert _ids(_network(gateway_ping=_ping("gateway", (), sent=3))) == set()


def test_unreachable_internet_says_it_cannot_tell_why() -> None:
    issues = detect_network_issues(
        _network(internet_ping=_ping("internet reference", (), sent=3))
    )
    unreachable = next(i for i in issues if i.id == "network.internet_unreachable")
    assert "не может отличить" in unreachable.detail


def test_fabricated_internet_replies_produce_no_latency_findings() -> None:
    fake = _ping("internet reference", (500.0,) * 10, unreliable_reason="tunnel")
    assert _ids(_network(internet_ping=fake)) == set()


def test_jitter_and_latency_are_reported() -> None:
    wobbly = _ping("internet reference", (120.0, 160.0) * 5)
    assert {"network.internet_jitter", "network.internet_latency"} <= _ids(
        _network(internet_ping=wobbly)
    )


def test_findings_are_labelled_heuristic() -> None:
    issue = detect_network_issues(_network(uplink=_adapter(kind=LinkKind.WIRELESS)))[0]
    assert issue.threshold_kind is not None
    assert issue.threshold_kind.value == "HEURISTIC"


# -- score ---------------------------------------------------------------------


def test_unscanned_network_is_excluded_not_zero() -> None:
    assert score_network(None, 15).score is None
    assert score_network(NetworkSnapshot(unavailable_reason="x"), 15).score is None


def test_disconnected_scores_zero() -> None:
    assert score_network(NetworkSnapshot(), 15).score == 0


def test_healthy_connection_scores_full_marks() -> None:
    assert score_network(_network(), 15).score == pytest.approx(100)


def test_wifi_costs_points() -> None:
    wifi = score_network(_network(uplink=_adapter(kind=LinkKind.WIRELESS)), 15)
    assert wifi.score == pytest.approx(80)
    assert "Wi-Fi -20" in wifi.explanation


def test_fabricated_replies_neither_help_nor_hurt() -> None:
    fake = _ping("internet reference", (900.0,) * 10, unreliable_reason="tunnel")
    component = score_network(_network(internet_ping=fake), 15)
    assert component.score == pytest.approx(100)
    assert "не измеряется" in component.explanation


def test_loss_is_penalised() -> None:
    lossy = _ping("internet reference", (20.0,) * 8)  # 20% loss
    assert score_network(_network(internet_ping=lossy), 15).score == pytest.approx(40)


# -- LAN without a default gateway (finding 10) -----------------------------


def test_lan_without_a_gateway_is_connected_not_disconnected() -> None:
    routing = parse_routing({"PathIfIndex": None, "Routes": [], "Adapters": [ETHERNET_ROW]})
    assert routing.uplink is not None and routing.uplink.name == "Ethernet"
    assert routing.gateway is None

    network = NetworkSnapshot(uplink=routing.uplink, gateway=None, path=None)
    ids = _ids(network)
    assert "network.disconnected" not in ids
    assert "network.no_default_route" in ids
    assert score_network(network, 15).score == pytest.approx(100)


def test_no_physical_adapter_is_still_disconnected() -> None:
    routing = parse_routing({"Routes": [], "Adapters": [VPN_ROW]})
    assert routing.uplink is None


def test_wired_is_preferred_when_there_is_no_route() -> None:
    wifi = {**ETHERNET_ROW, "IfIndex": 3, "Name": "Wi-Fi", "Medium": 9}
    routing = parse_routing({"Routes": [], "Adapters": [wifi, ETHERNET_ROW]})
    assert routing.uplink is not None and routing.uplink.name == "Ethernet"


def test_scanner_does_not_ping_the_internet_without_a_route() -> None:
    calls: list[str] = []

    def fake_ping(target, label, *, canary):
        calls.append(target)
        return PingStats(target, label, 10, (1.0,) * 10)

    rows = [{"PathIfIndex": None, "Routes": [], "Adapters": [ETHERNET_ROW]}]
    network = NetworkScanner(_FakePowerShell(rows), ping=fake_ping, use_wmi=False).scan()
    assert calls == []
    assert network.connected and network.internet_ping is None


# -- router traffic captured by a tunnel (finding 13) -------------------------


def _rows_with_via(via: int) -> list[dict]:
    return [{
        "PathIfIndex": 12,
        "Routes": [{"IfIndex": 7, "NextHop": "192.168.100.1", "Metric": 25,
                    "ViaIfIndex": via}],
        "Adapters": [VPN_ROW, ETHERNET_ROW],
    }]


def test_router_ping_through_a_tunnel_is_not_trusted() -> None:
    def fake_ping(target, label, *, canary):
        return PingStats(target, label, 10, (0.0,) * 10)

    network = NetworkScanner(_FakePowerShell(_rows_with_via(12)), ping=fake_ping, use_wmi=False).scan()
    assert network.gateway_ping is not None
    assert not network.gateway_ping.trustworthy
    assert "другой адаптер" in network.gateway_ping.summary()
    # An untrusted measurement produces no latency finding either way.
    assert "network.gateway_loss" not in _ids(network)


def test_router_ping_over_the_network_card_is_trusted() -> None:
    def fake_ping(target, label, *, canary):
        return PingStats(target, label, 10, (0.0,) * 10)

    network = NetworkScanner(_FakePowerShell(_rows_with_via(7)), ping=fake_ping, use_wmi=False).scan()
    assert network.gateway_ping is not None and network.gateway_ping.trustworthy


def test_missing_via_is_not_treated_as_rerouted() -> None:
    routing = parse_routing({
        "Routes": [{"IfIndex": 7, "NextHop": "192.168.100.1", "Metric": 25}],
        "Adapters": [ETHERNET_ROW],
    })
    assert routing.gateway_rerouted is False
