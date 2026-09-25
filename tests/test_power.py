"""Tests for power-scheme management and the power-plan tweak.

Parser tests run against **captured Russian output**, because the parser's
job is to work on a localised club PC. An English-only fixture would pass
while the real machine returned nothing.

The live apply/rollback test is opt-in via ``PGM_LIVE_SYSTEM_TESTS=1``: the
default test run must never change the operator's active power plan.
"""

from __future__ import annotations

import os
import pathlib

import pytest

from pudge_gaming_manager.core.optimization.engine import TweakEngine
from pudge_gaming_manager.core.optimization import engine as engine_module
from pudge_gaming_manager.core.optimization.tweak import Outcome, TweakContext
from pudge_gaming_manager.core.optimization.tweaks.power import SetPowerPlanTweak
from pudge_gaming_manager.database.connection import Database
from pudge_gaming_manager.utilities.command_runner import CommandResult
from pudge_gaming_manager.utilities.exceptions import PgmError
from pudge_gaming_manager.windows.power.settings import PowerSettingIndex
from pudge_gaming_manager.windows.power.manager import BuiltInScheme, PowerManager

windows_only = pytest.mark.skipif(os.name != "nt", reason="Windows-only behaviour")
live_only = pytest.mark.skipif(
    os.environ.get("PGM_LIVE_SYSTEM_TESTS") != "1",
    reason="set PGM_LIVE_SYSTEM_TESTS=1 to allow changing this machine",
)

# Real output captured from the Russian-locale development machine.
RUSSIAN_LIST_OUTPUT = """
Существующие схемы управления питанием (* - активные)
-----------------------------------
GUID схемы питания: 1e8e0931-6467-43d2-b99b-2cdb7ff43a8d  (Максимальная производительность)
GUID схемы питания: 381b4222-f694-41f0-9685-ff5bb260df2e  (Сбалансированная)
GUID схемы питания: 8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c  (Высокая производительность)
GUID схемы питания: a1841308-3541-4fab-bc81-f71556f20b4a  (Экономия энергии)
GUID схемы питания: cf6bc033-00db-4e65-b95b-203c360432bf  (Игровая производительность) *
"""

RUSSIAN_ACTIVE_OUTPUT = (
    "GUID схемы питания: cf6bc033-00db-4e65-b95b-203c360432bf  "
    "(Игровая производительность)\n"
)

ENGLISH_LIST_OUTPUT = """
Existing Power Schemes (* Active)
-----------------------------------
Power Scheme GUID: 381b4222-f694-41f0-9685-ff5bb260df2e  (Balanced) *
Power Scheme GUID: 8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c  (High performance)
"""


class FakeRunner:
    """A CommandRunner stand-in returning canned powercfg output."""

    def __init__(self, list_output: str = RUSSIAN_LIST_OUTPUT,
                 active_output: str = RUSSIAN_ACTIVE_OUTPUT) -> None:
        self.list_output = list_output
        self.active_output = active_output
        self.commands: list[list[str]] = []
        self.fail_setactive = False

    def _result(self, args, stdout: str = "", code: int = 0) -> CommandResult:
        return CommandResult(tuple(args), code, stdout, "", 0.01)

    def run(self, args, **kwargs) -> CommandResult:
        args = list(args)
        self.commands.append(args)
        if "/setactive" in args:
            if self.fail_setactive:
                from pudge_gaming_manager.utilities.exceptions import CommandFailedError

                raise CommandFailedError(
                    " ".join(args), exit_code=1, stderr="Access is denied."
                )
            self.active_output = (
                f"GUID: {args[-1]}  (switched)\n"
            )
            return self._result(args)
        return self._result(args)

    def try_run(self, args, **kwargs) -> CommandResult:
        args = list(args)
        self.commands.append(args)
        if "/list" in args:
            return self._result(args, self.list_output)
        if "/getactivescheme" in args:
            return self._result(args, self.active_output)
        return self._result(args)


# -- parsing ---------------------------------------------------------------


def test_parses_localised_scheme_list() -> None:
    """The parser must not depend on English keywords."""
    schemes = PowerManager._parse_scheme_list(RUSSIAN_LIST_OUTPUT)
    assert len(schemes) == 5
    assert {s.normalized_guid for s in schemes} >= {
        BuiltInScheme.BALANCED.value,
        BuiltInScheme.HIGH_PERFORMANCE.value,
        BuiltInScheme.POWER_SAVER.value,
    }


def test_active_scheme_detected_by_asterisk_not_by_name() -> None:
    schemes = PowerManager._parse_scheme_list(RUSSIAN_LIST_OUTPUT)
    active = [s for s in schemes if s.is_active]
    assert len(active) == 1
    assert active[0].normalized_guid == "cf6bc033-00db-4e65-b95b-203c360432bf"


def test_parser_also_handles_english_output() -> None:
    schemes = PowerManager._parse_scheme_list(ENGLISH_LIST_OUTPUT)
    assert len(schemes) == 2
    assert [s for s in schemes if s.is_active][0].name == "Balanced"


def test_custom_non_microsoft_schemes_are_included() -> None:
    """A club PC often has an OEM or vendor-created plan active."""
    schemes = PowerManager._parse_scheme_list(RUSSIAN_LIST_OUTPUT)
    guids = {s.normalized_guid for s in schemes}
    builtin = {b.value for b in BuiltInScheme}
    assert guids - builtin, "custom schemes must not be filtered out"


def test_scheme_names_are_captured() -> None:
    schemes = PowerManager._parse_scheme_list(RUSSIAN_LIST_OUTPUT)
    by_guid = {s.normalized_guid: s.name for s in schemes}
    assert by_guid[BuiltInScheme.BALANCED.value] == "Сбалансированная"


def test_empty_output_yields_no_schemes() -> None:
    assert PowerManager._parse_scheme_list("") == []


# -- manager ---------------------------------------------------------------


def test_get_active_guid_from_fake() -> None:
    manager = PowerManager(runner=FakeRunner())  # type: ignore[arg-type]
    assert manager.get_active_guid() == "cf6bc033-00db-4e65-b95b-203c360432bf"


def test_scheme_exists() -> None:
    manager = PowerManager(runner=FakeRunner())  # type: ignore[arg-type]
    assert manager.scheme_exists(BuiltInScheme.HIGH_PERFORMANCE.value)
    assert not manager.scheme_exists(BuiltInScheme.ULTIMATE_PERFORMANCE.value)


def test_set_active_rejects_a_malformed_identifier() -> None:
    manager = PowerManager(runner=FakeRunner())  # type: ignore[arg-type]
    with pytest.raises(PgmError, match="не является идентификатором"):
        manager.set_active("; shutdown /s")


def test_set_active_rejects_a_missing_scheme() -> None:
    manager = PowerManager(runner=FakeRunner())  # type: ignore[arg-type]
    with pytest.raises(PgmError) as excinfo:
        manager.set_active(BuiltInScheme.ULTIMATE_PERFORMANCE.value)
    assert "Максимальная производительность" in (excinfo.value.remedy or "")


def test_access_denied_becomes_an_actionable_error() -> None:
    runner = FakeRunner()
    runner.fail_setactive = True
    manager = PowerManager(runner=runner)  # type: ignore[arg-type]

    with pytest.raises(PgmError) as excinfo:
        manager.set_active(BuiltInScheme.HIGH_PERFORMANCE.value)
    assert "администратора" in (excinfo.value.remedy or "")


# -- tweak lifecycle (fakes only) -----------------------------------------


@pytest.fixture()
def db(tmp_path: pathlib.Path) -> Database:
    return Database(tmp_path / "power.db")


class FakePowerSettings:
    """Stands in for the powercfg-backed settings reader.

    The tweak consults the minimum processor state to decide whether the
    active scheme already achieves what switching would achieve. Tests that
    want the switch to be needed say the processor is allowed to drop.
    """

    def __init__(self, min_processor_state: int = 5) -> None:
        self.min_processor_state = min_processor_state

    def read(self, _subgroup: str, _setting: str, _scheme: str = "") -> object:
        return PowerSettingIndex(ac=self.min_processor_state, dc=5)


def _tweak_with_fake(
    min_processor_state: int = 5,
) -> tuple[SetPowerPlanTweak, PowerManager]:
    manager = PowerManager(runner=FakeRunner())  # type: ignore[arg-type]
    tweak = SetPowerPlanTweak(
        target_guid=BuiltInScheme.HIGH_PERFORMANCE.value,
        target_name="High performance",
        manager=manager,
        settings=FakePowerSettings(min_processor_state),  # type: ignore[arg-type]
    )
    return tweak, manager


def test_a_scheme_already_holding_the_processor_at_full_is_left_alone() -> None:
    """Enabling Ultimate Performance clones the scheme under a fresh GUID.

    So "is the active GUID one of the good ones" cannot answer this, and
    answering it wrongly meant offering to move a machine from Ultimate
    Performance *down* to High Performance and calling that an
    optimisation. The tweak asks the machine instead.
    """
    tweak, _ = _tweak_with_fake(min_processor_state=100)

    state = tweak.scan(TweakContext())

    assert not state.needs_change
    assert "100%" in state.summary


def test_scan_detects_a_needed_change() -> None:
    tweak, _ = _tweak_with_fake()
    state = tweak.scan(TweakContext())
    assert state.needs_change
    assert "->" in state.summary


def test_scan_reports_no_change_when_already_active() -> None:
    manager = PowerManager(runner=FakeRunner())  # type: ignore[arg-type]
    tweak = SetPowerPlanTweak(
        target_guid="cf6bc033-00db-4e65-b95b-203c360432bf",
        target_name="Gaming",
        manager=manager,
    )
    assert not tweak.scan(TweakContext()).needs_change


def test_validate_refuses_a_plan_this_pc_lacks() -> None:
    manager = PowerManager(runner=FakeRunner())  # type: ignore[arg-type]
    tweak = SetPowerPlanTweak(
        target_guid=BuiltInScheme.ULTIMATE_PERFORMANCE.value,
        target_name="Ultimate Performance",
        manager=manager,
    )
    ctx = TweakContext()
    validation = tweak.validate(ctx, tweak.scan(ctx))
    assert not validation.ok
    assert "Максимальная производительность" in validation.reason


def test_backup_records_the_previous_guid() -> None:
    tweak, _ = _tweak_with_fake()
    ctx = TweakContext()
    state = tweak.scan(ctx)
    backup = tweak.backup(ctx, state)

    assert backup.old_value == "cf6bc033-00db-4e65-b95b-203c360432bf"
    assert not backup.old_value_absent
    assert backup.new_value == BuiltInScheme.HIGH_PERFORMANCE.value


def test_full_cycle_through_the_engine(db: Database,
                                       monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(engine_module, "is_admin", lambda: True)
    tweak, manager = _tweak_with_fake()

    eng = TweakEngine(db, TweakContext())
    report = eng.apply(eng.plan([tweak]))

    assert report.applied == 1
    assert manager.get_active_guid() == BuiltInScheme.HIGH_PERFORMANCE.value
    row = db.query_one("SELECT old_value, state FROM backups")
    assert row is not None
    assert row["state"] == "APPLIED"
    assert row["old_value"] == "cf6bc033-00db-4e65-b95b-203c360432bf"


def test_rollback_restores_and_verifies() -> None:
    tweak, manager = _tweak_with_fake()
    ctx = TweakContext()
    state = tweak.scan(ctx)
    backup = tweak.backup(ctx, state)

    tweak.apply(ctx, state)
    assert tweak.verify(ctx, state).confirmed

    result = tweak.rollback(ctx, backup)
    assert result.outcome is Outcome.SUCCESS
    assert manager.get_active_guid() == "cf6bc033-00db-4e65-b95b-203c360432bf"


# -- live, read-only -------------------------------------------------------


@windows_only
def test_live_machine_reports_power_schemes() -> None:
    schemes = PowerManager().list_schemes()
    assert schemes, "powercfg reported no schemes"
    assert sum(1 for s in schemes if s.is_active) == 1


@windows_only
def test_live_active_guid_is_in_the_list() -> None:
    manager = PowerManager()
    active = manager.get_active_guid()
    assert active
    assert active in {s.normalized_guid for s in manager.list_schemes()}


@windows_only
def test_live_scheme_names_are_not_mojibake() -> None:
    """Proves the OEM codepage decode survives into parsed values."""
    schemes = PowerManager().list_schemes()
    assert all("�" not in s.name for s in schemes)


# -- live, mutating (opt-in) ----------------------------------------------


@windows_only
@live_only
def test_live_apply_then_rollback_restores_the_original_plan(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end against the real machine, restoring the original plan."""
    monkeypatch.setattr(engine_module, "is_admin", lambda: True)
    manager = PowerManager()
    original = manager.get_active_guid()
    assert original

    target = (
        BuiltInScheme.BALANCED.value
        if original != BuiltInScheme.BALANCED.value
        else BuiltInScheme.HIGH_PERFORMANCE.value
    )
    tweak = SetPowerPlanTweak(target_guid=target, target_name="test target")

    try:
        eng = TweakEngine(db, TweakContext())
        report = eng.apply(eng.plan([tweak]))
        assert report.applied == 1
        assert manager.get_active_guid() == target
    finally:
        manager.set_active(original)

    assert manager.get_active_guid() == original
