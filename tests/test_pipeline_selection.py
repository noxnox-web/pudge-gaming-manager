"""Which tweaks an optimization pass offers, and why.

``build_tweaks`` is the selection step: it decides what the operator will be
shown before the engine applies any safety gate. These tests pin the two
changes that were made to it — cleanup is no longer conditional on disk
pressure, and the power-plan tweak is actually reachable.
"""

from __future__ import annotations

import pytest

from pudge_gaming_manager.core.optimization.pipeline import OptimizationPipeline
from pudge_gaming_manager.core.optimization.tweaks.cleanup import (
    CleanTemporaryFilesTweak,
)
from pudge_gaming_manager.core.optimization.tweaks.power import SetPowerPlanTweak
from pudge_gaming_manager.core.optimization.tweaks.windows_old import (
    RemoveWindowsOldTweak,
)
from pudge_gaming_manager.hardware.models import (
    CpuInfo,
    DiskInfo,
    HardwareSnapshot,
    OsInfo,
    RamInfo,
    Reading,
)


def _snapshot(**kwargs) -> HardwareSnapshot:
    base = {
        "captured_at": "2026-09-24T00:00:00+00:00",
        "os": OsInfo(),
        "cpu": CpuInfo(),
        "gpus": (),
        "ram": RamInfo(),
        "disks": (),
        "monitors": (),
    }
    base.update(kwargs)
    return HardwareSnapshot(**base)  # type: ignore[arg-type]


def _roomy_disk() -> DiskInfo:
    """A system disk with plenty of space — the case that used to skip cleanup."""
    return DiskInfo(
        device_id="C:",
        total_gb=Reading(value=1000.0, unit="GB", source="test"),
        free_gb=Reading(value=900.0, unit="GB", source="test"),
        free_percent=Reading(value=90.0, unit="%", source="test"),
        is_system_disk=True,
    )


@pytest.fixture()
def pipeline(tmp_path) -> OptimizationPipeline:
    from pudge_gaming_manager.database.connection import Database

    return OptimizationPipeline(Database(tmp_path / "test.db"))


def _ids(tweaks) -> set[str]:
    return {t.id for t in tweaks}


def test_cleanup_is_offered_even_with_a_nearly_empty_disk(
    pipeline: OptimizationPipeline,
) -> None:
    """The old gate skipped cleanup entirely above 25% free.

    That made shader caches and crash dumps unreachable on any healthy PC,
    which reads to an operator as "the program does not clean anything".
    """
    tweaks = pipeline.build_tweaks(_snapshot(disks=(_roomy_disk(),)))

    assert CleanTemporaryFilesTweak.id in _ids(tweaks)


def test_cleanup_is_offered_with_no_disk_information_at_all(
    pipeline: OptimizationPipeline,
) -> None:
    """A disk the scan could not read is not a reason to skip cleanup."""
    tweaks = pipeline.build_tweaks(_snapshot())

    assert CleanTemporaryFilesTweak.id in _ids(tweaks)


def test_the_power_plan_tweak_is_reachable(
    pipeline: OptimizationPipeline,
) -> None:
    """It was written and tested but never added to a plan, so it never ran."""
    tweaks = pipeline.build_tweaks(_snapshot(disks=(_roomy_disk(),)))

    assert SetPowerPlanTweak.id in _ids(tweaks)


def test_windows_old_removal_stays_in_the_plan(
    pipeline: OptimizationPipeline,
) -> None:
    tweaks = pipeline.build_tweaks(_snapshot(disks=(_roomy_disk(),)))

    assert RemoveWindowsOldTweak.id in _ids(tweaks)


def test_no_tweak_is_offered_twice(pipeline: OptimizationPipeline) -> None:
    """The engine registers tweaks by id; a duplicate would shadow itself."""
    tweaks = pipeline.build_tweaks(_snapshot(disks=(_roomy_disk(),)))

    ids = [t.id for t in tweaks]
    assert len(ids) == len(set(ids))


def test_the_cleanup_tweak_stays_auto_applicable(
    pipeline: OptimizationPipeline,
) -> None:
    """Its risk is the highest of its categories, and the engine gates on that.

    Browser caches joined the defaults; had they been classed MEDIUM the
    whole cleanup tweak would have inherited MEDIUM and been refused by a
    pipeline running with ``allow_risk_above_low=False`` — cleanup would
    have silently stopped working.
    """
    cleanup = next(
        t
        for t in pipeline.build_tweaks(_snapshot())
        if t.id == CleanTemporaryFilesTweak.id
    )

    assert cleanup.risk.auto_applicable
