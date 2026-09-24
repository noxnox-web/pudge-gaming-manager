"""Round-trip measurement with the Win32 ICMP API.

``IcmpSendEcho`` (iphlpapi) is documented, needs no administrator rights and
returns structured replies. The alternatives are worse on a club PC: raw
ICMP sockets require elevation, and parsing ``ping.exe`` means parsing text
that Windows translates into the display language.

The transport (:class:`IcmpEcho`) and the statistics (:func:`measure`) are
separate, so everything except the one system call is testable offline.
"""

from __future__ import annotations

import ctypes
import ipaddress
import socket
import time
from collections.abc import Callable
from ctypes import wintypes

from ...utilities.logging_setup import get_logger
from ..models import PingStats

_log = get_logger(__name__)

#: ``echo(address, timeout_ms)`` -> round-trip time in ms, or ``None``.
EchoFunction = Callable[[str, int], float | None]

_IP_SUCCESS = 0

#: TEST-NET-1 (RFC 5737): reserved for documentation, routed nowhere. A reply
#: from it cannot have crossed a network, so it proves that something on
#: this PC — typically VPN or tunnel software — answers echoes itself.
UNROUTABLE_CANARY = "192.0.2.1"

_FABRICATED_REPLIES = (
    "что-то на этом ПК само отвечает на ping (пришёл ответ от "
    f"{UNROUTABLE_CANARY}, который не маршрутизируется). Так часто делают "
    "VPN и туннели, поэтому задержки на этом пути ненастоящие."
)

_PAYLOAD = b"PudgeGamingManager-latency-probe"


class _IpOptionInformation(ctypes.Structure):
    _fields_ = [
        ("Ttl", ctypes.c_ubyte),
        ("Tos", ctypes.c_ubyte),
        ("Flags", ctypes.c_ubyte),
        ("OptionsSize", ctypes.c_ubyte),
        ("OptionsData", ctypes.POINTER(ctypes.c_ubyte)),
    ]


class _IcmpEchoReply(ctypes.Structure):
    _fields_ = [
        ("Address", ctypes.c_ulong),
        ("Status", ctypes.c_ulong),
        ("RoundTripTime", ctypes.c_ulong),
        ("DataSize", ctypes.c_ushort),
        ("Reserved", ctypes.c_ushort),
        ("Data", ctypes.c_void_p),
        ("Options", _IpOptionInformation),
    ]


def _ipv4(address: str) -> ipaddress.IPv4Address:
    """Accept only a dotted IPv4 literal; the gateway string comes from outside."""
    return ipaddress.IPv4Address(address)


class IcmpEcho:
    """One ICMP handle. Not shared between threads; create one per thread."""

    def __init__(self) -> None:
        self._api = ctypes.WinDLL("iphlpapi", use_last_error=True)
        self._api.IcmpCreateFile.restype = wintypes.HANDLE
        self._api.IcmpSendEcho.argtypes = [
            wintypes.HANDLE, ctypes.c_ulong, ctypes.c_void_p, wintypes.WORD,
            ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
        ]
        self._api.IcmpSendEcho.restype = wintypes.DWORD
        self._api.IcmpCloseHandle.argtypes = [wintypes.HANDLE]

        handle = self._api.IcmpCreateFile()
        if not handle or handle == wintypes.HANDLE(-1).value:
            raise OSError(ctypes.get_last_error(), "IcmpCreateFile failed")
        self._handle = handle

    def __call__(self, address: str, timeout_ms: int) -> float | None:
        target = _ipv4(address)
        # IPAddr is the address in network byte order, stored as a ULONG.
        packed = int.from_bytes(socket.inet_aton(str(target)), "little")
        request = ctypes.create_string_buffer(_PAYLOAD, len(_PAYLOAD))
        # One reply, the echoed payload, and room for an ICMP error message.
        reply = ctypes.create_string_buffer(
            ctypes.sizeof(_IcmpEchoReply) + len(_PAYLOAD) + 8 + 32
        )
        count = self._api.IcmpSendEcho(
            self._handle, packed, request, len(_PAYLOAD), None,
            reply, ctypes.sizeof(reply), timeout_ms,
        )
        if count == 0:
            return None
        parsed = _IcmpEchoReply.from_buffer(reply)
        if parsed.Status != _IP_SUCCESS or parsed.Address != packed:
            return None
        return float(parsed.RoundTripTime)

    def close(self) -> None:
        if self._handle:
            self._api.IcmpCloseHandle(self._handle)
            self._handle = None

    def __enter__(self) -> IcmpEcho:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


def measure(
    echo: EchoFunction,
    target: str,
    label: str,
    *,
    count: int = 10,
    timeout_ms: int = 1000,
    interval_s: float = 0.05,
    give_up_after: int = 3,
    canary: bool = False,
    sleep: Callable[[float], None] = time.sleep,
) -> PingStats:
    """Send ``count`` echoes and summarise the replies.

    With ``canary``, first check that the path does not fabricate replies
    (see :data:`UNROUTABLE_CANARY`). Use it for any target beyond the local
    network; a LAN gateway is reached directly and needs no such check.

    Stops early when the first ``give_up_after`` echoes all go unanswered:
    ten one-second timeouts would add ten seconds to every scan of a PC
    whose network filters ICMP, and would not tell us anything more.
    """
    if canary and echo(UNROUTABLE_CANARY, min(timeout_ms, 500)) is not None:
        stats = PingStats(target, label, sent=0, rtts_ms=(),
                          unreliable_reason=_FABRICATED_REPLIES)
        _log.warning("ping %s (%s): %s", target, label, stats.summary())
        return stats

    rtts: list[float] = []
    sent = 0
    for attempt in range(count):
        sent += 1
        rtt = echo(target, timeout_ms)
        if rtt is not None:
            rtts.append(rtt)
        elif not rtts and sent >= give_up_after:
            break
        if attempt < count - 1:
            # Spaced out so routers that rate-limit ICMP do not read as loss.
            sleep(interval_s)

    stats = PingStats(target=target, label=label, sent=sent, rtts_ms=tuple(rtts))
    _log.info("ping %s (%s): %s", target, label, stats.summary())
    return stats


def ping(target: str, label: str, **options: object) -> PingStats:
    """Measure ``target`` with a real ICMP handle."""
    with IcmpEcho() as echo:
        return measure(echo, target, label, **options)  # type: ignore[arg-type]
