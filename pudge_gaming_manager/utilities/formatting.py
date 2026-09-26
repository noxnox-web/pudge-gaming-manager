"""Small formatting helpers shared across layers.

These are pure, dependency-free string helpers, so they live in
``utilities`` where every layer may use them — the cleaner, the Steam wiper
and the GUI all need to render a byte count the same way.
"""

from __future__ import annotations


def format_size(num_bytes: int) -> str:
    """Human-readable size. Kept here so the UI and logs agree."""
    size = float(num_bytes)
    for unit in ("Б", "КБ", "МБ", "ГБ", "ТБ"):
        if size < 1024.0 or unit == "TB":
            return f"{size:.0f} {unit}" if unit == "Б" else f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} ТБ"


def plural_ru(count: int, one: str, few: str, many: str) -> str:
    """``count`` with the Russian noun form it takes: 1 модуль, 2 модуля, 5 модулей."""
    tail = count % 100
    if 11 <= tail <= 14:
        form = many
    elif tail % 10 == 1:
        form = one
    elif 2 <= tail % 10 <= 4:
        form = few
    else:
        form = many
    return f"{count} {form}"


__all__ = ["format_size", "plural_ru"]
