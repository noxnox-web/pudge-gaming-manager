"""Core topology: SMT, performance/efficiency cores, and cache domains.

``GetSystemCpuSetInformation`` returns one ``SYSTEM_CPU_SET_INFORMATION``
record per logical processor, carrying its physical core index, its
last-level-cache index and its *efficiency class*. That is enough to answer
the questions guides raise about affinity without guessing from the model
name:

* **SMT** — more logical processors than physical cores.
* **P/E cores** (Intel 12th gen and later) — more than one efficiency
  class; the highest class is the performance cores.
* **Cache domains** — more than one last-level cache; on a Ryzen 9 X3D this
  is the two CCDs, only one of which has the 3D V-Cache.

Read-only, and no affinity is ever *set* from this: the point of showing it
is that pinning a game to cores by hand is easy to get wrong on exactly
these machines.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass

#: ``CpuSetInformation`` — the only record type defined.
_CPU_SET_INFORMATION = 0

#: Byte offsets inside one record (winnt.h, SYSTEM_CPU_SET_INFORMATION):
#: Size(4) Type(4) Id(4) Group(2) LogicalProcessorIndex(1) CoreIndex(1)
#: LastLevelCacheIndex(1) NumaNodeIndex(1) EfficiencyClass(1) ...
_OFF_SIZE = 0
_OFF_TYPE = 4
_OFF_GROUP = 12
_OFF_CORE = 15
_OFF_LLC = 16
_OFF_EFFICIENCY = 18


@dataclass(frozen=True, slots=True)
class CpuTopology:
    logical: int = 0
    physical: int = 0
    performance_cores: int = 0
    efficiency_cores: int = 0
    cache_domains: int = 0
    error: str = ""

    @property
    def smt(self) -> bool:
        return self.physical > 0 and self.logical > self.physical

    @property
    def hybrid(self) -> bool:
        return self.efficiency_cores > 0


def parse(buffer: bytes) -> CpuTopology:
    """Decode the raw records. Pure, so it is testable without the API."""
    cores: dict[tuple[int, int], int] = {}
    caches: set[tuple[int, int]] = set()
    logical = 0
    offset = 0
    while offset + _OFF_EFFICIENCY < len(buffer):
        size = int.from_bytes(buffer[offset + _OFF_SIZE: offset + _OFF_SIZE + 4], "little")
        if size <= 0:
            break
        kind = int.from_bytes(buffer[offset + _OFF_TYPE: offset + _OFF_TYPE + 4], "little")
        if kind == _CPU_SET_INFORMATION:
            group = int.from_bytes(buffer[offset + _OFF_GROUP: offset + _OFF_GROUP + 2], "little")
            core = buffer[offset + _OFF_CORE]
            llc = buffer[offset + _OFF_LLC]
            efficiency = buffer[offset + _OFF_EFFICIENCY]
            logical += 1
            cores[(group, core)] = efficiency
            caches.add((group, llc))
        offset += size

    classes = set(cores.values())
    top = max(classes) if classes else 0
    performance = sum(1 for c in cores.values() if c == top)
    return CpuTopology(
        logical=logical,
        physical=len(cores),
        performance_cores=performance if len(classes) > 1 else len(cores),
        efficiency_cores=len(cores) - performance if len(classes) > 1 else 0,
        cache_domains=len(caches),
    )


def read() -> CpuTopology:
    """Ask Windows. Never raises."""
    try:
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        query = kernel.GetSystemCpuSetInformation
    except (AttributeError, OSError) as exc:
        return CpuTopology(error=str(exc))

    needed = wintypes.ULONG(0)
    query(None, 0, ctypes.byref(needed), None, 0)
    if needed.value == 0:
        return CpuTopology(error=f"код ошибки {ctypes.get_last_error()}")
    buffer = ctypes.create_string_buffer(needed.value)
    if not query(buffer, needed, ctypes.byref(needed), None, 0):
        return CpuTopology(error=f"код ошибки {ctypes.get_last_error()}")
    return parse(buffer.raw[: needed.value])


__all__ = ["CpuTopology", "parse", "read"]
