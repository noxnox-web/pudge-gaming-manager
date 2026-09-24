"""What the Steam scan found: libraries and installed games.

Sizes are read from each game's manifest (``SizeOnDisk``) rather than by
walking the folder, so a scan of a library with a hundred games stays fast.
A size that could not be read is ``None``, never zero — the same honesty rule
the hardware scan follows.
"""

from __future__ import annotations

import pathlib
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SteamLibrary:
    """One Steam library folder."""

    path: pathlib.Path
    """The library root — its ``steamapps`` holds the manifests and games."""

    @property
    def steamapps(self) -> pathlib.Path:
        return self.path / "steamapps"

    @property
    def common(self) -> pathlib.Path:
        return self.steamapps / "common"


@dataclass(frozen=True, slots=True)
class InstalledGame:
    """One installed Steam game, as its manifest describes it."""

    app_id: int
    name: str
    install_dir: str
    """Folder name under ``steamapps/common`` — from the manifest, not guessed."""

    library: SteamLibrary
    manifest_path: pathlib.Path
    size_bytes: int | None = None

    @property
    def install_path(self) -> pathlib.Path:
        return self.library.common / self.install_dir

    @property
    def size_display(self) -> str:
        from ...utilities.formatting import format_size

        return format_size(self.size_bytes) if self.size_bytes is not None else "?"


@dataclass(frozen=True, slots=True)
class SteamInstall:
    """A located Steam installation."""

    path: pathlib.Path

    @property
    def steamapps(self) -> pathlib.Path:
        return self.path / "steamapps"
