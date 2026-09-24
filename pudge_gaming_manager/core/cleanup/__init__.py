"""Core-level facade for disk space: what is using it, and what may go.

The GUI depends on ``core`` and never on ``windows``, so the cleanup value
types are re-exported here alongside :class:`DiskCleaner`, which is the one
object the cleanup button needs.

Why a service rather than two calls from the controller
-------------------------------------------------------
"Clean the disk" covers two unrelated mechanisms: an allowlisted file
sweep (``windows.cleanup.engine``) and the shell's Recycle Bin API
(``windows.cleanup.recycle_bin``). Deciding which of them a given operator
selection runs is domain logic, and domain logic does not belong in a Qt
controller. The controller asks for a plan, shows it, and hands the
approved plan back.

The read-only usage survey lives here too. It is the counterpart to the
cleaner rather than a separate concern: the allowlist exists so the cleaner
can never delete something nobody listed, and the price of that discipline
is that it cannot say anything about the folder no category names. The
survey answers that and leaves the decision to a person.
"""

from __future__ import annotations

from .service import (
    DiskCleaner,
    DiskCleanupPlan,
    DiskCleanupResult,
    RECYCLE_BIN_ID,
)
from ...windows.cleanup.categories import CATEGORIES, default_categories
from ...windows.cleanup.report import CategoryReport, CleanResult, ScanReport
from ...windows.cleanup.recycle_bin import RecycleBinState
from ...windows.cleanup.rules import CleanupCategory, CleanupRisk
from ...windows.usage import FolderUsage, UsageReport, survey

__all__ = [
    "CATEGORIES",
    "CategoryReport",
    "CleanResult",
    "CleanupCategory",
    "CleanupRisk",
    "DiskCleaner",
    "DiskCleanupPlan",
    "DiskCleanupResult",
    "RECYCLE_BIN_ID",
    "RecycleBinState",
    "FolderUsage",
    "ScanReport",
    "UsageReport",
    "default_categories",
    "survey",
]
