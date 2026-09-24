"""Turning a scan into display strings.

Pure functions, no Qt. Extracted from the dashboard so the formatting rules
— which value leads, when something reads ``UNAVAILABLE``, what goes in the
tooltip — are testable without constructing a window.

The presentation rules enforced here:

* An unreadable value renders ``UNAVAILABLE`` with its reason in the
  tooltip. Never ``0``, never a placeholder that looks like a measurement.
* A value with a vendor threshold is graded by that threshold, not by a
  number invented in the view layer.
"""

from __future__ import annotations

import pathlib
from dataclasses import dataclass

from ...core.profiles.golden import ComparisonResult
from ...core.diagnostics.network import detect_network_issues
from ...core.types import HardwareSnapshot, NetworkSnapshot

UNAVAILABLE = "НЕТ ДАННЫХ"  # display value; status codes below stay English

#: Vendor decoration that consumes width without informing anyone.
_MODEL_NOISE = ("(R)", "(r)", "(TM)", "(tm)", "(C)", " CPU")

#: Heuristics used only for the colour of a status dot. The authoritative
#: grading lives in core/diagnostics; these keep the dot consistent with it.
_CPU_BUSY_WARNING = 60.0
_RAM_USAGE_WARNING = 85.0
_DISK_FREE_WARNING = 15.0
_DISK_FREE_CRITICAL = 8.0


@dataclass(frozen=True, slots=True)
class MetricView:
    """One dashboard row, ready to render."""

    value: str
    status: str
    detail: str = ""
    tooltip: str = ""


def tidy_model(name: str) -> str:
    """Strip vendor decoration from a hardware model string.

    "11th Gen Intel(R) Core(TM) i5-11400F @ 2.60GHz" is mostly punctuation;
    the trademark marks push the useful part out of the visible width.
    """
    cleaned = name
    for noise in _MODEL_NOISE:
        cleaned = cleaned.replace(noise, "")
    return " ".join(cleaned.split())


def elide(text: str, limit: int = 44) -> str:
    """Shorten from the right so the beginning stays readable."""
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def cpu_view(snapshot: HardwareSnapshot) -> MetricView:
    cpu = snapshot.cpu
    utilization = cpu.utilization_percent.value
    if utilization is None:
        return MetricView(
            UNAVAILABLE, "UNAVAILABLE", tidy_model(cpu.model),
            cpu.utilization_percent.unavailable_reason,
        )
    return MetricView(
        f"{utilization:.0f}%",
        "GOOD" if utilization < _CPU_BUSY_WARNING else "WARNING",
        tidy_model(cpu.model),
        f"Температура: {cpu.temperature_c.unavailable_reason}",
    )


def gpu_view(snapshot: HardwareSnapshot) -> MetricView:
    """GPU row.

    Temperature leads once NVML can measure it, because a thermal problem is
    the thing an administrator most needs to see. Without telemetry the row
    falls back to VRAM capacity rather than showing a blank.
    """
    gpu = snapshot.primary_gpu
    if gpu is None:
        return MetricView(UNAVAILABLE, "UNAVAILABLE", "видеокарта не найдена")

    temperature = gpu.temperature_c
    vram = gpu.vram_total_mb

    if temperature.available:
        value = temperature.display()
        status = gpu.temperature_status.value
    else:
        value = f"{vram.value // 1024} ГБ" if vram.value else UNAVAILABLE
        status = "GOOD" if vram.available else "UNAVAILABLE"

    parts = [tidy_model(gpu.model)]
    if gpu.utilization_percent.available:
        parts.append(f"загрузка {gpu.utilization_percent.display()}")
    if vram.available and gpu.vram_used_mb.available:
        parts.append(f"{gpu.vram_used_mb.value // 1024}/{vram.value // 1024} ГБ")

    tooltip = [f"Драйвер {gpu.driver_version} ({gpu.driver_date})"]
    if gpu.temperature_threshold is not None:
        tooltip.append(gpu.temperature_threshold.describe())
    else:
        tooltip.append(f"Температура: {temperature.unavailable_reason}")
    if gpu.power_watts.available:
        tooltip.append(
            f"Питание {gpu.power_watts.display(precision=0)} из "
            f"{gpu.power_limit_watts.display(precision=0)}"
        )
    if gpu.clock_sm_mhz.available:
        tooltip.append(
            f"Частота {gpu.clock_sm_mhz.display()} из {gpu.max_clock_sm_mhz.display()}"
        )
    if gpu.throttle_reasons:
        tooltip.append(f"Троттлинг: {', '.join(gpu.throttle_reasons)}")

    return MetricView(value, status, " · ".join(parts), "\n".join(tooltip))


def ram_view(snapshot: HardwareSnapshot) -> MetricView:
    ram = snapshot.ram
    if not ram.total_mb.available:
        return MetricView(
            UNAVAILABLE, "UNAVAILABLE", tooltip=ram.total_mb.unavailable_reason
        )
    usage = ram.usage_percent.value or 0.0
    return MetricView(
        f"{(ram.used_mb.value or 0) / 1024:.1f} / {ram.total_mb.value / 1024:.1f} ГБ",
        "GOOD" if usage < _RAM_USAGE_WARNING else "WARNING",
        f"{ram.module_count} модулей · {ram.speed_mhz.display()}",
        _ram_tooltip(ram),
    )


def _ram_tooltip(ram: object) -> str:
    modules = getattr(ram, "modules", ())
    if not modules:
        return ""
    return "\n".join(
        f"{m.manufacturer} {m.capacity_mb} MB @ {m.speed_mhz} MHz "
        f"({m.part_number})".strip()
        for m in modules
    )


def disk_view(snapshot: HardwareSnapshot) -> MetricView:
    disk = snapshot.system_disk
    if disk is None:
        return MetricView(UNAVAILABLE, "UNAVAILABLE", "системный диск не найден")

    free = disk.free_percent.value
    if free is None:
        return MetricView(
            UNAVAILABLE, "UNAVAILABLE", disk.device_id,
            disk.free_percent.unavailable_reason,
        )

    if free > _DISK_FREE_WARNING:
        status = "GOOD"
    elif free > _DISK_FREE_CRITICAL:
        status = "WARNING"
    else:
        status = "CRITICAL"

    tooltip = f"{disk.model}\nSMART в норме: {disk.smart_healthy.display()}"
    return MetricView(
        f"{free:.0f}% свободно",
        status,
        f"{disk.device_id} {disk.media_type} · "
        f"{disk.free_gb.display(precision=0)} из {disk.total_gb.display(precision=0)}",
        tooltip,
    )


def display_view(snapshot: HardwareSnapshot) -> MetricView:
    monitor = snapshot.primary_monitor
    if monitor is None or monitor.current_mode is None:
        return MetricView(UNAVAILABLE, "UNAVAILABLE", "монитор не найден")

    best = monitor.max_refresh_at_current_resolution.value
    below = monitor.is_running_below_capability
    return MetricView(
        f"{monitor.current_mode.refresh_hz} Гц",
        "WARNING" if below else "GOOD",
        f"{monitor.current_mode.width}x{monitor.current_mode.height}"
        + (f" · макс {best} Гц" if below else ""),
        monitor.friendly_name,
    )


def network_view(network: NetworkSnapshot | None) -> MetricView:
    """Network row: the physical link leads, latency and path follow.

    The dot takes the worst network finding, so the row can never look
    healthier (or worse) than the findings list beside it.
    """
    if network is None:
        return MetricView(UNAVAILABLE, "UNAVAILABLE", "не сканировалось")
    if network.unavailable_reason:
        return MetricView(
            UNAVAILABLE, "UNAVAILABLE", "не удалось прочитать", network.unavailable_reason
        )
    if not network.connected:
        return MetricView("НЕТ СЕТИ", "CRITICAL", "нет маршрута наружу")

    uplink = network.uplink
    parts: list[str] = []
    if uplink is not None:
        value = _link_speed(uplink.link_speed_mbps) or uplink.kind_label
        parts.append(uplink.kind_label)
    else:
        value = UNAVAILABLE
    if network.gateway_ping is not None and network.gateway_ping.trustworthy:
        average = network.gateway_ping.avg_ms
        if average is not None:
            parts.append("роутер <1 мс" if average < 1 else f"роутер {average:.0f} мс")
    if network.path is None and network.gateway is None:
        parts.append("нет интернета")
    elif network.tunnelled and network.path is not None:
        parts.append(f"через {network.path.name}")
    elif network.internet_ping is not None and network.internet_ping.trustworthy:
        average = network.internet_ping.avg_ms
        if average is not None:
            parts.append(f"интернет {average:.0f} мс")

    tooltip: list[str] = []
    if uplink is not None:
        tooltip.append(f"Подключение: {uplink.name} — {uplink.description}")
    if network.gateway:
        tooltip.append(f"Шлюз: {network.gateway}")
    if network.path is not None and network.tunnelled:
        tooltip.append(
            f"Трафик в интернет идёт через: {network.path.name} — "
            f"{network.path.description}"
        )
    for stats in (network.gateway_ping, network.internet_ping):
        if stats is not None:
            tooltip.append(f"{stats.label.capitalize()}: {stats.summary()}")
    tooltip.extend(network.warnings)

    severities = {i.severity.value for i in detect_network_issues(network)}
    status = next(
        (s for s in ("CRITICAL", "WARNING") if s in severities), "GOOD"
    )
    return MetricView(value, status, " · ".join(parts), "\n".join(tooltip))


def _link_speed(mbps: int | None) -> str:
    if mbps is None:
        return ""
    return f"{mbps // 1000} Гбит/с" if mbps >= 1000 and mbps % 1000 == 0 else f"{mbps} Мбит/с"


#: Row key -> presenter, in display order.
METRIC_VIEWS = {
    "cpu": cpu_view,
    "gpu": gpu_view,
    "ram": ram_view,
    "disk": disk_view,
    "display": display_view,
}
"""Hardware rows. The network row renders from the network scan instead."""


def profile_name_from_path(path: str) -> str:
    """Name a profile after the file the operator chose.

    A file name already excludes every character the schema forbids in a
    profile name, so this needs no second prompt and cannot be rejected.
    """
    stem = pathlib.PurePath(path).stem.strip()
    return stem[:120] or "Профиль клуба"


def difference_views(result: ComparisonResult) -> tuple[MetricView, ...]:
    """One row per difference, drift first.

    A value that could not be read is grey, never a warning: missing data
    is not evidence that the PC differs (rule #64).

    ``fixable`` means PGM ships a tweak for the setting, not that any button
    here applies it: OPTIMIZE PC acts on scan findings, not on profile
    drift, and restoring a profile is not built. The hint says exactly that.
    """
    rows: list[MetricView] = []
    for diff in result.drifted:
        hint = (
            "У PGM есть твик для этой настройки; восстановление из "
            "профиля пока не реализовано."
            if diff.fixable
            else "PGM не может это изменить; нужно вручную."
        )
        rows.append(MetricView(diff.line(), "WARNING", hint, diff.detail))
    for diff in result.unknown:
        rows.append(MetricView(diff.line(), "UNAVAILABLE"))
    return tuple(rows)
