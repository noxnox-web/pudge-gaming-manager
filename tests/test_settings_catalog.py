"""The settings catalogue, its tweaks, the change history and profile drift.

Everything here runs against fakes: a dictionary standing in for the
registry and a temporary database. Nothing touches this machine (rule #48).
"""

from __future__ import annotations

import pathlib
from dataclasses import replace
from typing import Any

import pytest
from pydantic import ValidationError

from pudge_gaming_manager.core.optimization import history
from pudge_gaming_manager.core.optimization.engine import TweakEngine
from pudge_gaming_manager.core.optimization.tweak import Outcome, RiskLevel, TweakContext
from pudge_gaming_manager.core.optimization.tweaks.choice import (
    FlagStringTweak,
    flag_value,
    tweak_for,
    with_flag,
)
from pudge_gaming_manager.core.optimization.tweaks.registry_value import ABSENT
from pudge_gaming_manager.core.profiles import settings_drift
from pudge_gaming_manager.core.profiles.golden import Difference, DriftSeverity
from pudge_gaming_manager.core.profiles.schema import GoldenProfile, WindowsSettingsPolicy
from pudge_gaming_manager.core.settings import state as settings_state
from pudge_gaming_manager.core.settings.catalog import (
    SETTINGS,
    SETTINGS_BY_ID,
    Option,
    SettingSpec,
    Tier,
    by_tier,
)
from pudge_gaming_manager.core.settings.service import SettingsService
from pudge_gaming_manager.database.connection import Database
from pudge_gaming_manager.windows.graphics.hags import HagsCaps, decode_caps
from pudge_gaming_manager.windows.registry.manager import Hive, RegistryValue, is_allowed_key


class FakeRegistry:
    """One dictionary of values, keyed by (hive, subkey, name)."""

    def __init__(self, values: dict | None = None, type_name: str = "REG_DWORD") -> None:
        self.values: dict[tuple, Any] = dict(values or {})
        self.type_name = type_name
        self.deleted: list[tuple] = []

    def read(self, hive, subkey, name, view=None) -> RegistryValue:
        key = (hive, subkey, name)
        if key not in self.values:
            return RegistryValue(hive, subkey, name, absent=True)
        return RegistryValue(hive, subkey, name, self.values[key], self.type_name)

    def write(self, hive, subkey, name, data, type_name, view=None, **_k) -> None:
        self.values[(hive, subkey, name)] = data

    def delete_value(self, hive, subkey, name, view=None) -> bool:
        self.deleted.append((hive, subkey, name))
        return self.values.pop((hive, subkey, name), None) is not None


def _key(spec: SettingSpec) -> tuple:
    return (spec.hive, spec.subkey, spec.value_name)


#: A user-hive, LOW-risk spec, so the engine applies it without elevation.
TEST_SPEC = SettingSpec(
    id="test_setting",
    name="Тестовая настройка",
    tier=Tier.ADVANCED,
    hive=Hive.HKCU,
    subkey=r"Software\Microsoft\GameBar",
    value_name="TestValue",
    value_type="REG_DWORD",
    options=(Option("on", "включено", 1), Option("off", "выключено", 0)),
    absent_means="on",
    what="Проверочная настройка для тестов, больше ничего не делает.",
    why="Нужна только затем, чтобы прогнать движок без прав администратора.",
    risk=RiskLevel.LOW,
)


# -- the catalogue ----------------------------------------------------------


@pytest.mark.parametrize("spec", SETTINGS, ids=lambda s: s.id)
def test_every_setting_is_complete_and_allowlisted(spec: SettingSpec) -> None:
    assert is_allowed_key(spec.hive, spec.subkey), spec.id
    assert len(spec.why) > 60 and spec.what
    assert spec.risk is not RiskLevel.CRITICAL
    assert spec.absent_means in {o.key for o in spec.options} or spec.extra.get("absent_label")
    if spec.recommended is not None:
        spec.option(spec.recommended)


def test_the_recommended_tier_always_says_what_to_recommend() -> None:
    assert all(s.recommended for s in by_tier(Tier.RECOMMENDED))


def test_disputed_settings_are_never_recommended_blindly() -> None:
    """HAGS and the scheduler values: the sources disagree, so no default."""
    for spec_id in ("hags", "network_throttling", "system_responsiveness",
                    "win32_priority_separation", "mmcss_games_scheduling"):
        assert SETTINGS_BY_ID[spec_id].recommended is None
        assert SETTINGS_BY_ID[spec_id].tier is not Tier.RECOMMENDED


def test_values_microsoft_documents_as_unused_are_not_offered() -> None:
    names = {s.value_name for s in SETTINGS}
    assert "GPU Priority" not in names
    assert "SFIO Priority" not in names


def test_the_optimize_pipeline_takes_only_the_recommended_tier(tmp_path) -> None:
    from pudge_gaming_manager.core.optimization.pipeline import OptimizationPipeline
    from pudge_gaming_manager.hardware.models import CpuInfo, HardwareSnapshot, OsInfo, RamInfo

    pipeline = OptimizationPipeline(Database(tmp_path / "p.db"))
    snapshot = HardwareSnapshot(
        captured_at="", os=OsInfo(), cpu=CpuInfo(), gpus=(), ram=RamInfo(), disks=(), monitors=(),
    )
    ids = {t.id for t in pipeline.build_tweaks(snapshot)}

    for spec in SETTINGS:
        assert (spec.tweak_id in ids) == (spec.tier is Tier.RECOMMENDED), spec.id
    assert "graphics.gpu_scheduling.enable" not in ids
    assert "network.throttling.disable" not in ids


# -- the flag-string setting ------------------------------------------------


def test_with_flag_keeps_every_other_pair() -> None:
    text = "AutoHDREnable=1;SwapEffectUpgradeEnable=0;VRROptimizeEnable=1;"

    result = with_flag(text, "SwapEffectUpgradeEnable", "1")

    assert result == "AutoHDREnable=1;SwapEffectUpgradeEnable=1;VRROptimizeEnable=1;"
    assert flag_value(result, "swapeffectupgradeenable") == "1"
    assert with_flag("", "A", "1") == "A=1;"
    assert flag_value(None, "A") is None


def test_the_windowed_setting_rewrites_only_its_own_pair_and_rolls_back() -> None:
    spec = SETTINGS_BY_ID["windowed_game_optimizations"]
    registry = FakeRegistry({_key(spec): "AutoHDREnable=1;"}, type_name="REG_SZ")
    tweak = tweak_for(spec, "on", registry)
    assert isinstance(tweak, FlagStringTweak)
    ctx = TweakContext()

    state = tweak.scan(ctx)
    backup = tweak.backup(ctx, state)
    tweak.apply(ctx, state)

    assert registry.values[_key(spec)] == "AutoHDREnable=1;SwapEffectUpgradeEnable=1;"
    assert tweak.verify(ctx, state).confirmed
    assert tweak.rollback(ctx, backup).outcome is Outcome.SUCCESS
    assert registry.values[_key(spec)] == "AutoHDREnable=1;"


def test_a_string_that_never_existed_is_deleted_on_rollback() -> None:
    spec = SETTINGS_BY_ID["windowed_game_optimizations"]
    registry = FakeRegistry(type_name="REG_SZ")
    tweak = tweak_for(spec, "on", registry)
    ctx = TweakContext()
    state = tweak.scan(ctx)
    backup = tweak.backup(ctx, state)
    tweak.apply(ctx, state)

    tweak.rollback(ctx, backup)

    assert _key(spec) not in registry.values
    assert registry.deleted


def test_a_setting_newer_than_this_windows_is_refused(monkeypatch) -> None:
    from pudge_gaming_manager.core.optimization.tweaks import choice

    monkeypatch.setattr(choice, "windows_build", lambda: 19045)
    tweak = tweak_for(SETTINGS_BY_ID["windowed_game_optimizations"], "on", FakeRegistry())

    validation = tweak.validate(TweakContext(), tweak.scan(TweakContext()))

    assert not validation.ok
    assert "22000" in validation.reason


# -- reading state ----------------------------------------------------------


def test_absent_reads_as_the_windows_default() -> None:
    spec = SETTINGS_BY_ID["transparency"]

    state = settings_state.read_state(spec, FakeRegistry(), build=22631)

    assert state.absent and state.current_key == "on"
    assert state.matches_recommendation is False


def test_a_value_outside_the_options_is_reported_as_custom() -> None:
    spec = SETTINGS_BY_ID["win32_priority_separation"]

    state = settings_state.read_state(spec, FakeRegistry({_key(spec): 6}), build=22631)

    assert state.current_key is None
    assert "6" in state.current_label


def test_unsupported_hags_is_unavailable_not_offered() -> None:
    caps = HagsCaps(supported=False, enabled=False, detail="нет поддержки")

    state = settings_state.read_state(
        SETTINGS_BY_ID["hags"], FakeRegistry(), build=22631, hags_caps=caps
    )

    assert not state.available


def test_hags_waiting_for_a_restart_says_so() -> None:
    spec = SETTINGS_BY_ID["hags"]
    caps = HagsCaps(supported=True, enabled=False)

    state = settings_state.read_state(spec, FakeRegistry({_key(spec): 2}), build=22631, hags_caps=caps)

    assert state.current_key == "on"
    assert "перезагрузки" in state.current_label


def test_hags_caps_bits_prefer_the_adapter_that_supports_it() -> None:
    # A real GPU (supported + enabled) beside Basic Render (all zero).
    caps = decode_caps([0b1011, 0])
    assert caps.supported and caps.enabled and not caps.enabled_by_default
    assert decode_caps([0]).supported is False
    assert decode_caps([]).supported is None


# -- the service ------------------------------------------------------------


def test_the_service_plans_only_what_was_chosen(tmp_path) -> None:
    service = SettingsService(
        Database(tmp_path / "s.db"), states=lambda: [], devices=lambda: []
    )

    plan = service.preview({"transparency": "off"}, set())

    assert [c.tweak.id for c in plan.changes] == ["setting.transparency"]


# -- history ----------------------------------------------------------------


def _apply(db: Database, registry: FakeRegistry, option: str) -> None:
    engine = TweakEngine(db, TweakContext(database=db))
    tweak = tweak_for(TEST_SPEC, option, registry)
    engine.register([tweak])
    report = engine.apply(engine.plan([tweak]))
    assert report.applied == 1, report.results


def test_a_change_can_be_undone_later_from_history(tmp_path) -> None:
    db = Database(tmp_path / "h.db")
    registry = FakeRegistry({_key(TEST_SPEC): 1})
    _apply(db, registry, "off")
    assert registry.values[_key(TEST_SPEC)] == 0

    [record] = history.list_changes(db)
    result = history.restore(
        db, record.backup_id, resolve=lambda r: tweak_for(TEST_SPEC, "on", registry)
    )

    assert result.ok, result.message
    # Restored as an int, not the text "1" the backup row stores.
    assert registry.values[_key(TEST_SPEC)] == 1
    assert history.list_changes(db)[0].state == "RESTORED"


def test_an_older_change_is_not_undone_over_a_newer_one(tmp_path) -> None:
    db = Database(tmp_path / "h.db")
    registry = FakeRegistry({_key(TEST_SPEC): 1})
    _apply(db, registry, "off")
    # Something else set it back, and PGM changed it again on top.
    registry.values[_key(TEST_SPEC)] = 1
    _apply(db, registry, "off")
    newer, older = history.list_changes(db)

    result = history.restore(
        db, older.backup_id, resolve=lambda r: tweak_for(TEST_SPEC, "on", registry)
    )

    assert not result.ok
    assert "позднее" in result.message


def test_restore_all_unwinds_newest_first(tmp_path) -> None:
    db = Database(tmp_path / "h.db")
    registry = FakeRegistry()  # absent to begin with
    _apply(db, registry, "off")
    _apply(db, registry, "on")

    results = history.restore_all(
        db, resolve=lambda r: tweak_for(TEST_SPEC, "on", registry)
    )

    assert all(r.ok for r in results), results
    assert _key(TEST_SPEC) not in registry.values  # back to "never existed"


def test_an_unknown_tweak_is_reported_not_guessed(tmp_path) -> None:
    db = Database(tmp_path / "h.db")
    _apply(db, FakeRegistry(), "off")
    [record] = history.list_changes(db)

    result = history.restore(db, record.backup_id, resolve=lambda r: None)

    assert not result.ok and "не знает" in result.message


def test_backup_text_is_decoded_by_type() -> None:
    assert history.decode("4294967295", "REG_DWORD") == 0xFFFFFFFF
    assert history.decode("10", "REG_SZ") == "10"
    assert history.decode(None, "REG_DWORD") is None


# -- the profile's settings section ------------------------------------------


def _states(**current: str | None):
    def read():
        return [
            settings_state.SettingState(SETTINGS_BY_ID[k], v, v or "?")
            for k, v in current.items()
        ]
    return read


def test_capture_takes_only_known_readable_values() -> None:
    captured = settings_drift.capture(_states(transparency="off", game_mode=None))

    assert captured == {"transparency": "off"}


def test_drift_is_reported_and_turned_into_fixes() -> None:
    policy = WindowsSettingsPolicy(expected={"transparency": "off", "game_mode": "on"})
    out: list[Difference] = []

    checked, fixes = settings_drift.compare(
        policy, out, states=_states(transparency="on", game_mode="on")
    )

    assert checked == 2
    assert fixes == {"transparency": "off"}
    assert [d.severity for d in out] == [DriftSeverity.DRIFT]


def test_an_unavailable_setting_is_unknown_not_drift() -> None:
    policy = WindowsSettingsPolicy(expected={"hags": "on"})
    unavailable = lambda: [  # noqa: E731
        replace(
            settings_state.SettingState(SETTINGS_BY_ID["hags"], None, "?"),
            available=False, unavailable_reason="нет поддержки",
        )
    ]
    out: list[Difference] = []

    _checked, fixes = settings_drift.compare(policy, out, states=unavailable)

    assert fixes == {} and out[0].severity is DriftSeverity.UNKNOWN


def test_a_profile_can_only_name_catalogue_settings_and_their_options() -> None:
    with pytest.raises(ValidationError):
        GoldenProfile(settings=WindowsSettingsPolicy(expected={"regpath": "on"}))
    with pytest.raises(ValidationError):
        GoldenProfile(settings=WindowsSettingsPolicy(expected={"transparency": "maybe"}))
    assert GoldenProfile().settings.expected == {}


def test_absent_marker_is_not_a_catalogue_value() -> None:
    """The backup's absence marker must never collide with a real option."""
    assert all(o.data != ABSENT for s in SETTINGS for o in s.options)


def test_catalogue_import_does_not_need_the_machine(tmp_path: pathlib.Path) -> None:
    assert SETTINGS and all(isinstance(s, SettingSpec) for s in SETTINGS)


def test_every_reversible_tweak_can_be_undone_from_history() -> None:
    """A tweak that backs up but has no history factory is undoable only
    inside the run that made it — «Откатить всё» would then report it as
    unknown. The dota2.com hosts block shipped that way; this pins it."""
    import importlib
    import pkgutil

    import pudge_gaming_manager.core.optimization.tweaks as package
    from pudge_gaming_manager.core.optimization.tweak import BackupScope, Tweak
    from pudge_gaming_manager.core.optimization.tweaks.choice import RegistryChoiceTweak

    for module in pkgutil.iter_modules(package.__path__):
        importlib.import_module(f"{package.__name__}.{module.name}")

    def concrete(cls):
        for sub in cls.__subclasses__():
            if not sub.__dict__.get("abstract_base"):
                yield sub
            yield from concrete(sub)

    missing = [
        cls.__name__
        for cls in concrete(Tweak)
        if cls.__module__.startswith(package.__name__)  # not other tests' fakes
        and cls.scope is not BackupScope.NONE
        and not issubclass(cls, RegistryChoiceTweak)  # resolved via the catalogue
        and cls.id not in history._FACTORIES
    ]
    assert missing == []
