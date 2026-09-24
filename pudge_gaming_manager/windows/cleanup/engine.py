"""The cleanup engine: scan, then delete only what the scan approved.

Flow (rule #9)::

    SCAN -> SHOW -> CONFIRM -> CLEAN -> VERIFY

``scan`` mutates nothing and produces a full inventory with sizes and
counts. ``clean`` deletes only paths that a scan already approved, and
re-checks every safety rule at deletion time rather than trusting the
inventory — the filesystem can change between the two.

Containment
-----------
Windows Temp commonly contains junctions and symlinks. Walking one without
resolving it would take the cleaner outside its sandbox, so every candidate
is resolved and re-tested for containment before it is touched. Directory
links are never descended into.
"""

from __future__ import annotations

import os
import pathlib
import time
from fnmatch import fnmatch
from typing import Iterator

from ...utilities.logging_setup import audit_event, get_logger
from ...utilities.privileges import is_admin
from .report import (
    CategoryReport,
    CleanResult,
    CleanupItem,
    ScanReport,
)
from ...utilities.secure_delete import DeleteError, delete_file
from .categories import default_categories
from .rules import (
    PROTECTED_PATH_FRAGMENTS,
    CleanupCategory,
    protected_extensions,
    protected_roots,
    resolve_roots,
)

_log = get_logger(__name__)


class CleanupEngine:
    """Scans for removable data and deletes it under strict containment."""

    def __init__(self, categories: tuple[CleanupCategory, ...] | None = None) -> None:
        self.categories = categories if categories is not None else default_categories()
        self._protected_roots = protected_roots()

    # -- safety ------------------------------------------------------------

    def is_protected(
        self, path: pathlib.Path, *, allow_user_files: bool = False
    ) -> bool:
        """True when a path must never be deleted.

        Fragments are matched against individual path **components**, never
        against the whole path string. Substring-matching the full path is
        both too broad and unpredictable: a directory called
        ``build-protected-assets`` would match the ``protect`` fragment
        (added for the DPAPI key store) and silently disable cleanup for
        everything beneath it.

        Args:
            allow_user_files: The owning category declares
                ``clears_user_files``, so only the credential extensions
                are refused. The path and root guards still apply in full —
                this relaxes one layer, never the sandbox itself.
        """
        if any(_component_is_protected(part) for part in path.parts):
            return True
        if path.suffix.lower() in protected_extensions(
            allow_user_files=allow_user_files
        ):
            return True
        for root in self._protected_roots:
            try:
                if path.is_relative_to(root):
                    return True
            except (OSError, ValueError):
                return True  # cannot prove it is safe, so refuse
        return False

    @staticmethod
    def _contained(candidate: pathlib.Path, root: pathlib.Path) -> bool:
        """True when ``candidate`` really lives inside ``root``.

        Both sides are resolved, which is what defeats a junction pointing
        somewhere else entirely. This costs a filesystem round-trip per
        call, so the scan uses it only for entries that are actually links;
        deletion always uses it, where correctness beats speed.
        """
        try:
            return candidate.resolve(strict=False).is_relative_to(
                root.resolve(strict=False)
            )
        except (OSError, ValueError, RuntimeError):
            return False

    @staticmethod
    def _contained_lexically(candidate: pathlib.Path, root: pathlib.Path) -> bool:
        """Containment by path arithmetic alone — no I/O.

        Sound for entries produced by the scan's own walk, which never
        descends into a link, so no component between ``root`` and
        ``candidate`` can redirect elsewhere.
        """
        try:
            return candidate.is_relative_to(root)
        except (OSError, ValueError):
            return False

    def _approves(
        self,
        path: pathlib.Path,
        root: pathlib.Path,
        category: CleanupCategory,
        now: float,
        stat: os.stat_result | None = None,
        resolve_containment: bool = True,
    ) -> tuple[bool, str]:
        """The single gate every candidate passes, at scan *and* at delete.

        Args:
            stat: A stat already taken for this path, to avoid a second
                syscall per file. Omitted at deletion time, where the point
                is precisely to re-read the current state.
            resolve_containment: Use the resolving containment check. The
                scan passes ``False`` for non-link entries it walked itself
                and handles links separately; deletion always leaves it on.
        """
        contained = (
            self._contained(path, root)
            if resolve_containment
            else self._contained_lexically(path, root)
        )
        if not contained:
            return False, "outside its category root"
        if self.is_protected(path, allow_user_files=category.clears_user_files):
            return False, "protected"
        if stat is None:
            try:
                stat = path.stat()
            except OSError:
                return False, "unreadable"
        if category.min_age_hours > 0:
            age_hours = (now - stat.st_mtime) / 3600.0
            if age_hours < category.min_age_hours:
                return False, "too new"
        return True, ""

    # -- scan --------------------------------------------------------------

    def scan(self) -> ScanReport:
        """Inventory every enabled category. Mutates nothing."""
        started = time.monotonic()
        report = ScanReport()
        now = time.time()

        for category in self.categories:
            report.categories.append(self._scan_category(category, now))

        report.duration_s = time.monotonic() - started
        _log.info(
            "cleanup scan: %s in %d files across %d categories (%.2fs)",
            report.size_display, report.total_files, len(report.categories),
            report.duration_s,
        )
        return report

    def _scan_category(
        self, category: CleanupCategory, now: float
    ) -> CategoryReport:
        result = CategoryReport(category=category)

        if category.requires_admin and not is_admin():
            result.available = False
            result.unavailable_reason = "нужны права администратора"
            return result

        roots = resolve_roots(category)
        if not roots:
            result.available = False
            result.unavailable_reason = "на этом ПК нет"
            return result

        result.roots_scanned = roots
        for root in roots:
            resolved_root = _resolve(root)
            for path, stat, is_link in self._candidates(root, category):
                # Containment only needs the expensive resolve() when the
                # entry is itself a link. Everything else came from a walk
                # that already pruned links, so a lexical check is sound —
                # and resolve() per file is what made a scan take a minute.
                if is_link and not self._contained(path, resolved_root):
                    result.skipped_protected += 1
                    continue

                approved, reason = self._approves(
                    path, root, category, now, stat=stat,
                    resolve_containment=is_link,
                )
                if approved:
                    result.items.append(
                        CleanupItem(
                            path=path,
                            size_bytes=stat.st_size,
                            modified=stat.st_mtime,
                        )
                    )
                elif reason == "protected":
                    result.skipped_protected += 1
                elif reason == "too new":
                    result.skipped_too_new += 1
                else:
                    result.unreadable += 1
        return result

    def _candidates(
        self, root: pathlib.Path, category: CleanupCategory
    ) -> Iterator[tuple[pathlib.Path, os.stat_result, bool]]:
        """Yield ``(path, stat, is_link)`` for files matching the category.

        Built on ``os.scandir``, which returns directory entries with their
        metadata already cached by the OS — one syscall per entry instead of
        a separate ``stat`` per candidate. On a Temp folder with 50 000
        files that is the difference between a scan taking a minute and a
        few seconds.

        Directory links are never descended into: a junction in Temp
        pointing at a profile folder would otherwise enumerate that folder's
        contents as deletable.
        """
        patterns = category.patterns
        stack: list[pathlib.Path] = [root]

        while stack:
            current = stack.pop()
            try:
                with os.scandir(current) as entries:
                    for entry in entries:
                        try:
                            is_link = entry.is_symlink() or _entry_is_junction(entry)
                            if entry.is_dir(follow_symlinks=False):
                                if (
                                    category.recursive
                                    and not is_link
                                    and not _component_is_protected(entry.name)
                                ):
                                    stack.append(pathlib.Path(entry.path))
                                continue
                            if not _matches(entry.name, patterns):
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

    # -- clean -------------------------------------------------------------

    def clean(
        self, report: ScanReport, *, dry_run: bool = False
    ) -> CleanResult:
        """Delete what a scan approved.

        Every item is re-validated against the same gate the scan used. The
        filesystem can change between scan and clean, and a stale inventory
        must never become permission to delete something.
        """
        result = CleanResult()
        now = time.time()

        for category_report in report.categories:
            if not category_report.available:
                continue
            category = category_report.category
            roots = category_report.roots_scanned

            for item in category_report.items:
                owning_root = next(
                    (r for r in roots if self._contained(item.path, r)), None
                )
                if owning_root is None:
                    result.refused_files += 1
                    continue

                approved, reason = self._approves(
                    item.path, owning_root, category, now
                )
                if not approved:
                    # "too new" here means the file was rewritten since the
                    # scan, i.e. something is using it. Correct to refuse.
                    result.refused_files += 1
                    _log.debug("refused %s at delete time: %s", item.path, reason)
                    continue

                if dry_run:
                    result.deleted_files += 1
                    result.deleted_bytes += item.size_bytes
                    continue

                try:
                    # Delete by the object, not the name: the path passed
                    # every check above, but an elevated unlink-by-name could
                    # still be redirected by a junction swapped in just now
                    # (see secure_delete). This opens the object once and
                    # deletes that, so the name can no longer be diverted.
                    result.deleted_bytes += delete_file(item.path)
                    result.deleted_files += 1
                except DeleteError as exc:
                    # The object turned into a reparse point or a directory
                    # after the scan — the signature of a swap attempt, not a
                    # file to delete. Refuse it loudly.
                    result.refused_files += 1
                    _log.warning("refused %s at delete time: %s", item.path, exc)
                except PermissionError:
                    # A locked file is in use. Expected, not an error worth
                    # reporting to the operator individually.
                    result.failed_files += 1
                except OSError as exc:
                    result.failed_files += 1
                    if len(result.errors) < 20:
                        result.errors.append(
                            f"{item.path.name}: {exc.strerror or exc}"
                        )

            if not dry_run and category.remove_empty_dirs:
                self._remove_empty_dirs(roots)

        if not dry_run:
            audit_event(
                "cleanup",
                "clean",
                target=",".join(c.category.id for c in report.categories),
                new_state={
                    "deleted_files": result.deleted_files,
                    "deleted_bytes": result.deleted_bytes,
                    "failed": result.failed_files,
                    "refused": result.refused_files,
                },
                result="SUCCESS" if not result.errors else "PARTIAL",
            )
        return result

    def _remove_empty_dirs(self, roots: list[pathlib.Path]) -> None:
        """Remove directories left empty. The roots themselves are kept."""
        for root in roots:
            for dirpath, dirnames, filenames in os.walk(
                root, topdown=False, followlinks=False
            ):
                current = pathlib.Path(dirpath)
                if current == root or dirnames or filenames:
                    continue
                if self.is_protected(current) or not self._contained(current, root):
                    continue
                try:
                    current.rmdir()
                except OSError:
                    pass  # not empty any more, or locked


def _resolve(path: pathlib.Path) -> pathlib.Path:
    """Resolve once, tolerating a path that cannot be resolved."""
    try:
        return path.resolve(strict=False)
    except (OSError, ValueError, RuntimeError):
        return path


def _matches(name: str, patterns: tuple[str, ...]) -> bool:
    return any(pattern == "*" or fnmatch(name, pattern) for pattern in patterns)


def _entry_is_junction(entry: os.DirEntry) -> bool:
    """Detect an NTFS junction on a scandir entry."""
    try:
        return bool(entry.stat(follow_symlinks=False).st_reparse_tag)
    except (OSError, AttributeError, ValueError):
        return False


def _component_is_protected(component: str) -> bool:
    """Match one path component against the protected-name list.

    A component counts as protected when it equals a fragment, or begins
    with it followed by a separator — so ``OneDrive - Contoso`` and
    ``SmartShell Client`` are caught, while ``my-documents-backup`` is not
    mistaken for ``Documents``.
    """
    lowered = component.lower()
    for fragment in PROTECTED_PATH_FRAGMENTS:
        if lowered == fragment:
            return True
        for separator in (" ", "-", "_", "."):
            if lowered.startswith(fragment + separator):
                return True
    return False


def _is_junction(path: pathlib.Path) -> bool:
    """Detect an NTFS junction, which ``is_symlink()`` does not report."""
    try:
        return bool(os.lstat(path).st_reparse_tag)  # type: ignore[attr-defined]
    except (OSError, AttributeError, ValueError):
        return False
