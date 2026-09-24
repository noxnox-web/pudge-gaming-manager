"""Tests for NVML telemetry.

Two halves: pure logic tested with constructed values, and live tests that
run only when an NVIDIA GPU is actually present. The live tests are read-only
— NVML is a read-only interface, so there is nothing here that can change the
machine.
"""

from __future__ import annotations

import pytest

from pudge_gaming_manager.hardware.gpu import nvml
from pudge_gaming_manager.hardware.models import (
    HealthStatus,
    Reading,
    ThresholdKind,
)

has_nvidia = nvml.is_available()
nvidia_only = pytest.mark.skipif(
    not has_nvidia, reason="no NVIDIA GPU or driver on this machine"
)


# -- throttle interpretation ----------------------------------------------


def test_idle_is_not_treated_as_throttling() -> None:
    """An idle GPU reports reduced clocks; that is correct, not a fault.

    Counting the idle bit would make every healthy PC at the desktop report
    a thermal problem.
    """
    telemetry = nvml.GpuTelemetry(index=0, throttle_reasons=("idle",))
    assert not telemetry.is_thermally_throttling
    assert not telemetry.is_power_throttling
    assert telemetry.is_idle


def test_thermal_reasons_are_detected() -> None:
    for reason in ("software thermal slowdown", "hardware thermal slowdown"):
        telemetry = nvml.GpuTelemetry(index=0, throttle_reasons=(reason,))
        assert telemetry.is_thermally_throttling, reason
        assert not telemetry.is_power_throttling


def test_power_reasons_are_detected() -> None:
    for reason in ("software power cap", "hardware power brake"):
        telemetry = nvml.GpuTelemetry(index=0, throttle_reasons=(reason,))
        assert telemetry.is_power_throttling, reason
        assert not telemetry.is_thermally_throttling


def test_no_reasons_means_no_throttling() -> None:
    telemetry = nvml.GpuTelemetry(index=0)
    assert not telemetry.is_thermally_throttling
    assert not telemetry.is_power_throttling


# -- derived values --------------------------------------------------------


def test_vram_percent_is_derived_from_totals() -> None:
    telemetry = nvml.GpuTelemetry(
        index=0,
        vram_total_mb=Reading(12288, unit=" MB"),
        vram_used_mb=Reading(3072, unit=" MB"),
    )
    assert telemetry.vram_used_percent.value == pytest.approx(25.0)


def test_vram_percent_unavailable_without_totals() -> None:
    telemetry = nvml.GpuTelemetry(index=0, vram_used_mb=Reading(3072))
    assert not telemetry.vram_used_percent.available
    assert telemetry.vram_used_percent.unavailable_reason


# -- vendor thresholds -----------------------------------------------------


def test_temperature_threshold_grading() -> None:
    from pudge_gaming_manager.hardware.models import Threshold

    threshold = Threshold(
        warning=93.0, critical=95.0, kind=ThresholdKind.VENDOR, source="driver"
    )
    assert threshold.evaluate(40.0) is HealthStatus.GOOD
    assert threshold.evaluate(93.0) is HealthStatus.WARNING
    assert threshold.evaluate(96.0) is HealthStatus.CRITICAL
    assert threshold.evaluate(None) is HealthStatus.UNAVAILABLE


def test_vendor_threshold_is_labelled_as_vendor() -> None:
    from pudge_gaming_manager.hardware.models import Threshold

    threshold = Threshold(
        warning=93.0, critical=95.0, kind=ThresholdKind.VENDOR,
        source="NVIDIA driver limits",
    )
    assert "предел производителя" in threshold.describe()
    assert "heuristic" not in threshold.describe()


# -- graceful absence ------------------------------------------------------


def test_session_without_nvml_reports_a_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A machine with no NVIDIA GPU must degrade, not crash."""
    monkeypatch.setattr(nvml, "_NVML_IMPORTED", False)
    with nvml.NvmlSession() as session:
        assert not session.available
        assert session.unavailable_reason
        assert session.read_all() == []


def test_read_telemetry_returns_reason_when_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(nvml, "_NVML_IMPORTED", False)
    telemetry, reason = nvml.read_telemetry()
    assert telemetry == []
    assert reason


# -- live (read-only) ------------------------------------------------------


@nvidia_only
def test_live_reads_one_gpu() -> None:
    telemetry, reason = nvml.read_telemetry()
    assert telemetry, reason
    assert telemetry[0].name


@nvidia_only
def test_live_temperature_is_plausible() -> None:
    telemetry, _ = nvml.read_telemetry()
    temperature = telemetry[0].temperature_c.value
    assert temperature is not None
    assert 10 <= temperature <= 110, f"implausible GPU temperature {temperature}"


@nvidia_only
def test_live_vram_total_exceeds_the_broken_wmi_field() -> None:
    """The whole reason NVML is used: WMI saturates AdapterRAM at 4 GB."""
    telemetry, _ = nvml.read_telemetry()
    total = telemetry[0].vram_total_mb.value
    assert total is not None and total > 0
    assert telemetry[0].vram_total_mb.source == "NVML"


@nvidia_only
def test_live_used_vram_does_not_exceed_total() -> None:
    telemetry, _ = nvml.read_telemetry()
    gpu = telemetry[0]
    assert gpu.vram_used_mb.value is not None
    assert gpu.vram_used_mb.value <= gpu.vram_total_mb.value  # type: ignore[operator]


@nvidia_only
def test_live_utilization_is_a_percentage() -> None:
    telemetry, _ = nvml.read_telemetry()
    utilization = telemetry[0].utilization_percent.value
    assert utilization is not None and 0 <= utilization <= 100


@nvidia_only
def test_live_threshold_comes_from_the_driver() -> None:
    telemetry, _ = nvml.read_telemetry()
    threshold = telemetry[0].temperature_threshold
    assert threshold is not None
    assert threshold.kind is ThresholdKind.VENDOR
    assert threshold.warning <= threshold.critical
    assert 60 <= threshold.warning <= 110


@nvidia_only
def test_live_gpu_is_not_reported_as_throttling_at_idle() -> None:
    telemetry, _ = nvml.read_telemetry()
    gpu = telemetry[0]
    assert not gpu.is_thermally_throttling, gpu.throttle_reasons


@nvidia_only
def test_session_can_be_opened_repeatedly() -> None:
    """The GUI may rescan many times; init/shutdown must not leak."""
    for _ in range(5):
        with nvml.NvmlSession() as session:
            assert session.available
            assert session.read_all()
