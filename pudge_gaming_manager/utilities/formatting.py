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


__all__ = ["format_size"]
