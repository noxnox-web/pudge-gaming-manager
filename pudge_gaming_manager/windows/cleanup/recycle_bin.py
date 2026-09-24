"""Emptying the Recycle Bin, through the shell API rather than by hand.

Why not just delete the files
-----------------------------
``C:\\$Recycle.Bin\\<SID>`` looks like an ordinary directory tree, and the
file-based cleaner could be pointed at it. It must not be. Every deleted
item is two files — the data (``$R...``) and an index record holding its
original path (``$I...``) — and Explorer keeps its own view of that pairing.
Unlinking them behind the shell's back leaves the bin showing entries that
no longer exist and, on some builds, an index it then refuses to rebuild.

``SHEmptyRecycleBinW`` is the supported way, and it is also the only one
that empties the bins of *every* drive, which is what an operator means by
"empty the recycle bin". ``SHQueryRecycleBinW`` reports the size beforehand,
so the confirmation dialog can state what will go.

The allowlisted-directory cleaner in ``engine.py`` is deliberately unaware
of all this: a mechanism this different belongs behind its own module, not
smuggled in as a category whose "root" is special-cased.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass

from ...utilities.formatting import format_size
from ...utilities.logging_setup import audit_event, get_logger

_log = get_logger(__name__)

#: Flags for SHEmptyRecycleBinW. The operator has already confirmed in our
#: own dialog, so Windows must not ask again or raise a progress window
#: over the application.
_SHERB_NOCONFIRMATION = 0x00000001
_SHERB_NOPROGRESSUI = 0x00000002
_SHERB_NOSOUND = 0x00000004

#: HRESULT the shell returns when there is nothing to empty. Not a failure.
_S_OK = 0
_E_UNEXPECTED = -2147418113  # 0x8000FFFF as a signed HRESULT


class _SHQUERYRBINFO(ctypes.Structure):
    """``SHQUERYRBINFO`` from shellapi.h.

    ``cbSize`` must be set to the structure's own size before the call;
    the shell uses it to tell struct versions apart.
    """

    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("i64Size", ctypes.c_int64),
        ("i64NumItems", ctypes.c_int64),
    ]


@dataclass(frozen=True, slots=True)
class RecycleBinState:
    """What the Recycle Bin holds, across every drive."""

    size_bytes: int = 0
    item_count: int = 0
    available: bool = True
    unavailable_reason: str = ""

    @property
    def has_content(self) -> bool:
        return self.available and self.item_count > 0

    @property
    def size_display(self) -> str:
        return format_size(self.size_bytes)


def query() -> RecycleBinState:
    """Report the total size and item count of the Recycle Bin.

    Passing a null root path asks about every drive at once. Changes
    nothing.
    """
    try:
        shell = ctypes.windll.shell32  # type: ignore[attr-defined]
    except (AttributeError, OSError) as exc:  # not Windows
        return RecycleBinState(
            available=False, unavailable_reason=f"нет доступа к shell32: {exc}"
        )

    info = _SHQUERYRBINFO()
    info.cbSize = ctypes.sizeof(_SHQUERYRBINFO)
    try:
        result = shell.SHQueryRecycleBinW(None, ctypes.byref(info))
    except OSError as exc:
        return RecycleBinState(
            available=False, unavailable_reason=f"запрос не выполнен: {exc}"
        )

    if result != _S_OK:
        return RecycleBinState(
            available=False,
            unavailable_reason=f"Windows вернул код 0x{result & 0xFFFFFFFF:08X}",
        )
    return RecycleBinState(
        size_bytes=int(info.i64Size), item_count=int(info.i64NumItems)
    )


def empty() -> tuple[bool, str]:
    """Empty the Recycle Bin on every drive.

    Returns ``(ok, detail)``. An already-empty bin counts as success: the
    shell reports ``E_UNEXPECTED`` for it on some builds, and telling the
    operator that emptying an empty bin failed would be false.
    """
    before = query()
    if not before.available:
        return False, f"Корзина недоступна: {before.unavailable_reason}"
    if not before.has_content:
        return True, "Корзина уже пуста."

    shell = ctypes.windll.shell32  # type: ignore[attr-defined]
    flags = _SHERB_NOCONFIRMATION | _SHERB_NOPROGRESSUI | _SHERB_NOSOUND
    try:
        result = shell.SHEmptyRecycleBinW(None, None, flags)
    except OSError as exc:
        _log.warning("SHEmptyRecycleBinW raised: %s", exc)
        return False, f"Очистка корзины не выполнена: {exc}"

    after = query()
    freed = max(before.size_bytes - after.size_bytes, 0)
    emptied = after.available and after.item_count == 0

    audit_event(
        "cleanup",
        "empty_recycle_bin",
        target="all drives",
        old_state={"items": before.item_count, "bytes": before.size_bytes},
        new_state={"items": after.item_count, "bytes": after.size_bytes},
        result="SUCCESS" if emptied else "PARTIAL",
    )

    if emptied:
        return True, f"Корзина очищена: удалено объектов {before.item_count}."
    if result not in (_S_OK, _E_UNEXPECTED):
        return False, (
            f"Windows вернул код 0x{result & 0xFFFFFFFF:08X}; "
            f"в корзине осталось объектов: {after.item_count}."
        )
    # Items held open by another process survive; say so rather than
    # claiming a clean sweep.
    return False, (
        f"Освобождено {format_size(freed)}, но в корзине осталось объектов: "
        f"{after.item_count} — вероятно, они заняты другим процессом."
    )


__all__ = ["RecycleBinState", "empty", "query"]
