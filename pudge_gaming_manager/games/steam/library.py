"""Locating Steam and enumerating what it has installed. Read-only.

Steam's own files are the source of truth:

* The install path comes from the registry (both the 64-bit ``Wow6432Node``
  location the installer writes and the per-user key, in that order).
* Libraries come from ``steamapps/libraryfolders.vdf`` — a club PC often has
  a small system SSD and a big games HDD, so games are rarely all in one
  place.
* Games come from each library's ``appmanifest_*.acf``. The manifest, not the
  folder name, is authoritative for the app id and the install directory, and
  it carries the on-disk size.

Nothing here deletes or writes. It is safe to run in a normal player session.
"""

from __future__ import annotations

import pathlib

from ...utilities.logging_setup import get_logger
from .models import InstalledGame, SteamInstall, SteamLibrary
from .vdf import VdfError, loads

_log = get_logger(__name__)

_REGISTRY_LOCATIONS = (
    (r"SOFTWARE\Wow6432Node\Valve\Steam", "InstallPath"),
    (r"SOFTWARE\Valve\Steam", "InstallPath"),
)
_PER_USER = (r"Software\Valve\Steam", "SteamPath")


def find_steam() -> SteamInstall | None:
    """Return the Steam installation, or ``None`` if it is not installed."""
    for path in _registry_candidates():
        resolved = pathlib.Path(path)
        if (resolved / "steamapps").is_dir():
            return SteamInstall(path=resolved)
        if resolved.is_dir():
            _log.debug("Steam path %s has no steamapps folder", resolved)
    return None


def _registry_candidates() -> list[str]:
    try:
        import winreg
    except ImportError:  # pragma: no cover - non-Windows
        return []

    found: list[str] = []
    for hive, key, value in (
        (winreg.HKEY_LOCAL_MACHINE, *_REGISTRY_LOCATIONS[0]),
        (winreg.HKEY_LOCAL_MACHINE, *_REGISTRY_LOCATIONS[1]),
        (winreg.HKEY_CURRENT_USER, *_PER_USER),
    ):
        raw = _read_string(winreg, hive, key, value)
        if raw:
            found.append(raw)
    return found


def _read_string(winreg, hive, key: str, value: str) -> str | None:
    try:
        with winreg.OpenKey(hive, key) as handle:
            data, kind = winreg.QueryValueEx(handle, value)
    except OSError:
        return None
    return data if kind == winreg.REG_SZ and isinstance(data, str) else None


def discover_libraries(install: SteamInstall) -> list[SteamLibrary]:
    """Every library folder, starting with the install itself.

    A malformed ``libraryfolders.vdf`` is logged and the install's own
    library is still returned, so one corrupt file does not hide every game.
    """
    libraries: dict[pathlib.Path, SteamLibrary] = {}

    def add(path: pathlib.Path) -> None:
        try:
            resolved = path.resolve(strict=False)
        except (OSError, ValueError):
            resolved = path
        if (resolved / "steamapps").is_dir():
            libraries.setdefault(resolved, SteamLibrary(path=resolved))

    add(install.path)

    vdf_path = install.steamapps / "libraryfolders.vdf"
    try:
        parsed = loads(vdf_path.read_text(encoding="utf-8"))
    except (OSError, VdfError) as exc:
        _log.warning("could not read %s: %s", vdf_path, exc)
        return list(libraries.values())

    for entry in _library_paths(parsed):
        add(pathlib.Path(entry))
    return list(libraries.values())


def _library_paths(parsed: dict) -> list[str]:
    root = parsed.get("libraryfolders")
    if not isinstance(root, dict):
        return []
    paths: list[str] = []
    for entry in root.values():
        if isinstance(entry, dict):
            path = entry.get("path")
            if isinstance(path, str) and path:
                paths.append(path)
    return paths


def installed_games(libraries: list[SteamLibrary]) -> list[InstalledGame]:
    """Every installed game across the given libraries, by manifest."""
    games: list[InstalledGame] = []
    for library in libraries:
        try:
            manifests = sorted(library.steamapps.glob("appmanifest_*.acf"))
        except OSError as exc:
            _log.warning("cannot list manifests in %s: %s", library.steamapps, exc)
            continue
        for manifest in manifests:
            game = _read_manifest(manifest, library)
            if game is not None:
                games.append(game)
    return games


def _read_manifest(manifest: pathlib.Path, library: SteamLibrary) -> InstalledGame | None:
    try:
        parsed = loads(manifest.read_text(encoding="utf-8"))
    except (OSError, VdfError) as exc:
        _log.warning("skipping unreadable manifest %s: %s", manifest, exc)
        return None

    state = parsed.get("AppState")
    if not isinstance(state, dict):
        _log.warning("manifest %s has no AppState", manifest)
        return None

    app_id = _as_int(state.get("appid"))
    install_dir = state.get("installdir")
    if app_id is None or not isinstance(install_dir, str) or not install_dir:
        _log.warning("manifest %s is missing appid or installdir", manifest)
        return None

    name = state.get("name")
    return InstalledGame(
        app_id=app_id,
        name=name if isinstance(name, str) and name else f"App {app_id}",
        install_dir=install_dir,
        library=library,
        manifest_path=manifest,
        size_bytes=_as_int(state.get("SizeOnDisk")),
    )


def _as_int(value: object) -> int | None:
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None
