"""What the cleaner is allowed to touch, and what it must never touch.

This module owns the *guards*. The catalogue of categories lives next door
in ``categories.py``; splitting them keeps each file reviewable and makes it
plain that adding a category cannot weaken a guard.

The cleaner cannot delete anything that is not described by a category, and
every category names explicit root directories resolved from environment
variables. There is no "scan the disk for junk" mode, because that is how a
cleaner deletes someone's save games.

Five independent safety layers
------------------------------
1. **Allowlisted roots.** Only the directories below are ever considered.
2. **Containment.** A candidate's *resolved* path must still sit inside its
   category root. Windows Temp routinely contains junctions, and following
   one without this check would walk the cleaner straight out of the
   sandbox.
3. **Protected paths.** Club software, launchers, anti-cheat and user
   documents are refused even if some future category points at them.
4. **Protected extensions.** Documents, saves and executables are refused
   inside temp folders. A category that exists precisely to remove the
   user's own files — the Downloads folder — waives this one by declaring
   ``clears_user_files``; credentials are refused even then.
5. **Minimum age.** Files younger than the category's threshold are left
   alone, because a file created seconds ago is probably open right now.

The root directory itself is never removed — only its contents.
"""

from __future__ import annotations

import os
import pathlib
from dataclasses import dataclass
from enum import Enum

#: Most matches a single wildcard root may expand to. Browser profile globs
#: resolve to a handful of directories; anything wildly beyond that means
#: the pattern is wrong, and a wrong pattern must not become a licence to
#: walk half the profile.
MAX_ROOT_MATCHES = 64

#: Fixed path components a wildcard root must have before its first
#: wildcard. ``%LOCALAPPDATA%\\*`` is refused: a pattern has to name the
#: vendor and the product before it may guess at a profile name.
MIN_FIXED_COMPONENTS = 3


class CleanupRisk(str, Enum):
    """How much a user could miss what is being deleted."""

    SAFE = "SAFE"
    """Regenerated automatically, no user-visible effect."""

    LOW = "LOW"
    """Regenerated, but the first use afterwards is slower."""

    MEDIUM = "MEDIUM"
    """The user's own files, not data the system recreates."""


@dataclass(frozen=True, slots=True)
class CleanupCategory:
    """One kind of removable data."""

    id: str
    name: str
    description: str
    rationale: str
    """Why this is safe to delete — required, same standard as a tweak."""

    roots: tuple[str, ...]
    """Environment-variable templates, e.g. ``'%TEMP%'``. A template may
    contain ``*`` or ``?`` in its trailing components to address profile
    directories whose names are not known ahead of time."""

    patterns: tuple[str, ...] = ("*",)
    """Glob patterns applied within each root."""

    recursive: bool = True
    min_age_hours: float = 1.0
    """Files modified more recently than this are skipped as possibly in use."""

    risk: CleanupRisk = CleanupRisk.SAFE
    requires_admin: bool = False
    remove_empty_dirs: bool = True
    enabled_by_default: bool = True

    clears_user_files: bool = False
    """This category holds the user's own files rather than data the system
    regenerates, so the protected-extension guard does not apply to it.
    Only the Downloads folder sets this: refusing to delete ``.exe`` and
    ``.pdf`` there would leave behind exactly what the operator asked to
    clear. Credentials stay protected regardless."""


def _split_at_wildcard(path: pathlib.Path) -> tuple[pathlib.Path, str] | None:
    """Split ``path`` into (fixed anchor, glob pattern) at its first wildcard.

    Returns ``None`` when the path holds no wildcard, or when too few fixed
    components precede it for the pattern to be trustworthy.
    """
    parts = path.parts
    for index, part in enumerate(parts):
        if "*" in part or "?" in part:
            if index < MIN_FIXED_COMPONENTS:
                return None
            return pathlib.Path(*parts[:index]), str(pathlib.Path(*parts[index:]))
    return None


def _expand(template: str) -> list[pathlib.Path]:
    """Resolve one root template to the existing directories it names.

    A plain template yields at most one directory. A wildcard template
    yields every directory it matches, which is how a single entry covers
    every Chrome profile and Firefox's randomly named profile directory
    without hard-coding names that differ on every PC.
    """
    expanded = os.path.expandvars(template)
    if "%" in expanded:  # an unset variable — do not guess a path
        return []

    path = pathlib.Path(expanded)
    split = _split_at_wildcard(path)
    if split is None:
        if "*" in expanded or "?" in expanded:
            return []  # a wildcard too close to the drive root
        return [path] if path.is_dir() else []

    anchor, pattern = split
    try:
        matches = sorted(p for p in anchor.glob(pattern) if p.is_dir())
    except (OSError, ValueError, IndexError):
        return []
    return matches[:MAX_ROOT_MATCHES]


def resolve_roots(category: CleanupCategory) -> list[pathlib.Path]:
    """Return the category's roots that actually exist on this machine."""
    resolved: list[pathlib.Path] = []
    for template in category.roots:
        resolved.extend(_expand(template))
    return resolved


# --------------------------------------------------------------------------
# Protected paths — never deleted from, whatever a category says
# --------------------------------------------------------------------------

#: Directory names that mark club infrastructure or irreplaceable user data.
#: Matched case-insensitively against every component of a candidate path.
PROTECTED_PATH_FRAGMENTS: tuple[str, ...] = (
    # Club software (rule #47). Untested here — no SmartShell on the
    # development machine — so the matching is deliberately broad.
    "smartshell",
    "senet",
    "langame",
    "gizmo",
    # Launchers and their depots
    "steamapps",
    "steamlibrary",
    "epic games",
    "riot games",
    "battle.net",
    "gog galaxy",
    # Anti-cheat
    "easyanticheat",
    "battleye",
    "vanguard",
    "faceit",
    "esea",
    # Irreplaceable user data
    "documents",
    "desktop",
    "pictures",
    "videos",
    "music",
    "onedrive",
    "saved games",
    "my games",
    # Credentials and identity
    "credentials",
    "protect",
    "vault",
)

#: Whole directories that must never be recursed into, by absolute path.
PROTECTED_ROOTS: tuple[str, ...] = (
    "%SystemRoot%\\System32",
    "%SystemRoot%\\SysWOW64",
    "%ProgramFiles%",
    "%ProgramFiles(x86)%",
    "%ProgramData%\\Microsoft\\Windows\\Start Menu",
)

#: Never deleted by any category, not even one that clears user files.
#: Losing a private key or a password database is unrecoverable in a way
#: that losing a downloaded installer is not.
CREDENTIAL_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".key", ".pem", ".pfx", ".p12",  # keys and certificates
        ".kdbx", ".psafe3",              # password databases
        ".ovpn", ".ppk",                 # VPN and SSH profiles
    }
)

#: Refused by every category that clears *system* data — the things a
#: cleaner has no business touching inside a temp folder. A
#: ``clears_user_files`` category may remove them, because there the whole
#: point is to clear the user's own downloads.
PROTECTED_EXTENSIONS: frozenset[str] = CREDENTIAL_EXTENSIONS | frozenset(
    {
        ".sav", ".save", ".sgame",       # game saves
        ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".pdf", ".odt",
        ".sys", ".dll", ".exe",          # never remove executables or drivers
    }
)


def protected_roots() -> list[pathlib.Path]:
    """Resolved protected directories present on this machine."""
    resolved: list[pathlib.Path] = []
    for template in PROTECTED_ROOTS:
        resolved.extend(_expand(template))
    return resolved


def protected_extensions(*, allow_user_files: bool = False) -> frozenset[str]:
    """The extension guard that applies to a given category.

    Args:
        allow_user_files: The category declares ``clears_user_files``, so
            only credentials remain protected.
    """
    return CREDENTIAL_EXTENSIONS if allow_user_files else PROTECTED_EXTENSIONS


__all__ = [
    "CREDENTIAL_EXTENSIONS",
    "CleanupCategory",
    "CleanupRisk",
    "MAX_ROOT_MATCHES",
    "MIN_FIXED_COMPONENTS",
    "PROTECTED_EXTENSIONS",
    "PROTECTED_PATH_FRAGMENTS",
    "PROTECTED_ROOTS",
    "protected_extensions",
    "protected_roots",
    "resolve_roots",
]
