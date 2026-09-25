"""Removing applications — the one thing this program cannot undo.

Every other tweak is defended by its rollback. These are defended only by
never removing something that was not named, so that is what most of this
file checks: the catalogue, and the two guards standing behind it.
"""

from __future__ import annotations

import os
import pathlib
from typing import Any

import pytest

from pudge_gaming_manager.core.optimization.tweak import (
    BackupScope,
    Outcome,
    RiskLevel,
    TweakContext,
)
from pudge_gaming_manager.core.optimization.tweaks.apps import (
    RemoveOneDriveTweak,
    RemoveTeamsConsumerTweak,
    RemoveUnusedAppsTweak,
    RemoveWidgetsTweak,
    RemoveXboxAppsTweak,
    app_tweaks,
)
from pudge_gaming_manager.windows.apps import catalogue, inventory, onedrive

windows_only = pytest.mark.skipif(os.name != "nt", reason="Windows-only behaviour")

APP_TWEAKS = [
    RemoveXboxAppsTweak,
    RemoveWidgetsTweak,
    RemoveTeamsConsumerTweak,
    RemoveUnusedAppsTweak,
]


class FakePowerShell:
    """Answers the inventory query with whatever the test declares."""

    def __init__(self, packages: list[tuple[str, str]]) -> None:
        self.packages = packages
        self.removed: list[str] = []

    def run_json(self, *_a: Any, **_k: Any) -> list[dict[str, str]]:
        return [
            {"Name": name, "Full": f"{name}_1.0_x64", "Signature": signature}
            for name, signature in self.packages
        ]

    def try_run_json(self, *_a: Any, **_k: Any) -> list[dict[str, str]]:
        return []

    def run(self, _script: str, parameters: dict | None = None, **_k: Any) -> Any:
        self.removed.append((parameters or {}).get("Name", ""))
        return None


def _inventory(packages: list[tuple[str, str]]) -> inventory.AppInventory:
    return inventory.AppInventory(powershell=FakePowerShell(packages))  # type: ignore[arg-type]


# -- the exclusion that matters most ----------------------------------------


def test_the_xbox_sign_in_broker_is_never_removed() -> None:
    """Removing it breaks Game Pass, Minecraft, Forza and Xbox-Live Steam games.

    This is the single most damaging thing a "gaming optimiser" can do to a
    gaming PC, and it is exactly what a pattern match on "Xbox" would do.
    """
    assert "Microsoft.XboxIdentityProvider" not in catalogue.XBOX.packages

    excluded = {package for package, _why in catalogue.XBOX.excluded}
    assert "Microsoft.XboxIdentityProvider" in excluded


def test_it_stays_excluded_even_if_windows_reports_it_installed() -> None:
    """The catalogue is the gate, not the inventory."""
    source = _inventory(
        [
            ("Microsoft.XboxIdentityProvider", "Store"),
            ("Microsoft.XboxGamingOverlay", "Store"),
        ]
    )
    tweak = RemoveXboxAppsTweak(source)

    state = tweak.scan(TweakContext())

    assert "XboxGamingOverlay" in state.summary
    assert "IdentityProvider" not in state.summary


def test_every_exclusion_carries_its_reason() -> None:
    """A name left out without a reason reads as an oversight."""
    for group in catalogue.GROUPS:
        for package, why in group.excluded:
            assert package, group.id
            assert len(why) > 20, f"{group.id}/{package} has no real reason"


# -- the system-component guard ---------------------------------------------


def test_a_system_signed_package_is_refused() -> None:
    """Shell components are signed System; removing one breaks the desktop."""
    source = _inventory([("Microsoft.XboxGamingOverlay", "System")])

    assert source.find(catalogue.XBOX.packages) == []


def test_a_store_signed_package_is_offered() -> None:
    source = _inventory([("Microsoft.XboxGamingOverlay", "Store")])

    found = source.find(catalogue.XBOX.packages)

    assert [app.name for app in found] == ["Microsoft.XboxGamingOverlay"]


def test_the_guard_is_repeated_at_the_point_of_removal() -> None:
    """A guard that exists on only one path is a guard with a way around it."""
    system_app = inventory.InstalledApp(
        name="Microsoft.Windows.ShellExperienceHost",
        full_name="x",
        signature="System",
    )
    powershell = FakePowerShell([])

    ok, detail = inventory.remove(system_app, powershell)  # type: ignore[arg-type]

    assert not ok
    assert "компонент Windows" in detail
    assert powershell.removed == []


def test_no_catalogue_entry_names_a_package_another_entry_protects() -> None:
    """A group must not remove what a sibling group deliberately keeps."""
    protected = catalogue.all_excluded()
    for group in catalogue.GROUPS:
        for package in group.packages:
            assert package.lower() not in protected, f"{group.id}: {package}"


def test_the_store_itself_is_never_removed() -> None:
    """Without it, nothing removed here can be put back."""
    for group in catalogue.GROUPS:
        assert "Microsoft.WindowsStore" not in group.packages


# -- nothing is removed by pattern ------------------------------------------


def test_an_unlisted_package_is_left_alone_however_it_is_named() -> None:
    """There is no "remove everything matching Xbox" rule, on purpose."""
    source = _inventory(
        [
            ("Microsoft.XboxSomethingBrandNew", "Store"),
            ("Contoso.XboxCompanion", "Store"),
        ]
    )

    assert source.find(catalogue.XBOX.packages) == []


def test_a_group_with_nothing_installed_needs_no_change() -> None:
    tweak = RemoveWidgetsTweak(_inventory([("Microsoft.WindowsStore", "Store")]))

    state = tweak.scan(TweakContext())

    assert not state.needs_change
    assert "нечего удалять" in state.summary


# -- honesty about irreversibility ------------------------------------------


@pytest.mark.parametrize("cls", APP_TWEAKS + [RemoveOneDriveTweak], ids=lambda c: c.__name__)
def test_removal_declares_itself_irreversible(cls) -> None:
    """BackupScope.NONE is what makes the preview print НЕОБРАТИМО."""
    assert cls.scope is BackupScope.NONE


@pytest.mark.parametrize("cls", APP_TWEAKS + [RemoveOneDriveTweak], ids=lambda c: c.__name__)
def test_removal_is_medium_so_it_needs_the_opt_in(cls) -> None:
    """Never applied because nobody unticked it."""
    assert cls.risk is RiskLevel.MEDIUM
    assert not cls.risk.auto_applicable


def test_rollback_says_plainly_that_it_cannot_restore() -> None:
    tweak = RemoveXboxAppsTweak(_inventory([]))

    result = tweak.rollback(TweakContext(), None)  # type: ignore[arg-type]

    assert result.outcome is Outcome.FAILED
    assert "Магазина" in result.detail


def test_backup_refuses_rather_than_recording_something_useless() -> None:
    """A record that cannot restore would be worse than admitting there is none."""
    tweak = RemoveXboxAppsTweak(_inventory([]))

    with pytest.raises(NotImplementedError):
        tweak.backup(TweakContext(), None)  # type: ignore[arg-type]


@pytest.mark.parametrize("cls", APP_TWEAKS + [RemoveOneDriveTweak], ids=lambda c: c.__name__)
def test_every_removal_explains_itself(cls) -> None:
    assert cls.id and cls.name and cls.description
    assert len(cls.rationale) > 60


# -- what the tweaks do with an inventory -----------------------------------


def test_an_unreadable_inventory_blocks_the_change() -> None:
    """Not knowing what would be removed is a reason not to remove."""

    class Failing:
        def run_json(self, *_a: Any, **_k: Any):
            from pudge_gaming_manager.utilities.exceptions import PgmError

            raise PgmError(what="нет", reason="PowerShell недоступен")

    source = inventory.AppInventory(powershell=Failing())  # type: ignore[arg-type]
    tweak = RemoveUnusedAppsTweak(source)

    state = tweak.scan(TweakContext())

    assert not state.needs_change
    assert state.current_absent
    assert not tweak.validate(TweakContext(), state).ok


def test_a_dry_run_removes_nothing() -> None:
    powershell = FakePowerShell([("Microsoft.BingNews", "Store")])
    source = inventory.AppInventory(powershell=powershell)  # type: ignore[arg-type]
    tweak = RemoveUnusedAppsTweak(source, powershell=powershell)  # type: ignore[arg-type]
    ctx = TweakContext(dry_run=True)

    state = tweak.scan(ctx)
    assert state.needs_change

    tweak.apply(ctx, state)

    assert powershell.removed == []


def test_the_tweaks_share_one_inventory() -> None:
    """Get-AppxPackage costs two seconds; five calls would cost ten."""
    tweaks = app_tweaks()
    sources = {
        id(getattr(t, "_inventory")) for t in tweaks if hasattr(t, "_inventory")
    }

    assert len(sources) == 1


# -- OneDrive ---------------------------------------------------------------


def test_onedrive_is_looked_for_in_every_known_location(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """Its installer moved between builds; assuming one meant finding none."""
    assert len(onedrive._SETUP_LOCATIONS) >= 3


def test_an_absent_onedrive_is_not_a_change(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(onedrive, "find_setup", lambda: None)

    state = RemoveOneDriveTweak().scan(TweakContext())

    assert not state.needs_change
    assert "не установлен" in state.summary


def test_uninstalling_an_absent_onedrive_succeeds_quietly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(onedrive, "find_setup", lambda: None)

    ok, detail = onedrive.uninstall()

    assert ok
    assert "не установлен" in detail


def test_the_cleaner_still_refuses_to_touch_the_onedrive_folder() -> None:
    """Uninstalling the client must not become deleting somebody's files."""
    from pudge_gaming_manager.windows.cleanup.rules import PROTECTED_PATH_FRAGMENTS

    assert "onedrive" in PROTECTED_PATH_FRAGMENTS


@windows_only
def test_reading_the_real_machine_does_not_raise() -> None:
    source = inventory.AppInventory()

    installed = source.installed()

    assert isinstance(installed, dict)
    for app in installed.values():
        assert app.name
