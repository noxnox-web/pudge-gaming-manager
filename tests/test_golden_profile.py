"""Tests for Golden Profile capture, comparison and loading.

The loading tests are the important ones: a profile is untrusted input that
arrives on removable media, so the schema is a security boundary, not a
convenience.
"""

from __future__ import annotations

import json
import pathlib

import pytest
from pydantic import ValidationError

from pudge_gaming_manager.core.profiles import golden, storage
from pudge_gaming_manager.core.profiles.schema import (
    SCHEMA_VERSION,
    CleanupPolicy,
    DisplayPolicy,
    GoldenProfile,
    HardwareExpectations,
    PowerPolicy,
    ServicePolicy,
)
from pudge_gaming_manager.hardware.models import (
    CpuInfo,
    DiskInfo,
    DisplayMode,
    GpuInfo,
    HardwareSnapshot,
    MonitorInfo,
    OsInfo,
    RamInfo,
    Reading,
)
from pudge_gaming_manager.utilities.exceptions import ProfileSchemaError
from pudge_gaming_manager.windows.power.manager import PowerScheme

BALANCED = "381b4222-f694-41f0-9685-ff5bb260df2e"
HIGH_PERF = "8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c"


def _snapshot(**kwargs) -> HardwareSnapshot:
    base = {
        "captured_at": "2026-09-20T00:00:00+00:00",
        "os": OsInfo(machine_name="PC-01", caption="Windows 11 Pro", build="22631"),
        "cpu": CpuInfo(model="Test CPU", physical_cores=Reading(6)),
        "gpus": (
            GpuInfo(
                vendor="NVIDIA",
                model="NVIDIA GeForce RTX 3060",
                vram_total_mb=Reading(12288, unit=" MB"),
            ),
        ),
        "ram": RamInfo(total_mb=Reading(16384, unit=" MB")),
        "disks": (
            DiskInfo(
                device_id="C:",
                is_system_disk=True,
                media_type="SSD",
                free_percent=Reading(45.0, unit="%"),
            ),
        ),
        "monitors": (
            MonitorInfo(
                device_name=r"\\.\DISPLAY1",
                friendly_name="Main",
                is_primary=True,
                current_mode=DisplayMode(1920, 1080, 240),
                max_refresh_at_current_resolution=Reading(240, unit=" Hz"),
            ),
        ),
    }
    base.update(kwargs)
    return HardwareSnapshot(**base)  # type: ignore[arg-type]


# -- capture ---------------------------------------------------------------


def test_capture_records_the_reference_machine() -> None:
    profile = golden.capture(
        _snapshot(), name="Club", active_power_scheme=(HIGH_PERF, "High performance")
    )
    assert profile.name == "Club"
    assert profile.hardware.min_ram_gb == 16
    assert profile.hardware.min_vram_gb == 12
    assert profile.hardware.gpu_vendor == "NVIDIA"
    assert profile.hardware.require_ssd_system_disk is True
    assert profile.power.scheme_guid == HIGH_PERF


def test_capture_leaves_unreadable_values_unset() -> None:
    """An unreadable figure must not become a requirement (rule #64)."""
    profile = golden.capture(
        _snapshot(ram=RamInfo(), gpus=()),
    )
    assert profile.hardware.min_ram_gb is None
    assert profile.hardware.min_vram_gb is None
    assert profile.hardware.gpu_vendor is None


def test_captured_profile_matches_its_own_machine() -> None:
    """A reference PC must not fail the profile taken from it."""
    snapshot = _snapshot()
    profile = golden.capture(snapshot, active_power_scheme=(HIGH_PERF, "High"))
    result = golden.compare(profile, snapshot, active_power_guid=HIGH_PERF)
    assert result.matches, [d.line() for d in result.drifted]


# -- comparison ------------------------------------------------------------


def test_detects_less_memory() -> None:
    profile = GoldenProfile(hardware=HardwareExpectations(min_ram_gb=16))
    result = golden.compare(profile, _snapshot(ram=RamInfo(total_mb=Reading(8192))))
    assert len(result.drifted) == 1
    assert result.drifted[0].setting == "min_ram_gb"


def test_detects_wrong_power_plan() -> None:
    profile = GoldenProfile(
        power=PowerPolicy(scheme_guid=HIGH_PERF, scheme_name="High performance")
    )
    result = golden.compare(profile, _snapshot(), active_power_guid=BALANCED)
    drift = result.drifted[0]
    assert drift.section == "power"
    assert drift.fixable


def test_matching_power_plan_is_not_drift() -> None:
    profile = GoldenProfile(power=PowerPolicy(scheme_guid=HIGH_PERF))
    result = golden.compare(profile, _snapshot(), active_power_guid=HIGH_PERF.upper())
    assert result.matches


def test_detects_display_below_capability() -> None:
    monitor = MonitorInfo(
        friendly_name="Main",
        is_primary=True,
        current_mode=DisplayMode(1920, 1080, 144),
        max_refresh_at_current_resolution=Reading(240, unit=" Hz"),
    )
    profile = GoldenProfile(display=DisplayPolicy(require_maximum_refresh_rate=True))
    result = golden.compare(profile, _snapshot(monitors=(monitor,)))
    assert result.drifted
    assert result.drifted[0].fixable


def test_unreadable_value_is_unknown_not_drift() -> None:
    """Missing data is not evidence of a problem (rule #64)."""
    profile = GoldenProfile(hardware=HardwareExpectations(min_ram_gb=16))
    result = golden.compare(profile, _snapshot(ram=RamInfo()))
    assert not result.drifted
    assert len(result.unknown) == 1
    assert "не удалось" in result.unknown[0].detail.lower()


def test_empty_profile_checks_nothing_and_matches() -> None:
    result = golden.compare(GoldenProfile(), _snapshot())
    assert result.matches


def test_result_is_machine_readable() -> None:
    profile = GoldenProfile(hardware=HardwareExpectations(min_ram_gb=32))
    payload = golden.compare(profile, _snapshot()).to_dict()
    assert payload["matches"] is False
    assert payload["differences"][0]["section"] == "hardware"
    json.dumps(payload)  # must be serialisable


# -- live machine state -----------------------------------------------------


class _FakePower:
    """Stands in for PowerManager so no test runs powercfg."""

    def __init__(self, guid: str | None, name: str = "") -> None:
        self.guid, self.name, self.calls = guid, name, 0

    def get_active_guid(self) -> str | None:
        self.calls += 1
        return self.guid

    def get_active_scheme(self) -> PowerScheme | None:
        self.calls += 1
        if self.guid is None:
            return None
        return PowerScheme(guid=self.guid, name=self.name, is_active=True)


def test_capture_from_machine_records_the_live_power_plan() -> None:
    power = _FakePower(HIGH_PERF.upper(), "High performance")
    profile = golden.capture_from_machine(_snapshot(), name="Club", power=power)
    assert profile.power.scheme_guid == HIGH_PERF
    assert profile.power.scheme_name == "High performance"


def test_capture_from_machine_omits_an_unreadable_power_plan() -> None:
    profile = golden.capture_from_machine(_snapshot(), power=_FakePower(None))
    assert profile.power.scheme_guid is None


def test_compare_with_machine_uses_the_live_power_plan() -> None:
    profile = GoldenProfile(power=PowerPolicy(scheme_guid=HIGH_PERF))
    result = golden.compare_with_machine(
        profile, _snapshot(), power=_FakePower(BALANCED)
    )
    assert result.drifted[0].section == "power"


def test_compare_with_machine_skips_powercfg_when_no_plan_is_pinned() -> None:
    power = _FakePower(BALANCED)
    golden.compare_with_machine(GoldenProfile(), _snapshot(), power=power)
    assert power.calls == 0


# -- round trip ------------------------------------------------------------


def test_save_and_load_round_trip(tmp_path: pathlib.Path) -> None:
    profile = golden.capture(_snapshot(), name="Club A")
    path = storage.save(profile, tmp_path / storage.PROFILE_FILENAME)
    loaded = storage.load(path)
    assert loaded == profile


def test_saved_profile_is_readable_json(tmp_path: pathlib.Path) -> None:
    path = storage.save(golden.capture(_snapshot()), tmp_path / "p.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["schema_version"] == SCHEMA_VERSION


# -- the schema as a security boundary ------------------------------------


def test_unknown_field_is_rejected(tmp_path: pathlib.Path) -> None:
    """A profile cannot smuggle in fields this build does not know."""
    path = tmp_path / "p.json"
    path.write_text(
        json.dumps({"schema_version": 1, "name": "x", "run_command": "calc.exe"}),
        encoding="utf-8",
    )
    with pytest.raises(ProfileSchemaError) as excinfo:
        storage.load(path)
    assert "run_command" in excinfo.value.reason


def test_nested_unknown_field_is_rejected(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "p.json"
    path.write_text(
        json.dumps(
            {"schema_version": 1, "power": {"scheme_guid": BALANCED, "script": "x"}}
        ),
        encoding="utf-8",
    )
    with pytest.raises(ProfileSchemaError):
        storage.load(path)


def test_future_schema_version_is_rejected(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "p.json"
    path.write_text(json.dumps({"schema_version": 99}), encoding="utf-8")
    with pytest.raises(ProfileSchemaError) as excinfo:
        storage.load(path)
    assert "99" in excinfo.value.reason


def test_malformed_guid_is_rejected() -> None:
    with pytest.raises(ValidationError):
        PowerPolicy(scheme_guid="; shutdown /s /t 0")


def test_profile_cannot_target_a_protected_service() -> None:
    """A config file must not be able to reconfigure Defender (rule #47)."""
    with pytest.raises(ValidationError) as excinfo:
        ServicePolicy(expected={"WinDefend": {"startup_type": "Manual"}})
    assert "protected" in str(excinfo.value)


def test_profile_cannot_disable_a_service() -> None:
    """``Disabled`` is not an allowed startup type in a profile."""
    with pytest.raises(ValidationError):
        ServicePolicy(expected={"Spooler": {"startup_type": "Disabled"}})


def test_unknown_cleanup_category_is_rejected() -> None:
    with pytest.raises(ValidationError) as excinfo:
        CleanupPolicy(enabled_categories=("delete.everything",))
    assert "unknown cleanup categories" in str(excinfo.value)


def test_known_cleanup_category_is_accepted() -> None:
    assert CleanupPolicy(enabled_categories=("temp.user",)).enabled_categories


def test_games_policy_deduplicates_and_orders_app_ids() -> None:
    from pudge_gaming_manager.core.profiles.schema import GamesPolicy

    policy = GamesPolicy(keep_steam_app_ids=(570, 730, 570))
    assert policy.keep_steam_app_ids == (570, 730)


def test_games_policy_rejects_a_non_positive_app_id() -> None:
    from pudge_gaming_manager.core.profiles.schema import GamesPolicy

    with pytest.raises(ValidationError):
        GamesPolicy(keep_steam_app_ids=(0,))


def test_profile_without_a_games_section_defaults_to_empty() -> None:
    assert GoldenProfile().games.keep_steam_app_ids == ()


def test_name_with_path_characters_is_rejected() -> None:
    with pytest.raises(ValidationError):
        GoldenProfile(name="../../etc/passwd")


def test_oversized_file_is_refused(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "big.json"
    path.write_text("x" * (storage.MAX_PROFILE_BYTES + 1), encoding="utf-8")
    with pytest.raises(ProfileSchemaError) as excinfo:
        storage.load(path)
    assert "bytes" in excinfo.value.reason


def test_invalid_json_is_refused(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ProfileSchemaError) as excinfo:
        storage.load(path)
    assert "not valid JSON" in excinfo.value.reason


def test_non_object_json_is_refused(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "list.json"
    path.write_text("[1,2,3]", encoding="utf-8")
    with pytest.raises(ProfileSchemaError):
        storage.load(path)


def test_missing_file_is_refused(tmp_path: pathlib.Path) -> None:
    with pytest.raises(ProfileSchemaError):
        storage.load(tmp_path / "nope.json")


def test_rejection_message_is_actionable(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "p.json"
    path.write_text(json.dumps({"schema_version": 99}), encoding="utf-8")
    with pytest.raises(ProfileSchemaError) as excinfo:
        storage.load(path)
    assert excinfo.value.remedy
    assert "Причина:" in excinfo.value.user_message()


# -- hostile files stay inside the ProfileSchemaError contract (finding 11) -


def test_deeply_nested_json_is_refused_cleanly(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "deep.json"
    path.write_text("[" * 100_000 + "]" * 100_000, encoding="utf-8")
    with pytest.raises(ProfileSchemaError) as excinfo:
        storage.load(path)
    assert "nested too deeply" in excinfo.value.reason


def test_non_utf8_file_is_refused_cleanly(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "bytes.json"
    path.write_bytes(b'{"name": "\xff\xfe"}')
    with pytest.raises(ProfileSchemaError) as excinfo:
        storage.load(path)
    assert "not UTF-8" in excinfo.value.reason
