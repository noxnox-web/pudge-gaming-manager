"""Tests for the additions the cleanup button needed.

Three things are new and each one relaxes or extends a guard, so each one
is pinned here: wildcard roots (browser profiles), the user-files waiver
(the Downloads folder), and the composed plan the dialog is built from.
"""

from __future__ import annotations

import os
import pathlib
import time

import pytest

from pudge_gaming_manager.core.cleanup import RECYCLE_BIN_ID, DiskCleaner
from pudge_gaming_manager.core.cleanup.service import DiskCleanupPlan
from pudge_gaming_manager.windows.cleanup import recycle_bin
from pudge_gaming_manager.windows.cleanup.categories import CATEGORIES
from pudge_gaming_manager.windows.cleanup.engine import CleanupEngine
from pudge_gaming_manager.windows.cleanup.recycle_bin import RecycleBinState
from pudge_gaming_manager.windows.cleanup.rules import (
    MIN_FIXED_COMPONENTS,
    CleanupCategory,
    CleanupRisk,
    resolve_roots,
)

windows_only = pytest.mark.skipif(os.name != "nt", reason="Windows-only behaviour")

_OLD = 1_000_000.0  # comfortably older than any min_age_hours in use


def _write(path: pathlib.Path, *, size: int = 16) -> pathlib.Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    old = time.time() - _OLD
    os.utime(path, (old, old))
    return path


def _category(root_template: str, **kwargs) -> CleanupCategory:
    defaults = dict(
        id="test.category",
        name="Test category",
        description="Test",
        rationale="Test",
        roots=(root_template,),
        min_age_hours=0.0,
    )
    defaults.update(kwargs)
    return CleanupCategory(**defaults)  # type: ignore[arg-type]


# -- wildcard roots ---------------------------------------------------------


def test_wildcard_root_expands_to_every_matching_profile(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One entry covers "Default", "Profile 1", "Profile 2" and the rest."""
    base = tmp_path / "vendor" / "browser" / "User Data"
    for profile in ("Default", "Profile 1", "Profile 7"):
        (base / profile / "Cache").mkdir(parents=True)
    monkeypatch.setenv("PGM_TEST_ROOT", str(tmp_path))

    category = _category("%PGM_TEST_ROOT%\\vendor\\browser\\User Data\\*\\Cache")
    roots = resolve_roots(category)

    assert {r.parent.name for r in roots} == {"Default", "Profile 1", "Profile 7"}


def test_wildcard_root_ignores_files_and_missing_paths(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only directories are roots; a matching file is not one."""
    base = tmp_path / "vendor" / "browser"
    base.mkdir(parents=True)
    (base / "NotAProfile").write_text("file, not a directory")
    monkeypatch.setenv("PGM_TEST_ROOT", str(tmp_path))

    assert resolve_roots(_category("%PGM_TEST_ROOT%\\vendor\\browser\\*")) == []


def test_wildcard_too_close_to_the_drive_root_is_refused() -> None:
    """A pattern must name a vendor and a product before guessing a profile.

    Without this, a typo'd template like ``%LOCALAPPDATA%\\*`` would hand
    the cleaner every directory in the profile at once.
    """
    shallow = "\\".join(["C:"] + ["*"])
    assert resolve_roots(_category(shallow)) == []
    assert MIN_FIXED_COMPONENTS >= 3


def test_shipped_wildcard_roots_all_clear_the_depth_guard() -> None:
    """Every browser template in the catalogue is deep enough to resolve."""
    for category in CATEGORIES:
        for template in category.roots:
            if "*" not in template and "?" not in template:
                continue
            expanded = pathlib.Path(os.path.expandvars(template))
            depth = next(
                i for i, part in enumerate(expanded.parts) if "*" in part or "?" in part
            )
            assert depth >= MIN_FIXED_COMPONENTS, template


# -- the user-files waiver --------------------------------------------------


def test_documents_are_protected_in_an_ordinary_category(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(tmp_path / "report.pdf")
    _write(tmp_path / "setup.exe")
    _write(tmp_path / "scratch.tmp")
    monkeypatch.setenv("PGM_TEST_ROOT", str(tmp_path))

    report = CleanupEngine((_category("%PGM_TEST_ROOT%"),)).scan()

    found = {item.path.name for item in report.categories[0].items}
    assert found == {"scratch.tmp"}
    assert report.categories[0].skipped_protected == 2


def test_downloads_style_category_may_remove_documents_and_installers(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole point of clearing Downloads is the installers in it."""
    _write(tmp_path / "report.pdf")
    _write(tmp_path / "setup.exe")
    _write(tmp_path / "archive.zip")
    monkeypatch.setenv("PGM_TEST_ROOT", str(tmp_path))

    category = _category("%PGM_TEST_ROOT%", clears_user_files=True)
    report = CleanupEngine((category,)).scan()

    found = {item.path.name for item in report.categories[0].items}
    assert found == {"report.pdf", "setup.exe", "archive.zip"}


def test_credentials_survive_even_the_user_files_waiver(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A downloaded certificate or password database is never deleted."""
    for name in ("id.pem", "vault.kdbx", "work.ovpn", "client.pfx"):
        _write(tmp_path / name)
    _write(tmp_path / "setup.exe")
    monkeypatch.setenv("PGM_TEST_ROOT", str(tmp_path))

    category = _category("%PGM_TEST_ROOT%", clears_user_files=True)
    report = CleanupEngine((category,)).scan()

    found = {item.path.name for item in report.categories[0].items}
    assert found == {"setup.exe"}
    assert report.categories[0].skipped_protected == 4


def test_the_waiver_does_not_relax_the_path_guard(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Relaxing the extension layer must not open the protected folders."""
    _write(tmp_path / "Documents" / "thesis.txt")
    _write(tmp_path / "loose.txt")
    monkeypatch.setenv("PGM_TEST_ROOT", str(tmp_path))

    category = _category("%PGM_TEST_ROOT%", clears_user_files=True)
    report = CleanupEngine((category,)).scan()

    found = {item.path.name for item in report.categories[0].items}
    assert found == {"loose.txt"}


def test_only_downloads_waives_the_extension_guard() -> None:
    """No shipped category acquires the waiver by accident."""
    waived = {c.id for c in CATEGORIES if c.clears_user_files}
    assert waived == {"files.downloads"}


# -- the composed plan ------------------------------------------------------


def _plan_with(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> DiskCleaner:
    _write(tmp_path / "junk.tmp", size=1024)
    monkeypatch.setenv("PGM_TEST_ROOT", str(tmp_path))
    return DiskCleaner((_category("%PGM_TEST_ROOT%"),))


def test_plan_reports_files_and_the_recycle_bin_together(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cleaner = _plan_with(tmp_path, monkeypatch)
    monkeypatch.setattr(
        recycle_bin, "query", lambda: RecycleBinState(size_bytes=2048, item_count=3)
    )

    plan = cleaner.plan()

    assert plan.scan.total_bytes == 1024
    assert plan.total_bytes == 1024 + 2048
    assert RECYCLE_BIN_ID in plan.default_selection()


def test_an_unticked_category_is_not_touched(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The selection is the permission; anything outside it stays."""
    cleaner = _plan_with(tmp_path, monkeypatch)
    monkeypatch.setattr(recycle_bin, "query", lambda: RecycleBinState())

    plan = cleaner.plan()
    result = cleaner.run(plan, selected=set())

    assert result.files.deleted_files == 0
    assert (tmp_path / "junk.tmp").exists()


def test_a_ticked_category_is_cleaned(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cleaner = _plan_with(tmp_path, monkeypatch)
    monkeypatch.setattr(recycle_bin, "query", lambda: RecycleBinState())

    plan = cleaner.plan()
    result = cleaner.run(plan, selected={"test.category"})

    assert result.files.deleted_files == 1
    assert not (tmp_path / "junk.tmp").exists()


def test_the_recycle_bin_is_only_emptied_when_it_was_ticked(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cleaner = _plan_with(tmp_path, monkeypatch)
    monkeypatch.setattr(
        recycle_bin, "query", lambda: RecycleBinState(size_bytes=2048, item_count=3)
    )
    calls: list[int] = []
    monkeypatch.setattr(
        recycle_bin, "empty", lambda: (calls.append(1), (True, "ok"))[1]
    )

    plan = cleaner.plan()

    cleaner.run(plan, selected={"test.category"})
    assert calls == []

    cleaner.run(plan, selected={RECYCLE_BIN_ID})
    assert calls == [1]


def test_dry_run_deletes_nothing(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cleaner = _plan_with(tmp_path, monkeypatch)
    monkeypatch.setattr(
        recycle_bin, "query", lambda: RecycleBinState(size_bytes=2048, item_count=3)
    )
    monkeypatch.setattr(
        recycle_bin,
        "empty",
        lambda: pytest.fail("dry run must not empty the recycle bin"),
    )

    plan = cleaner.plan()
    result = cleaner.run(plan, {"test.category", RECYCLE_BIN_ID}, dry_run=True)

    assert (tmp_path / "junk.tmp").exists()
    assert result.files.deleted_files == 1  # counted, not deleted


def test_user_file_categories_start_unticked(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The dialog opens with the Downloads-style rows off."""
    _write(tmp_path / "installer.exe")
    monkeypatch.setenv("PGM_TEST_ROOT", str(tmp_path))
    category = _category(
        "%PGM_TEST_ROOT%",
        clears_user_files=True,
        enabled_by_default=False,
        risk=CleanupRisk.MEDIUM,
    )
    monkeypatch.setattr(recycle_bin, "query", lambda: RecycleBinState())

    plan = DiskCleaner((category,)).plan()

    assert plan.scan.total_files == 1
    assert plan.default_selection() == set()


# -- the recycle bin binding ------------------------------------------------


@windows_only
def test_querying_the_recycle_bin_does_not_raise() -> None:
    """The shell call is wired correctly: it answers rather than crashing."""
    state = recycle_bin.query()

    assert state.available or state.unavailable_reason
    assert state.size_bytes >= 0
    assert state.item_count >= 0


def test_emptying_an_empty_bin_counts_as_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reporting a failure for an already-empty bin would be false."""
    monkeypatch.setattr(recycle_bin, "query", lambda: RecycleBinState())

    ok, detail = recycle_bin.empty()

    assert ok
    assert "пуст" in detail


def test_an_unreachable_bin_is_reported_not_swallowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        recycle_bin,
        "query",
        lambda: RecycleBinState(available=False, unavailable_reason="нет shell32"),
    )

    ok, detail = recycle_bin.empty()

    assert not ok
    assert "нет shell32" in detail


# -- reporting what was really reclaimed ------------------------------------


def test_a_real_run_measures_free_space_before_and_after(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cleaner = _plan_with(tmp_path, monkeypatch)
    monkeypatch.setattr(recycle_bin, "query", lambda: RecycleBinState())

    plan = cleaner.plan()
    result = cleaner.run(plan, selected={"test.category"})

    assert result.space.measured
    assert result.reclaimed_bytes is not None


def test_a_dry_run_measures_nothing_and_says_so(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """There is no "after" to compare against, so there is no figure."""
    cleaner = _plan_with(tmp_path, monkeypatch)
    monkeypatch.setattr(recycle_bin, "query", lambda: RecycleBinState())

    plan = cleaner.plan()
    result = cleaner.run(plan, selected={"test.category"}, dry_run=True)

    assert result.reclaimed_bytes is None
    assert result.reclaimed_display == "не измерено"


def test_a_large_shortfall_is_explained_not_hidden() -> None:
    """Shadow copies are the usual cause and the operator deserves the reason."""
    from pudge_gaming_manager.core.cleanup.service import DiskCleanupResult
    from pudge_gaming_manager.utilities.disk_space import FreeSpaceDelta
    from pudge_gaming_manager.windows.cleanup.report import CleanResult

    result = DiskCleanupResult(
        files=CleanResult(deleted_files=10, deleted_bytes=10_000_000_000),
        space=FreeSpaceDelta(before={"C:\\": 0}, after={"C:\\": 1_000_000_000}),
    )

    assert result.reclaimed_bytes == 1_000_000_000
    assert "теневые копии" in result.space_note


def test_matching_numbers_produce_no_note() -> None:
    """An explanation nobody needs is noise that buries the real warnings."""
    from pudge_gaming_manager.core.cleanup.service import DiskCleanupResult
    from pudge_gaming_manager.utilities.disk_space import FreeSpaceDelta
    from pudge_gaming_manager.windows.cleanup.report import CleanResult

    result = DiskCleanupResult(
        files=CleanResult(deleted_files=10, deleted_bytes=1_000_000_000),
        space=FreeSpaceDelta(before={"C:\\": 0}, after={"C:\\": 1_000_000_000}),
    )

    assert result.space_note == ""


def test_unmeasured_space_says_so_rather_than_claiming_the_logical_total() -> None:
    from pudge_gaming_manager.core.cleanup.service import DiskCleanupResult
    from pudge_gaming_manager.windows.cleanup.report import CleanResult

    result = DiskCleanupResult(
        files=CleanResult(deleted_files=1, deleted_bytes=500)
    )

    assert result.reclaimed_bytes is None
    assert "измерить не удалось" in result.space_note
