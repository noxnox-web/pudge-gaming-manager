"""Hardware-accelerated GPU scheduling: supported, and in force?

Why not just read the registry
------------------------------
``GraphicsDrivers\\HwSchMode`` records what was *requested*. Windows only
honours it on a GPU and driver that support hardware scheduling, and a
driver can enable it by default with the value absent. So the value alone
answers neither question an operator actually has: "can this PC do it" and
"is it on right now".

The display kernel answers both. ``D3DKMTQueryAdapterInfo`` with
``KMTQAITYPE_WDDM_2_7_CAPS`` returns ``D3DKMT_WDDM_2_7_CAPS``, whose low bits
are documented as HwSchSupported, HwSchEnabled and HwSchEnabledByDefault.
That is the same source the Settings page uses to decide whether to show the
switch at all.

Read-only. Nothing here can change the scheduling mode; that is the settings
catalogue's job, through the registry, with a backup.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass

from ...utilities.logging_setup import get_logger

_log = get_logger(__name__)

#: ``KMTQAITYPE_WDDM_2_7_CAPS`` — position 70 in ``KMTQUERYADAPTERINFOTYPE``
#: (d3dkmthk.h). Available from Windows 10 2004 / WDDM 2.7; older kernels
#: reject it, which reads as "not supported".
_KMTQAITYPE_WDDM_2_7_CAPS = 70

#: Bits of ``D3DKMT_WDDM_2_7_CAPS.Value``.
_HW_SCH_SUPPORTED = 1 << 0
_HW_SCH_ENABLED = 1 << 1
_HW_SCH_ENABLED_BY_DEFAULT = 1 << 2

#: The kernel reports at most this many adapters in one enumeration.
_MAX_ADAPTERS = 64


class _LUID(ctypes.Structure):
    _fields_ = [("LowPart", wintypes.DWORD), ("HighPart", wintypes.LONG)]


class _ADAPTERINFO(ctypes.Structure):
    _fields_ = [
        ("hAdapter", ctypes.c_uint),
        ("AdapterLuid", _LUID),
        ("NumOfSources", wintypes.ULONG),
        ("bPrecisePresentRegionsPreferred", wintypes.BOOL),
    ]


class _ENUMADAPTERS2(ctypes.Structure):
    _fields_ = [
        ("NumAdapters", wintypes.ULONG),
        ("pAdapters", ctypes.POINTER(_ADAPTERINFO)),
    ]


class _QUERYADAPTERINFO(ctypes.Structure):
    _fields_ = [
        ("hAdapter", ctypes.c_uint),
        ("Type", ctypes.c_int),
        ("pPrivateDriverData", ctypes.c_void_p),
        ("PrivateDriverDataSize", ctypes.c_uint),
    ]


class _CLOSEADAPTER(ctypes.Structure):
    _fields_ = [("hAdapter", ctypes.c_uint)]


@dataclass(frozen=True, slots=True)
class HagsCaps:
    """What the display kernel reports for the adapters driving displays."""

    supported: bool | None = None
    """``None`` when the kernel could not be asked (pre-2004 Windows, or a
    failed call). Unknown is not "unsupported"."""

    enabled: bool | None = None
    enabled_by_default: bool | None = None
    detail: str = ""

    @property
    def known(self) -> bool:
        return self.supported is not None


def decode_caps(values: list[int]) -> HagsCaps:
    """Fold the caps of every display adapter into one answer.

    A PC is "supported" when any adapter driving a display supports
    hardware scheduling — that is the GPU the games run on; a Microsoft
    Basic Render adapter beside it reports zero and must not outvote it.
    """
    if not values:
        return HagsCaps(detail="видеоадаптеры с выходом на монитор не найдены")
    supported = [v for v in values if v & _HW_SCH_SUPPORTED]
    if not supported:
        return HagsCaps(
            supported=False,
            enabled=False,
            enabled_by_default=False,
            detail="видеокарта или драйвер не поддерживают аппаратное планирование",
        )
    return HagsCaps(
        supported=True,
        enabled=any(v & _HW_SCH_ENABLED for v in supported),
        enabled_by_default=any(v & _HW_SCH_ENABLED_BY_DEFAULT for v in supported),
    )


def query() -> HagsCaps:
    """Ask the display kernel. Never raises; unknown is reported as such."""
    try:
        gdi = ctypes.WinDLL("gdi32")
        enum_adapters = gdi.D3DKMTEnumAdapters2
        query_info = gdi.D3DKMTQueryAdapterInfo
        close_adapter = gdi.D3DKMTCloseAdapter
    except (AttributeError, OSError) as exc:
        return HagsCaps(detail=f"ядро графики недоступно: {exc}")

    request = _ENUMADAPTERS2(NumAdapters=_MAX_ADAPTERS)
    adapters = (_ADAPTERINFO * _MAX_ADAPTERS)()
    request.pAdapters = adapters
    status = enum_adapters(ctypes.byref(request)) & 0xFFFFFFFF
    if status != 0:
        return HagsCaps(detail=f"перечисление адаптеров: код 0x{status:08X}")

    values: list[int] = []
    failures = 0
    for adapter in adapters[: min(request.NumAdapters, _MAX_ADAPTERS)]:
        try:
            # Render-only adapters (Basic Render, compute) have no sources
            # and do not run games' presentation.
            if adapter.NumOfSources == 0:
                continue
            caps = ctypes.c_uint(0)
            info = _QUERYADAPTERINFO(
                hAdapter=adapter.hAdapter,
                Type=_KMTQAITYPE_WDDM_2_7_CAPS,
                pPrivateDriverData=ctypes.cast(ctypes.byref(caps), ctypes.c_void_p),
                PrivateDriverDataSize=ctypes.sizeof(caps),
            )
            if query_info(ctypes.byref(info)) & 0xFFFFFFFF == 0:
                values.append(caps.value)
            else:
                failures += 1
        finally:
            close_adapter(ctypes.byref(_CLOSEADAPTER(hAdapter=adapter.hAdapter)))

    if not values and failures:
        return HagsCaps(
            detail="Windows не сообщает возможности WDDM 2.7 — вероятно, "
            "сборка старше 2004 или драйвер устарел"
        )
    return decode_caps(values)


__all__ = ["HagsCaps", "decode_caps", "query"]
