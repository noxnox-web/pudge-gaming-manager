"""Measuring free space, so a cleanup can report what it actually freed.

Why this is not the same as adding up deleted files
---------------------------------------------------
The two numbers legitimately differ, and reporting only the first would
overstate the result:

* A file inside a Volume Shadow Copy or a restore point is still referenced
  after it is unlinked, so its bytes do not come back until the snapshot
  expires.
* Deduplicated and compressed volumes free less than the logical size.
* NTFS allocates in clusters, so many small files free slightly more than
  their logical total.
* Anything else running on the machine writes during the cleanup.

Rule #40 says only measurements actually taken are reported. So the cleanup
takes both: the logical total of what it deleted, and a real before/after
reading of every drive it touched.
"""

from __future__ import annotations

import pathlib
import shutil
from dataclasses import dataclass, field


def free_bytes(path: pathlib.Path | str) -> int | None:
    """Free space on the volume holding ``path``, or ``None`` if unreadable.

    ``None`` rather than ``0``: a volume that cannot be queried is unknown,
    and reporting unknown as zero would turn a missing reading into a claim.
    """
    try:
        return shutil.disk_usage(str(path)).free
    except (OSError, ValueError):
        return None


def drive_of(path: pathlib.Path) -> str:
    """The volume root for a path, e.g. ``'C:\\\\'``. Empty when it has none."""
    return pathlib.Path(path).anchor


@dataclass(slots=True)
class FreeSpaceDelta:
    """Free space on each drive, measured before and after an operation."""

    before: dict[str, int] = field(default_factory=dict)
    after: dict[str, int] = field(default_factory=dict)

    @property
    def measured(self) -> bool:
        """True when at least one drive was read both before and after."""
        return bool(self.before.keys() & self.after.keys())

    @property
    def freed_bytes(self) -> int:
        """Total space that actually came back, across every drive measured.

        A drive that lost space during the operation contributes zero rather
        than a negative number: something else wrote to it, which is not the
        cleanup's doing and not a reason to understate the rest.
        """
        total = 0
        for drive, before in self.before.items():
            after = self.after.get(drive)
            if after is None:
                continue
            total += max(after - before, 0)
        return total

    def measure_before(self, drives: set[str]) -> None:
        self.before = self._read(drives)

    def measure_after(self) -> None:
        self.after = self._read(set(self.before))

    @staticmethod
    def _read(drives: set[str]) -> dict[str, int]:
        readings: dict[str, int] = {}
        for drive in drives:
            value = free_bytes(drive)
            if value is not None:
                readings[drive] = value
        return readings


__all__ = ["FreeSpaceDelta", "drive_of", "free_bytes"]
