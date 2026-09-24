"""Enumerating candidate files under a root, without ever following a link.

Split from ``engine.py`` alongside ``deletion.py``: that module decides what
may be deleted, this one decides what is even *seen*. The distinction is not
cosmetic — the pruning here is a safety rule in its own right, not an
optimisation. A junction planted in Temp and pointing at a profile folder
would, if descended into, present that folder's contents to the engine as
ordinary candidates sitting inside the category root.

Built on ``os.scandir``, which hands back directory entries with the metadata
the OS already read — one syscall per entry instead of a separate ``stat``
per candidate. On a Temp folder with 50 000 files that is the difference
between a scan taking a minute and a few seconds.
"""

from __future__ import annotations

import os
import pathlib
from fnmatch import fnmatch
from typing import Iterator

from ...utilities.logging_setup import get_logger
from .rules import CleanupCategory, component_is_protected, waived_fragments

_log = get_logger(__name__)


def candidates(
    root: pathlib.Path, category: CleanupCategory
) -> Iterator[tuple[pathlib.Path, os.stat_result, bool]]:
    """Yield ``(path, stat, is_link)`` for files matching ``category``.

    Directory links are never descended into. A link that is itself a
    candidate file is still yielded, flagged, so the caller can apply the
    expensive resolving containment check to it and only to it.
    """
    patterns = category.patterns
    # The walk prunes protected directory names as it goes, so it must
    # honour the same waiver the approval gate does. Otherwise a category
    # rooted inside a waived vendor folder would descend into nothing and
    # silently report itself empty.
    allowed = waived_fragments(category)
    stack: list[pathlib.Path] = [root]

    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    try:
                        is_link = entry.is_symlink() or entry_is_junction(entry)
                        if entry.is_dir(follow_symlinks=False):
                            if (
                                category.recursive
                                and not is_link
                                and not component_is_protected(entry.name, allowed)
                            ):
                                stack.append(pathlib.Path(entry.path))
                            continue
                        if not matches(entry.name, patterns):
                            continue
                        yield (
                            pathlib.Path(entry.path),
                            entry.stat(follow_symlinks=False),
                            is_link,
                        )
                    except OSError:
                        continue
        except OSError as exc:
            _log.debug("cannot enumerate %s: %s", current, exc)

        if not category.recursive:
            break


def matches(name: str, patterns: tuple[str, ...]) -> bool:
    """True when ``name`` matches any of the category's glob patterns."""
    return any(pattern == "*" or fnmatch(name, pattern) for pattern in patterns)


def entry_is_junction(entry: os.DirEntry) -> bool:
    """Detect an NTFS junction on a scandir entry.

    ``is_symlink()`` does not report junctions, which is precisely the kind
    a redirection attack would use.
    """
    try:
        return bool(entry.stat(follow_symlinks=False).st_reparse_tag)
    except (OSError, AttributeError, ValueError):
        return False


__all__ = ["candidates", "entry_is_junction", "matches"]
