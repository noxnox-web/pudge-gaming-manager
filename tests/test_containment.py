"""The containment predicate, which decides whether a path may be deleted.

``within`` replaced ``pathlib.Path.is_relative_to`` because that method was
the single largest cost in a scan. A string-prefix containment check invites
one specific bug — ``C:\\Temp`` appearing to contain ``C:\\Temp2`` — so that
case is pinned first and hardest. Everything else here exists because the
filesystem treats these paths as equal and so must the predicate.
"""

from __future__ import annotations

import os
import pathlib

import pytest

from pudge_gaming_manager.windows.cleanup.engine import CleanupEngine
from pudge_gaming_manager.windows.cleanup.rules import within

windows_only = pytest.mark.skipif(os.name != "nt", reason="Windows path semantics")


def P(text: str) -> pathlib.Path:
    return pathlib.Path(text)


# -- the prefix trap --------------------------------------------------------


@windows_only
@pytest.mark.parametrize(
    "candidate",
    [
        r"C:\Temp2\file.tmp",
        r"C:\Temporary\file.tmp",
        r"C:\TempFiles",
        r"C:\Temp2",
    ],
)
def test_a_sibling_with_a_shared_prefix_is_not_contained(candidate: str) -> None:
    """The bug a naive startswith would introduce, and the reason for the sep."""
    assert not within(P(candidate), P(r"C:\Temp"))


@windows_only
def test_a_real_child_is_contained() -> None:
    assert within(P(r"C:\Temp\sub\file.tmp"), P(r"C:\Temp"))


@windows_only
def test_the_root_itself_is_contained() -> None:
    """The cleaner never deletes a root, but containment of it must be true.

    ``_remove_empty_dirs`` asks this question about directories that may be
    the root, and a false answer there would skip the check that follows.
    """
    assert within(P(r"C:\Temp"), P(r"C:\Temp"))


@windows_only
def test_a_parent_is_not_contained_in_its_child() -> None:
    assert not within(P(r"C:\Temp"), P(r"C:\Temp\sub"))


# -- what the filesystem considers the same path ----------------------------


@windows_only
def test_case_is_ignored_as_windows_ignores_it() -> None:
    assert within(P(r"c:\temp\SUB\File.TMP"), P(r"C:\TEMP"))


@windows_only
def test_forward_slashes_are_the_same_path() -> None:
    assert within(P("C:/Temp/sub/file.tmp"), P(r"C:\Temp"))
    assert within(P(r"C:\Temp\sub\file.tmp"), P("C:/Temp"))


@windows_only
def test_a_drive_root_contains_everything_on_that_drive() -> None:
    """A root that already ends in a separator must not grow a second one."""
    assert within(P(r"C:\Windows\System32"), P("C:\\"))


@windows_only
def test_a_different_drive_is_never_contained() -> None:
    assert not within(P(r"D:\Temp\file.tmp"), P(r"C:\Temp"))


@windows_only
def test_a_trailing_separator_on_the_root_changes_nothing() -> None:
    assert within(P(r"C:\Temp\file.tmp"), P("C:\\Temp\\"))
    assert not within(P(r"C:\Temp2\file.tmp"), P("C:\\Temp\\"))


# -- agreement with what it replaced ----------------------------------------


@windows_only
@pytest.mark.parametrize(
    ("candidate", "root"),
    [
        (r"C:\Temp\a\b.txt", r"C:\Temp"),
        (r"C:\Temp", r"C:\Temp"),
        (r"C:\Temp2\a.txt", r"C:\Temp"),
        (r"D:\a", r"C:\a"),
        (r"C:\Windows\System32\x.dll", "C:\\"),
        (r"C:\Users\x\AppData\Local", r"C:\Users\x"),
        (r"C:\Users\xx", r"C:\Users\x"),
    ],
)
def test_it_agrees_with_pathlib_on_the_cases_pathlib_gets_right(
    candidate: str, root: str
) -> None:
    """Same verdicts as ``is_relative_to``, 25x faster — not different rules."""
    assert within(P(candidate), P(root)) == P(candidate).is_relative_to(P(root))


# -- the guard that uses it -------------------------------------------------


@windows_only
def test_protected_roots_still_refuse_their_children() -> None:
    """is_protected walks the protected roots through this predicate."""
    engine = CleanupEngine(())
    system32 = pathlib.Path(os.path.expandvars(r"%SystemRoot%\System32"))

    assert engine.is_protected(system32 / "kernel32.dll")
    assert engine.is_protected(system32 / "drivers" / "etc" / "hosts")


@windows_only
def test_a_lookalike_of_a_protected_root_is_not_swept_in() -> None:
    """``System32Backup`` is not inside ``System32``."""
    engine = CleanupEngine(())
    root = pathlib.Path(os.path.expandvars("%SystemRoot%"))

    lookalike = root / "System32Backup" / "notes.txt"
    assert not engine.is_protected(lookalike)
