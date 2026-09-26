"""Tests for dashboard formatting.

No Qt here — that is the point of extracting the presenters. These assert
the honesty rules the UI is supposed to enforce.
"""

from __future__ import annotations

import pytest

from pudge_gaming_manager.app.gui import presenters
from pudge_gaming_manager.core.types import AdapterInfo, LinkKind, NetworkSnapshot, PingStats
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
    Threshold,
    ThresholdKind,
)


def _snapshot(**kwargs) -> HardwareSnapshot:
    base = {
        "captured_at": "2026-09-20T00:00:00+00:00",
        "os": OsInfo(),
        "cpu": CpuInfo(),
        "gpus": (),
        "ram": RamInfo(),
        "disks": (),
        "monitors": (),
    }
    base.update(kwargs)
    return HardwareSnapshot(**base)  # type: ignore[arg-type]


# -- string tidying --------------------------------------------------------


def test_model_noise_is_stripped() -> None:
    assert (
        presenters.tidy_model("11th Gen Intel(R) Core(TM) i5-11400F @ 2.60GHz")
        == "11th Gen Intel Core i5-11400F @ 2.60GHz"
    )


def test_tidying_collapses_whitespace() -> None:
    assert presenters.tidy_model("AMD   Ryzen  5  ") == "AMD Ryzen 5"


def test_module_count_takes_the_russian_plural() -> None:
    from pudge_gaming_manager.utilities.formatting import plural_ru

    forms = ("модуль", "модуля", "модулей")
    assert [plural_ru(n, *forms) for n in (1, 2, 4, 5, 11, 21, 22, 112)] == [
        "1 модуль", "2 модуля", "4 модуля", "5 модулей",
        "11 модулей", "21 модуль", "22 модуля", "112 модулей",
    ]


# -- the honesty rule ------------------------------------------------------


def test_missing_cpu_reading_says_unavailable_not_zero() -> None:
    view = presenters.cpu_view(_snapshot(cpu=CpuInfo(model="Test CPU")))
    assert view.value == presenters.UNAVAILABLE
    assert view.status == "UNAVAILABLE"
    assert "0" not in view.value


def test_absent_gpu_is_not_an_error_state() -> None:
    view = presenters.gpu_view(_snapshot())
    assert view.value == presenters.UNAVAILABLE
    assert view.status == "UNAVAILABLE"


def test_unreadable_network_row_says_so() -> None:
    view = presenters.network_view(NetworkSnapshot(unavailable_reason="PowerShell timed out"))
    assert view.value == presenters.UNAVAILABLE
    assert view.status == "UNAVAILABLE"
    assert "timed out" in view.tooltip


def test_disconnected_network_row_is_critical() -> None:
    view = presenters.network_view(NetworkSnapshot())
    assert (view.value, view.status) == ("НЕТ СЕТИ", "CRITICAL")


def test_tunnelled_network_row_leads_with_the_real_link() -> None:
    nic = AdapterInfo(7, "Ethernet", "Realtek", LinkKind.WIRED, 1_000_000_000, True, False)
    vpn = AdapterInfo(12, "DurevVPN", "sing-tun", LinkKind.OTHER, 10**11, True, True)
    network = NetworkSnapshot(
        uplink=nic, gateway="192.168.100.1", path=vpn,
        gateway_ping=PingStats("192.168.100.1", "gateway", 10, (0.0,) * 10),
        internet_ping=PingStats("1.1.1.1", "internet reference", 0, (),
                                unreliable_reason="the tunnel answers ping"),
    )
    view = presenters.network_view(network)
    assert view.value == "1 Гбит/с"  # not the tunnel's 100 Gbps
    assert "через DurevVPN" in view.detail
    assert "роутер <1 мс" in view.detail
    # Status matches the findings list: the tunnel is a warning there.
    assert view.status == "WARNING"
    assert "не измеряется" in view.tooltip or "not measurable" in view.tooltip


def test_unreadable_disk_does_not_render_as_full() -> None:
    disk = DiskInfo(device_id="C:", is_system_disk=True)
    view = presenters.disk_view(_snapshot(disks=(disk,)))
    assert view.value == presenters.UNAVAILABLE
    assert view.status == "UNAVAILABLE"


# -- GPU: temperature leads when measurable -------------------------------


def _gpu_with_temperature(temperature: float) -> GpuInfo:
    return GpuInfo(
        vendor="NVIDIA",
        model="NVIDIA GeForce RTX 3060",
        vram_total_mb=Reading(12288, unit=" MB"),
        vram_used_mb=Reading(2048, unit=" MB"),
        temperature_c=Reading(temperature, unit="°C"),
        utilization_percent=Reading(30.0, unit="%"),
        temperature_threshold=Threshold(
            warning=93.0, critical=95.0, kind=ThresholdKind.VENDOR,
            source="NVIDIA driver limits",
        ),
    )


def test_gpu_shows_temperature_when_nvml_is_available() -> None:
    view = presenters.gpu_view(_snapshot(gpus=(_gpu_with_temperature(41.0),)))
    assert "41" in view.value
    assert view.status == "GOOD"


def test_gpu_temperature_graded_by_the_vendor_threshold() -> None:
    warm = presenters.gpu_view(_snapshot(gpus=(_gpu_with_temperature(94.0),)))
    hot = presenters.gpu_view(_snapshot(gpus=(_gpu_with_temperature(97.0),)))
    assert warm.status == "WARNING"
    assert hot.status == "CRITICAL"


def test_gpu_tooltip_cites_the_vendor_threshold() -> None:
    view = presenters.gpu_view(_snapshot(gpus=(_gpu_with_temperature(41.0),)))
    assert "предел производителя" in view.tooltip


def test_gpu_falls_back_to_vram_without_telemetry() -> None:
    gpu = GpuInfo(
        vendor="AMD",
        model="AMD Radeon RX 7800",
        vram_total_mb=Reading(16384, unit=" MB"),
        temperature_c=Reading.unavailable("no documented interface", unit="°C"),
    )
    view = presenters.gpu_view(_snapshot(gpus=(gpu,)))
    assert view.value == "16 ГБ"
    assert "no documented interface" in view.tooltip


def test_throttle_reasons_reach_the_tooltip() -> None:
    gpu = GpuInfo(
        vendor="NVIDIA", model="RTX 3060",
        vram_total_mb=Reading(12288),
        temperature_c=Reading(85.0, unit="°C"),
        throttle_reasons=("hardware thermal slowdown",),
        thermal_throttling=True,
    )
    view = presenters.gpu_view(_snapshot(gpus=(gpu,)))
    assert "hardware thermal slowdown" in view.tooltip


# -- disk / display --------------------------------------------------------


@pytest.mark.parametrize(
    ("free", "expected"),
    [(50.0, "GOOD"), (13.0, "WARNING"), (5.0, "CRITICAL")],
)
def test_disk_status_tracks_free_space(free: float, expected: str) -> None:
    disk = DiskInfo(
        device_id="C:",
        is_system_disk=True,
        media_type="SSD",
        free_percent=Reading(free, unit="%"),
        free_gb=Reading(free * 2, unit=" GB"),
        total_gb=Reading(200.0, unit=" GB"),
    )
    assert presenters.disk_view(_snapshot(disks=(disk,))).status == expected


def test_display_at_maximum_is_good() -> None:
    monitor = MonitorInfo(
        is_primary=True,
        current_mode=DisplayMode(1920, 1080, 240),
        max_refresh_at_current_resolution=Reading(240, unit=" Hz"),
    )
    view = presenters.display_view(_snapshot(monitors=(monitor,)))
    assert view.status == "GOOD"
    assert "240 Гц" in view.value
    assert "макс" not in view.detail


def test_display_below_capability_warns_and_names_the_maximum() -> None:
    monitor = MonitorInfo(
        is_primary=True,
        current_mode=DisplayMode(1920, 1080, 144),
        max_refresh_at_current_resolution=Reading(240, unit=" Hz"),
    )
    view = presenters.display_view(_snapshot(monitors=(monitor,)))
    assert view.status == "WARNING"
    assert "макс 240 Гц" in view.detail


def test_every_metric_key_has_a_presenter() -> None:
    snapshot = _snapshot()
    for key, presenter in presenters.METRIC_VIEWS.items():
        view = presenter(snapshot)
        assert view.value, f"{key} produced no value"


# -- golden profile comparison ------------------------------------------------


def test_profile_name_comes_from_the_file_name() -> None:
    assert presenters.profile_name_from_path(r"E:\club\Arena A.json") == "Arena A"
    assert len(presenters.profile_name_from_path("x" * 300 + ".json")) == 120


def test_difference_views_put_drift_first_and_grey_out_unknowns() -> None:
    from pudge_gaming_manager.core.profiles.golden import (
        ComparisonResult,
        Difference,
        DriftSeverity,
    )

    result = ComparisonResult(
        profile_name="Club",
        differences=(
            Difference("hardware", "min_ram_gb", 16, None, DriftSeverity.UNKNOWN,
                       "Installed memory could not be read."),
            Difference("power", "scheme_guid", "a", "b", DriftSeverity.DRIFT,
                       "Expected the 'High performance' plan.", fixable=True),
        ),
        checked=2,
    )
    views = presenters.difference_views(result)
    assert [v.status for v in views] == ["WARNING", "UNAVAILABLE"]
    # Must not promise a button that does not act on profile drift.
    assert "OPTIMIZE" not in views[0].detail
    assert "не реализовано" in views[0].detail
    assert "не удалось проверить" in views[1].value
