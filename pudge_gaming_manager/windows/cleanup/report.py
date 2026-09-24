"""Value types the cleanup engine produces: an inventory and a result.

Kept apart from the engine so the shapes the GUI and the tweak render can be
imported without pulling in the deletion machinery, and so ``engine.py``
stays under the module-size limit.
"""

from __future__ import annotations

import pathlib
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

# format_size now lives in utilities so every layer can share it.
from ...utilities.formatting import format_size  # noqa: F401  (re-exported)

if TYPE_CHECKING:
    from .rules import CleanupCategory


@dataclass(frozen=True, slots=True)
class CleanupItem:
    """One file the scan found and approved for deletion."""

    path: pathlib.Path
    size_bytes: int
    modified: float


@dataclass(slots=True)
class CategoryReport:
    """What one category contains."""

    category: CleanupCategory
    items: list[CleanupItem] = field(default_factory=list)
    roots_scanned: list[pathlib.Path] = field(default_factory=list)
    skipped_protected: int = 0
    skipped_too_new: int = 0
    unreadable: int = 0
    available: bool = True
    unavailable_reason: str = ""

    @property
    def total_bytes(self) -> int:
        return sum(i.size_bytes for i in self.items)

    @property
    def file_count(self) -> int:
        return len(self.items)

    @property
    def size_display(self) -> str:
        return format_size(self.total_bytes)


@dataclass(slots=True)
class ScanReport:
    """Everything a cleanup scan found."""

    categories: list[CategoryReport] = field(default_factory=list)
    duration_s: float = 0.0

    @property
    def total_bytes(self) -> int:
        return sum(c.total_bytes for c in self.categories)

    @property
    def total_files(self) -> int:
        return sum(c.file_count for c in self.categories)

    @property
    def size_display(self) -> str:
        return format_size(self.total_bytes)

    def preview_lines(self) -> list[str]:
        lines = [f"{self.size_display} reclaimable in {self.total_files} files"]
        for report in sorted(
            self.categories, key=lambda c: c.total_bytes, reverse=True
        ):
            if not report.available:
                lines.append(
                    f"  - {report.category.name}: {report.unavailable_reason}"
                )
            elif report.file_count:
                lines.append(
                    f"  + [{report.category.risk.value}] {report.category.name}: "
                    f"{report.size_display} in {report.file_count} files"
                )
        return lines


@dataclass(slots=True)
class CleanResult:
    """What a cleanup actually removed."""

    deleted_files: int = 0
    deleted_bytes: int = 0
    failed_files: int = 0
    refused_files: int = 0
    """Items the scan listed but the deletion-time safety re-check rejected."""

    errors: list[str] = field(default_factory=list)

    @property
    def size_display(self) -> str:
        return format_size(self.deleted_bytes)
