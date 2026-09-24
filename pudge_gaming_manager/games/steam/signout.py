"""Signing every Steam account out of a club PC.

Steam keeps sign-in state in two places, and a reset must clear both:

* ``<Steam>\\config`` — the account list (``loginusers.vdf``), recent
  co-players, avatars. Emptied the way SteamWiper does it, except that
  ``config.vdf`` (download region, client settings) and ``libraryfolders.vdf``
  are kept: the latter is Steam's own record of the library folders, and
  deleting it can make every game on a second drive look uninstalled.
* ``%LOCALAPPDATA%\\Steam`` in each **Windows user's profile** —
  ``local.vdf`` holds ``ConnectCache``, the tokens that sign an account in
  without a password, and ``htmlcache`` holds the client's web cookies
  (store and community sessions). Clearing ``config`` alone leaves these, so
  Steam can sign the last player straight back in.

Profiles are enumerated from the registry rather than assumed: on a club PC
the player's Windows account and the administrator running PGM are usually
different, and it is the player's profile that holds the tokens.
"""

from __future__ import annotations

import os
import pathlib

from ...utilities.logging_setup import get_logger
from .models import SteamInstall
from .targets import CacheTarget, ClearMode, dir_size

_log = get_logger(__name__)

#: Kept when ``config`` is emptied (compared case-insensitively).
CONFIG_KEEP = frozenset({"config.vdf", "libraryfolders.vdf"})

_PROFILE_LIST = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\ProfileList"


def signout_targets(install: SteamInstall) -> list[CacheTarget]:
    """Everything that, once cleared, signs every account out."""
    targets: list[CacheTarget] = []

    config = install.path / "config"
    if config.is_dir():
        size = 0
        for child in _children(config):
            if child.name.lower() in CONFIG_KEEP:
                continue
            size += (dir_size(child) or 0) if child.is_dir() else _file_size(child)
        targets.append(
            CacheTarget(
                config, "config — список аккаунтов и история входов (Steam)", size,
                mode=ClearMode.CONTENTS, keep_names=CONFIG_KEEP,
            )
        )

    for profile in _profile_dirs():
        steam = profile / "AppData" / "Local" / "Steam"
        if not _no_redirection(steam):
            # A junction here would aim a fixed-name delete somewhere else.
            _log.warning("skipping %s: it is redirected by a link", steam)
            continue
        user = profile.name
        tokens = steam / "local.vdf"
        if tokens.is_file():
            targets.append(
                CacheTarget(
                    tokens, f"сохранённые токены входа ({user})", _file_size(tokens),
                    mode=ClearMode.FILE,
                )
            )
        web = steam / "htmlcache"
        if web.is_dir():
            targets.append(
                CacheTarget(web, f"веб-куки Steam ({user})", dir_size(web))
            )
    return targets


def _profile_dirs() -> list[pathlib.Path]:
    """Every local user profile folder Windows knows about."""
    try:
        import winreg
    except ImportError:  # pragma: no cover - non-Windows
        return []

    profiles: list[pathlib.Path] = []
    try:
        root = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _PROFILE_LIST)
    except OSError:
        return profiles
    with root:
        index = 0
        while True:
            try:
                sid = winreg.EnumKey(root, index)
            except OSError:
                break
            index += 1
            # S-1-5-21-* are real accounts; system and service profiles
            # (S-1-5-18/19/20) never run the Steam client.
            if not sid.startswith("S-1-5-21-"):
                continue
            try:
                with winreg.OpenKey(root, sid) as key:
                    image, _ = winreg.QueryValueEx(key, "ProfileImagePath")
            except OSError:
                continue
            path = pathlib.Path(os.path.expandvars(str(image)))
            if path.is_dir():
                profiles.append(path)
    return profiles


def _no_redirection(path: pathlib.Path) -> bool:
    """True when no link anywhere in ``path`` points it elsewhere."""
    try:
        resolved = path.resolve(strict=False)
    except (OSError, RuntimeError):
        return False
    return os.path.normcase(str(resolved)) == os.path.normcase(str(path))


def _children(folder: pathlib.Path) -> list[pathlib.Path]:
    try:
        return list(folder.iterdir())
    except OSError:
        return []


def _file_size(path: pathlib.Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


__all__ = ["CONFIG_KEEP", "signout_targets"]
