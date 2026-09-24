"""The read-first tools: the usage survey, the startup manager, free space.

Everything here either reads the machine or toggles one reversible flag, so
the tests are about honesty of reporting rather than about destruction:
does a partial answer say it is partial, does an unreadable folder get
counted, does a toggle believe the registry rather than the click.
"""

from __future__ import annotations

import os
import pathlib

import pytest

from pudge_gaming_manager.utilities.disk_space import (
    FreeSpaceDelta,
    drive_of,
    free_bytes,
)
from pudge_gaming_manager.windows import usage
from pudge_gaming_manager.windows.startup import manager as startup

windows_only = pytest.mark.skipif(os.name != "nt", reason="Windows-only behaviour")


def _tree(root: pathlib.Path, layout: dict[str, int]) -> None:
    """Create ``relative/path -> size`` under ``root``."""
    for rel, size in layout.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x" * size)


# -- the usage survey -------------------------------------------------------


def test_survey_reports_folders_at_the_requested_depth(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _tree(
        tmp_path,
        {
            "a/one/big.bin": 4096,
            "a/two/small.bin": 128,
            "b/one/mid.bin": 1024,
        },
    )
    monkeypatch.setenv("PGM_TEST_ROOT", str(tmp_path))

    report = usage.survey(("%PGM_TEST_ROOT%",), depth=2, time_budget_s=30)

    by_path = {f.path.relative_to(tmp_path).as_posix(): f.size_bytes for f in report.folders}
    assert by_path == {"a/one": 4096, "a/two": 128, "b/one": 1024}


def test_reported_folders_never_contain_one_another(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A fixed depth is what keeps the sizes from double-counting."""
    _tree(tmp_path, {"a/b/c/d/deep.bin": 512, "a/b/other.bin": 256})
    monkeypatch.setenv("PGM_TEST_ROOT", str(tmp_path))

    report = usage.survey(("%PGM_TEST_ROOT%",), depth=2, time_budget_s=30)

    paths = [f.path for f in report.folders]
    for outer in paths:
        for inner in paths:
            if outer != inner:
                assert not inner.is_relative_to(outer)


def test_a_branch_shallower_than_the_depth_is_still_reported(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A flat 40 GB folder must not vanish because it has no subfolders."""
    _tree(tmp_path, {"flat/huge.bin": 8192, "deep/a/b/file.bin": 64})
    monkeypatch.setenv("PGM_TEST_ROOT", str(tmp_path))

    report = usage.survey(("%PGM_TEST_ROOT%",), depth=3, time_budget_s=30)

    names = {f.path.name for f in report.folders}
    assert "flat" in names


def test_survey_is_sorted_largest_first_and_honours_the_limit(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _tree(tmp_path, {f"d{i}/f.bin": (i + 1) * 100 for i in range(6)})
    monkeypatch.setenv("PGM_TEST_ROOT", str(tmp_path))

    report = usage.survey(("%PGM_TEST_ROOT%",), depth=1, limit=3, time_budget_s=30)

    sizes = [f.size_bytes for f in report.folders]
    assert sizes == sorted(sizes, reverse=True)
    assert len(report.folders) == 3


def test_an_exhausted_budget_marks_the_report_incomplete(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A partial answer that claims to be complete is the dangerous one."""
    _tree(tmp_path, {f"d{i}/f.bin": 64 for i in range(40)})
    monkeypatch.setenv("PGM_TEST_ROOT", str(tmp_path))

    report = usage.survey(("%PGM_TEST_ROOT%",), depth=1, time_budget_s=-1.0)

    assert not report.complete


def test_a_missing_root_is_skipped_not_fatal(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PGM_TEST_ROOT", str(tmp_path / "nope"))

    report = usage.survey(("%PGM_TEST_ROOT%", "%PGM_UNSET_VAR%"), time_budget_s=5)

    assert report.roots_scanned == []
    assert report.folders == []


@windows_only
def test_the_survey_does_not_follow_a_junction(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Otherwise another drive's contents are counted as if they lived here."""
    real = tmp_path / "real"
    _tree(real, {"payload.bin": 4096})
    inside = tmp_path / "root" / "link"
    inside.parent.mkdir(parents=True)
    os.system(f'mklink /J "{inside}" "{real}" >nul 2>&1')
    if not inside.exists():
        pytest.skip("could not create a junction here")
    monkeypatch.setenv("PGM_TEST_ROOT", str(tmp_path / "root"))

    report = usage.survey(("%PGM_TEST_ROOT%",), depth=1, time_budget_s=30)

    assert report.total_bytes == 0


# -- free space -------------------------------------------------------------


def test_free_space_of_a_real_path_is_read() -> None:
    assert (free_bytes(pathlib.Path.home()) or 0) > 0


def test_free_space_of_a_nonsense_path_is_unknown_not_zero() -> None:
    """Unknown reported as zero would turn a missing reading into a claim."""
    assert free_bytes(pathlib.Path("Q:/definitely/not/here")) is None


def test_a_delta_with_no_readings_is_not_measured() -> None:
    assert not FreeSpaceDelta().measured


def test_freed_bytes_sums_only_the_drives_read_twice() -> None:
    delta = FreeSpaceDelta(
        before={"C:\\": 100, "D:\\": 500}, after={"C:\\": 350}
    )

    assert delta.measured
    assert delta.freed_bytes == 250


def test_a_drive_that_lost_space_does_not_go_negative() -> None:
    """Something else wrote during the cleanup; that is not a debit here."""
    delta = FreeSpaceDelta(
        before={"C:\\": 100, "D:\\": 100}, after={"C:\\": 400, "D:\\": 40}
    )

    assert delta.freed_bytes == 300


@windows_only
def test_drive_of_returns_the_volume_root() -> None:
    assert drive_of(pathlib.Path(r"C:\Users\someone\file.txt")) == "C:\\"


# -- the startup manager ----------------------------------------------------


def test_an_absent_approval_flag_means_enabled() -> None:
    """Guessing "disabled" would show an entry as off while it still runs."""
    assert startup._approval_is_enabled(None)
    assert startup._approval_is_enabled(b"")


@pytest.mark.parametrize(
    ("first_byte", "expected"),
    [(2, True), (3, False), (6, True), (7, False)],
)
def test_the_approval_flag_is_read_from_bit_zero(
    first_byte: int, expected: bool
) -> None:
    """Windows writes 02/03 and also 06/07, so the bit is what matters."""
    data = bytes([first_byte]) + bytes(11)

    assert startup._approval_is_enabled(data) is expected


def test_the_payload_round_trips_through_the_reader() -> None:
    assert startup._approval_is_enabled(startup._approval_payload(True))
    assert not startup._approval_is_enabled(startup._approval_payload(False))


def test_the_payload_is_the_length_windows_writes() -> None:
    for enabled in (True, False):
        assert len(startup._approval_payload(enabled)) == startup._APPROVAL_SIZE


def test_disabling_records_when_it_happened() -> None:
    """Task Manager shows that timestamp; an all-zero one would read as 1601."""
    payload = startup._approval_payload(False)

    assert payload[4:] != bytes(8)


def test_enabling_leaves_the_timestamp_empty() -> None:
    assert startup._approval_payload(True)[4:] == bytes(8)


def test_every_source_maps_to_an_approval_key_and_hive() -> None:
    for source in startup.StartupSource:
        assert source.approval_subkey.endswith(
            ("\\Run", "\\Run32", "\\StartupFolder")
        )
        assert source.label
        assert source.hive.name in {"HKCU", "HKLM"}


def test_machine_wide_sources_are_the_ones_needing_admin() -> None:
    needs = {s for s in startup.StartupSource if s.requires_admin}

    assert needs == {
        startup.StartupSource.RUN_MACHINE,
        startup.StartupSource.RUN_MACHINE_32,
        startup.StartupSource.FOLDER_COMMON,
    }


def test_the_approval_key_is_inside_the_registry_allowlist() -> None:
    """The toggle must go through RegistryManager, not around it.

    StartupApproved sits under the already-allowlisted Explorer key. If
    that ever stopped being true the write would be refused at runtime,
    which is a worse place to find out than here.
    """
    from pudge_gaming_manager.windows.registry.manager import is_allowed_key

    for source in startup.StartupSource:
        assert is_allowed_key(source.hive, source.approval_subkey), source


def test_run_keys_themselves_stay_outside_the_allowlist() -> None:
    """Disabling never writes to a Run key, and must stay unable to."""
    from pudge_gaming_manager.windows.registry.manager import Hive, is_allowed_key

    run = r"Software\Microsoft\Windows\CurrentVersion\Run"
    assert not is_allowed_key(Hive.HKCU, run)
    assert not is_allowed_key(Hive.HKLM, run)


@windows_only
def test_enumerating_startup_entries_does_not_raise() -> None:
    entries = startup.StartupManager().entries()

    for entry in entries:
        assert entry.name
        assert entry.key.startswith(entry.source.value)


@windows_only
def test_disabled_entries_sort_after_enabled_ones() -> None:
    entries = startup.StartupManager().entries()
    flags = [e.enabled for e in entries]

    assert flags == sorted(flags, reverse=True)
