"""Locating bundled GUI assets, from source and from a frozen exe.

The asset lives next to this module in ``assets/``. Referencing it through
this module's own path resolves correctly both when running from source and
inside a PyInstaller bundle, where ``__file__`` points into the unpacked
temporary tree and the asset is added at the same relative location.
"""

from __future__ import annotations

import pathlib

_ASSETS = pathlib.Path(__file__).resolve().parent / "assets"


def logo_path() -> str:
    """Absolute path to the application logo (PNG with a transparent field)."""
    return str(_ASSETS / "logo.png")


__all__ = ["logo_path"]
