"""What the cleaner is allowed to touch, and what it must never touch.

This module is a **allowlist**. The cleaner cannot delete anything that is
not described by a category here, and every category names an explicit root
directory resolved from an environment variable. There is no "scan the disk
for junk" mode, because that is how a cleaner deletes someone's save games.

Four independent safety layers
------------------------------
1. **Allowlisted roots.** Only the directories below are ever considered.
2. **Containment.** A candidate's *resolved* path must still sit inside its
   category root. Windows Temp routinely contains junctions, and following
   one without this check would walk the cleaner straight out of the
   sandbox.
3. **Protected paths.** Club software, launchers, anti-cheat and user
   documents are refused even if some future category points at them.
4. **Minimum age.** Files younger than the category's threshold are left
   alone, because a file created seconds ago is probably open right now.

The root directory itself is never removed — only its contents.
"""

from __future__ import annotations

import os
import pathlib
from dataclasses import dataclass
from enum import Enum


class CleanupRisk(str, Enum):
    """How much a user could miss what is being deleted."""

    SAFE = "SAFE"
    """Regenerated automatically, no user-visible effect."""

    LOW = "LOW"
    """Regenerated, but the first use afterwards is slower."""

    MEDIUM = "MEDIUM"
    """A user could notice, e.g. losing browser cache on a shared PC."""


@dataclass(frozen=True, slots=True)
class CleanupCategory:
    """One kind of removable data."""

    id: str
    name: str
    description: str
    rationale: str
    """Why this is safe to delete — required, same standard as a tweak."""

    roots: tuple[str, ...]
    """Environment-variable templates, e.g. ``'%TEMP%'``."""

    patterns: tuple[str, ...] = ("*",)
    """Glob patterns applied within each root."""

    recursive: bool = True
    min_age_hours: float = 1.0
    """Files modified more recently than this are skipped as possibly in use."""

    risk: CleanupRisk = CleanupRisk.SAFE
    requires_admin: bool = False
    remove_empty_dirs: bool = True
    enabled_by_default: bool = True


def _expand(template: str) -> pathlib.Path | None:
    """Resolve an environment-variable template to an existing directory."""
    expanded = os.path.expandvars(template)
    if "%" in expanded:  # an unset variable — do not guess a path
        return None
    path = pathlib.Path(expanded)
    return path if path.is_dir() else None


def resolve_roots(category: CleanupCategory) -> list[pathlib.Path]:
    """Return the category's roots that actually exist on this machine."""
    resolved: list[pathlib.Path] = []
    for template in category.roots:
        path = _expand(template)
        if path is not None:
            resolved.append(path)
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

#: File extensions never deleted, regardless of location or age. These are
#: the things a cleaner has no business touching even inside a temp folder.
PROTECTED_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".sav", ".save", ".sgame",       # game saves
        ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".pdf", ".odt",
        ".key", ".pem", ".pfx", ".p12",  # keys and certificates
        ".kdbx",                          # password databases
        ".sys", ".dll", ".exe",          # never remove executables or drivers
    }
)


def protected_roots() -> list[pathlib.Path]:
    """Resolved protected directories present on this machine."""
    resolved: list[pathlib.Path] = []
    for template in PROTECTED_ROOTS:
        path = _expand(template)
        if path is not None:
            resolved.append(path)
    return resolved


# --------------------------------------------------------------------------
# The categories
# --------------------------------------------------------------------------

CATEGORIES: tuple[CleanupCategory, ...] = (
    CleanupCategory(
        id="temp.user",
        name="User temporary files",
        description="Temporary files created by applications for this user.",
        rationale=(
            "Windows and applications write scratch data here and are "
            "expected to clean up after themselves; much of it is orphaned "
            "by crashes. Anything still needed is recreated on demand."
        ),
        roots=("%TEMP%", "%TMP%"),
        min_age_hours=24.0,
        risk=CleanupRisk.SAFE,
    ),
    CleanupCategory(
        id="temp.windows",
        name="Windows temporary files",
        description="Machine-wide scratch directory used by installers.",
        rationale=(
            "Installer and servicing scratch space. Entries older than a day "
            "belong to finished operations."
        ),
        roots=("%SystemRoot%\\Temp",),
        min_age_hours=24.0,
        risk=CleanupRisk.SAFE,
        requires_admin=True,
    ),
    CleanupCategory(
        id="cache.shader.directx",
        name="DirectX shader cache",
        description="Compiled shader cache for Direct3D.",
        rationale=(
            "Rebuilt automatically by the driver. Clearing it costs a few "
            "seconds of extra stutter the first time a game runs, and "
            "resolves the corrupted-cache crashes that follow a driver "
            "update."
        ),
        roots=("%LOCALAPPDATA%\\D3DSCache",),
        min_age_hours=0.0,
        risk=CleanupRisk.LOW,
    ),
    CleanupCategory(
        id="cache.shader.nvidia",
        name="NVIDIA shader cache",
        description="NVIDIA DirectX and OpenGL shader caches.",
        rationale=(
            "Regenerated by the driver. A stale cache after a driver update "
            "is a common cause of first-launch crashes."
        ),
        roots=(
            "%LOCALAPPDATA%\\NVIDIA\\DXCache",
            "%LOCALAPPDATA%\\NVIDIA\\GLCache",
            "%LOCALAPPDATA%\\NVIDIA Corporation\\NV_Cache",
        ),
        min_age_hours=0.0,
        risk=CleanupRisk.LOW,
    ),
    CleanupCategory(
        id="cache.thumbnails",
        name="Thumbnail cache",
        description="Explorer thumbnail database files.",
        rationale=(
            "Explorer rebuilds thumbnails on demand. Clearing fixes the "
            "stale or wrong preview images that accumulate over time."
        ),
        roots=("%LOCALAPPDATA%\\Microsoft\\Windows\\Explorer",),
        patterns=("thumbcache_*.db", "iconcache_*.db"),
        recursive=False,
        min_age_hours=0.0,
        risk=CleanupRisk.SAFE,
    ),
    CleanupCategory(
        id="dumps.crash",
        name="Crash dumps",
        description="Application crash dumps and Windows error reports.",
        rationale=(
            "Diagnostic snapshots of past crashes. They are only useful "
            "while actively debugging that crash and can be very large."
        ),
        roots=(
            "%LOCALAPPDATA%\\CrashDumps",
            "%LOCALAPPDATA%\\Microsoft\\Windows\\WER\\ReportArchive",
            "%LOCALAPPDATA%\\Microsoft\\Windows\\WER\\ReportQueue",
        ),
        min_age_hours=24.0,
        risk=CleanupRisk.LOW,
    ),
    CleanupCategory(
        id="cache.delivery_optimization",
        name="Delivery Optimization cache",
        description="Peer-to-peer update data cached for other PCs.",
        rationale=(
            "Windows caches update content here to share with other machines "
            "on the LAN. It is repopulated as needed and can reach several "
            "gigabytes on a club network."
        ),
        roots=("%SystemRoot%\\SoftwareDistribution\\DeliveryOptimization",),
        min_age_hours=24.0,
        risk=CleanupRisk.LOW,
        requires_admin=True,
    ),
    CleanupCategory(
        id="logs.windows",
        name="Windows log files",
        description="Servicing and setup logs.",
        rationale=(
            "Text logs from completed Windows servicing operations. They are "
            "read only when diagnosing a failed update."
        ),
        roots=("%SystemRoot%\\Logs\\CBS", "%SystemRoot%\\Logs\\DISM"),
        patterns=("*.log", "*.cab", "*.etl"),
        min_age_hours=168.0,  # one week
        risk=CleanupRisk.LOW,
        requires_admin=True,
        enabled_by_default=False,
    ),
    # Browser caches name the exact subdirectories they clear. Cookies,
    # saved passwords, history and profile data are deliberately absent:
    # rule #46 requires stating precisely what browser data is removed, and
    # signing a player out of their accounts is not "cleanup".
    CleanupCategory(
        id="cache.browser.chrome",
        name="Chrome cache",
        description="Google Chrome page and shader cache. Not cookies, passwords or history.",
        rationale=(
            "Cached page resources, rebuilt on next visit. Deliberately "
            "excludes cookies, saved logins and history so a player is not "
            "signed out of their accounts."
        ),
        roots=(
            "%LOCALAPPDATA%\\Google\\Chrome\\User Data\\Default\\Cache",
            "%LOCALAPPDATA%\\Google\\Chrome\\User Data\\Default\\Code Cache",
            "%LOCALAPPDATA%\\Google\\Chrome\\User Data\\Default\\GPUCache",
        ),
        min_age_hours=0.0,
        risk=CleanupRisk.MEDIUM,
        enabled_by_default=False,
    ),
    CleanupCategory(
        id="cache.browser.edge",
        name="Edge cache",
        description="Microsoft Edge page and shader cache. Not cookies, passwords or history.",
        rationale=(
            "Cached page resources, rebuilt on next visit. Excludes cookies, "
            "saved logins and history."
        ),
        roots=(
            "%LOCALAPPDATA%\\Microsoft\\Edge\\User Data\\Default\\Cache",
            "%LOCALAPPDATA%\\Microsoft\\Edge\\User Data\\Default\\Code Cache",
            "%LOCALAPPDATA%\\Microsoft\\Edge\\User Data\\Default\\GPUCache",
        ),
        min_age_hours=0.0,
        risk=CleanupRisk.MEDIUM,
        enabled_by_default=False,
    ),
)


CATEGORIES_BY_ID: dict[str, CleanupCategory] = {c.id: c for c in CATEGORIES}


def default_categories() -> tuple[CleanupCategory, ...]:
    """Categories enabled unless the operator says otherwise.

    Browser caches and Windows logs are off by default: the first can sign a
    player out of a session, the second is occasionally needed to diagnose a
    failed update.
    """
    return tuple(c for c in CATEGORIES if c.enabled_by_default)
