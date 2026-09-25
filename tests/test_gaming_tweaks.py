"""The gaming tweaks added on top of the original five.

Most of this file is about the two things that actually went wrong while
writing them, both of which were silent: a powercfg parse that read a
setting's lower bound as its current value, and a power-plan check that
offered to move a machine from Ultimate Performance down to High
Performance and call it an optimisation.
"""

from __future__ import annotations

import os
from typing import Any

import pytest

from pudge_gaming_manager.core.optimization.tweak import (
    BackupScope,
    Outcome,
    RiskLevel,
    TweakContext,
)
from pudge_gaming_manager.core.optimization.tweaks.game_dvr import (
    DisableGameDvrPolicyTweak,
    DisableGameDvrTweak,
)
from pudge_gaming_manager.core.optimization.tweaks.graphics import (
    HardwareGpuSchedulingTweak,
    NetworkThrottlingTweak,
)
from pudge_gaming_manager.core.optimization.tweaks.mouse import (
    DisableMouseAccelerationTweak,
)
from pudge_gaming_manager.core.optimization.tweaks.power_settings import (
    DisablePcieAspmTweak,
    DisableUsbSelectiveSuspendTweak,
)
from pudge_gaming_manager.core.optimization.tweaks.registry_value import ABSENT
from pudge_gaming_manager.windows.input import mouse
from pudge_gaming_manager.windows.power.settings import (
    PowerSettings,
    _current_indices,
)
from pudge_gaming_manager.windows.registry.manager import (
    Hive,
    RegistryValue,
    is_allowed_key,
)

windows_only = pytest.mark.skipif(os.name != "nt", reason="Windows-only behaviour")


# -- the powercfg parse -----------------------------------------------------

ENUM_OUTPUT = """GUID схемы питания: cf6bc033  (Максимальная производительность)
  GUID подгруппы: 2a737441  (Параметры USB)
    GUID настройки питания: 48e6b7a6  (Временное отключение USB-порта)
      Индекс возможной настройки: 000
      Понятное имя возможной настройки: Запрещено
      Индекс возможной настройки: 001
      Понятное имя возможной настройки: Разрешено
    Текущий индекс настройки питания от сети: 0x00000001
    Текущий индекс настройки питания от батарей: 0x00000001
"""

RANGE_OUTPUT = """GUID схемы питания: cf6bc033  (Максимальная производительность)
  GUID подгруппы: 54533251  (Управление питанием процессора)
    GUID настройки питания: 893dee8e  (Минимальное состояние процессора)
      Псевдоним GUID: PROCTHROTTLEMIN
      Минимальная возможная настройка: 0x00000000
      Максимальная возможная настройка: 0x00000064
      Инкремент возможных настроек: 0x00000001
      Единицы возможных настроек: %
    Текущий индекс настройки питания от сети: 0x00000064
    Текущий индекс настройки питания от батарей: 0x00000005
"""

ENGLISH_OUTPUT = """Power Scheme GUID: cf6bc033  (High performance)
  Subgroup GUID: 54533251  (Processor power management)
    Power Setting GUID: 893dee8e  (Minimum processor state)
      GUID Alias: PROCTHROTTLEMIN
      Minimum Possible Setting: 0x00000000
      Maximum Possible Setting: 0x00000064
      Possible Settings increment: 0x00000001
      Possible Settings units: %
    Current AC Power Setting Index: 0x00000032
    Current DC Power Setting Index: 0x00000005
"""


def test_an_enumerated_setting_reads_its_current_values() -> None:
    assert _current_indices(ENUM_OUTPUT) == [1, 1]


def test_a_range_setting_does_not_report_its_lower_bound_as_current() -> None:
    """The bug this parse was rewritten for.

    A range setting prints its bounds in hex too, so "the first two hex
    numbers" picked up the minimum, 0, and a processor pinned at 100% read
    as one sitting at 0 — which made the power-plan tweak offer a change
    that was already in force.
    """
    assert _current_indices(RANGE_OUTPUT) == [100, 5]


def test_the_parse_does_not_depend_on_the_windows_language() -> None:
    """Not one label is read, so an English install parses identically."""
    assert _current_indices(ENGLISH_OUTPUT) == [50, 5]


def test_output_without_any_current_index_yields_nothing() -> None:
    assert _current_indices("GUID схемы питания: cf6bc033\n") == []


def test_a_setting_absent_from_this_machine_reads_as_none() -> None:
    class Refusing:
        def run(self, *_a: Any, **_k: Any) -> Any:
            class Result:
                exit_code = 1
                stdout = ""

            return Result()

    settings = PowerSettings(runner=Refusing())  # type: ignore[arg-type]

    assert settings.read("nope", "nope") is None


# -- the registry-value base ------------------------------------------------


class FakeRegistry:
    """A registry that remembers one value, including its absence."""

    def __init__(self, data: Any = None, absent: bool = True) -> None:
        self.data = data
        self.absent = absent
        self.deleted = False

    def read(self, hive, subkey, name, view=None) -> RegistryValue:
        return RegistryValue(
            hive=hive, subkey=subkey, name=name,
            data=self.data, type_name="REG_DWORD", absent=self.absent,
        )

    def write(self, hive, subkey, name, data, type_name, view=None, **_k) -> None:
        self.data = data
        self.absent = False

    def delete_value(self, hive, subkey, name, view=None) -> None:
        self.data = None
        self.absent = True
        self.deleted = True


def test_an_absent_value_is_a_change_to_be_made() -> None:
    """Absent means Windows is using its default, which is not the target."""
    registry = FakeRegistry(absent=True)
    tweak = NetworkThrottlingTweak(registry=registry)  # type: ignore[arg-type]

    state = tweak.scan(TweakContext())

    assert state.needs_change
    assert state.current_value == ABSENT
    # Absent is readable, so the change is allowed — unlike a value that
    # could not be read at all.
    assert tweak.validate(TweakContext(), state).ok


def test_rollback_deletes_a_value_that_did_not_exist() -> None:
    """Writing zero back would be a different setting under the same name.

    Windows reads a missing entry as "use the default", and that default is
    frequently not zero — for the network throttle it is 10.
    """
    registry = FakeRegistry(absent=True)
    tweak = NetworkThrottlingTweak(registry=registry)  # type: ignore[arg-type]
    ctx = TweakContext()

    state = tweak.scan(ctx)
    backup = tweak.backup(ctx, state)
    assert backup.old_value_absent

    tweak.apply(ctx, state)
    assert not registry.absent

    result = tweak.rollback(ctx, backup)

    assert result.outcome is Outcome.SUCCESS
    assert registry.deleted
    assert registry.absent


def test_rollback_restores_a_value_that_did_exist() -> None:
    registry = FakeRegistry(data=10, absent=False)
    tweak = NetworkThrottlingTweak(registry=registry)  # type: ignore[arg-type]
    ctx = TweakContext()

    state = tweak.scan(ctx)
    backup = tweak.backup(ctx, state)
    tweak.apply(ctx, state)
    assert registry.data == 0xFFFFFFFF

    result = tweak.rollback(ctx, backup)

    assert result.outcome is Outcome.SUCCESS
    assert registry.data == 10
    assert not registry.deleted


def test_a_value_already_correct_needs_no_change() -> None:
    registry = FakeRegistry(data=0xFFFFFFFF, absent=False)
    tweak = NetworkThrottlingTweak(registry=registry)  # type: ignore[arg-type]

    assert not tweak.scan(TweakContext()).needs_change


def test_verification_reads_back_rather_than_trusting_the_write() -> None:
    registry = FakeRegistry(absent=True)
    tweak = NetworkThrottlingTweak(registry=registry)  # type: ignore[arg-type]
    ctx = TweakContext()
    state = tweak.scan(ctx)
    tweak.apply(ctx, state)

    assert tweak.verify(ctx, state).confirmed

    # Something undid it behind our back; verification must notice.
    registry.data = 10
    assert not tweak.verify(ctx, state).confirmed


def test_a_restart_only_setting_says_so_and_does_not_claim_to_be_live() -> None:
    registry = FakeRegistry(absent=True)
    tweak = HardwareGpuSchedulingTweak(registry=registry)  # type: ignore[arg-type]
    ctx = TweakContext()

    result = tweak.apply(ctx, tweak.scan(ctx))

    assert result.needs_restart
    assert "перезагрузк" in result.detail
    assert "перезагрузк" in tweak.verify(ctx, tweak.scan(ctx)).detail


# -- every new tweak keeps the contract -------------------------------------

NEW_TWEAKS = [
    HardwareGpuSchedulingTweak,
    NetworkThrottlingTweak,
    DisableGameDvrTweak,
    DisableGameDvrPolicyTweak,
    DisableMouseAccelerationTweak,
    DisableUsbSelectiveSuspendTweak,
    DisablePcieAspmTweak,
]


@pytest.mark.parametrize("cls", NEW_TWEAKS, ids=lambda c: c.__name__)
def test_every_tweak_explains_itself(cls) -> None:
    """Rule #64: no unexplained change."""
    assert cls.id and cls.name and cls.description and cls.rationale
    assert len(cls.rationale) > 60, "a rationale of a few words is not one"


@pytest.mark.parametrize("cls", NEW_TWEAKS, ids=lambda c: c.__name__)
def test_no_tweak_promises_a_frame_rate(cls) -> None:
    """Rule #65: a changed setting is not a measured improvement."""
    text = f"{cls.description} {cls.rationale}".lower()
    for claim in ("fps", "кадр/с", "прирост fps", "больше кадров", "быстрее игры"):
        assert claim not in text, f"{cls.__name__} claims {claim!r}"


@pytest.mark.parametrize("cls", NEW_TWEAKS, ids=lambda c: c.__name__)
def test_every_tweak_is_reversible(cls) -> None:
    """None of these is a deletion, so all of them must back up."""
    assert cls.scope is not BackupScope.NONE


@pytest.mark.parametrize("cls", NEW_TWEAKS, ids=lambda c: c.__name__)
def test_no_tweak_is_critical(cls) -> None:
    assert cls.risk is not RiskLevel.CRITICAL


def test_every_registry_tweak_targets_an_allowlisted_key() -> None:
    """A key outside the allowlist is refused at runtime; catch it here."""
    for cls in (
        HardwareGpuSchedulingTweak,
        NetworkThrottlingTweak,
        DisableGameDvrTweak,
        DisableGameDvrPolicyTweak,
    ):
        assert is_allowed_key(cls.hive, cls.subkey), cls.__name__


def test_the_allowlist_still_refuses_what_it_always_refused() -> None:
    """Adding Game DVR must not have opened the Policies tree."""
    assert not is_allowed_key(
        Hive.HKLM, r"SOFTWARE\Policies\Microsoft\Windows Defender"
    )
    assert not is_allowed_key(Hive.HKLM, r"SYSTEM\CurrentControlSet\Services")
    assert not is_allowed_key(
        Hive.HKCU, r"Software\Microsoft\Windows\CurrentVersion\Run"
    )


# -- the mouse setting ------------------------------------------------------


def test_acceleration_is_the_switch_not_the_thresholds() -> None:
    assert mouse.MouseAcceleration(6, 10, 1).enabled
    assert not mouse.MouseAcceleration(6, 10, 0).enabled
    assert not mouse.DISABLED.enabled
    assert mouse.DEFAULT.enabled


def test_the_backup_form_round_trips() -> None:
    for setting in (mouse.DEFAULT, mouse.DISABLED, mouse.MouseAcceleration(3, 7, 2)):
        assert mouse.MouseAcceleration.from_text(setting.as_text()) == setting


def test_a_malformed_backup_is_refused_rather_than_guessed() -> None:
    for text in ("", "1,2", "a,b,c", "1,2,3,4"):
        assert mouse.MouseAcceleration.from_text(text) is None


def test_an_unreadable_setting_blocks_the_change() -> None:
    """Without the old values there is no way back, so it must not proceed."""
    from pudge_gaming_manager.core.optimization.tweak import TweakState

    tweak = DisableMouseAccelerationTweak()
    # The state scan() produces when SystemParametersInfo will not answer.
    unreadable = TweakState(
        needs_change=False, current_absent=True, summary="не прочитано"
    )

    assert not tweak.validate(TweakContext(), unreadable).ok


@windows_only
def test_reading_the_live_mouse_setting_works() -> None:
    current = mouse.read()

    assert current is not None
    assert current.acceleration >= 0
