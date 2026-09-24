"""Issue detection: turning a scan into a list of actionable findings.

An issue is reported only when PGM can say what is wrong, why it matters, and
whether it can fix it. An unreadable value produces **no issue** — absence of
data is not evidence of a problem (rule #64).
"""

from __future__ import annotations

from ...hardware.models import HardwareSnapshot, HealthStatus, ThresholdKind
from ...network.models import NetworkSnapshot
from .model import Issue, Severity
from .network import detect_network_issues


#: Free-space thresholds for the system disk, as percentages.
#: Heuristic, not vendor-documented — labelled as such in the UI.
DISK_FREE_WARNING_PERCENT = 15.0
DISK_FREE_CRITICAL_PERCENT = 8.0

#: Memory pressure at idle.
RAM_USAGE_WARNING_PERCENT = 85.0


def detect_issues(
    snapshot: HardwareSnapshot, network: NetworkSnapshot | None = None
) -> list[Issue]:
    """Return every finding in the scan, most severe first."""
    issues: list[Issue] = []
    if network is not None:
        issues.extend(detect_network_issues(network))
    issues.extend(_gpu_issues(snapshot))
    issues.extend(_disk_issues(snapshot))
    issues.extend(_display_issues(snapshot))
    issues.extend(_memory_issues(snapshot))
    issues.extend(_scan_issues(snapshot))

    order = {Severity.CRITICAL: 0, Severity.WARNING: 1, Severity.INFO: 2}
    return sorted(issues, key=lambda i: order[i.severity])


def _gpu_issues(snapshot: HardwareSnapshot) -> list[Issue]:
    """GPU findings, graded against the driver's own documented limits.

    Note that an idle GPU reports reduced clocks as a "throttle reason".
    That is correct behaviour, not a fault, and NVML's idle bit is excluded
    from throttle detection upstream — otherwise every healthy PC at the
    desktop would report a problem.
    """
    found: list[Issue] = []
    for gpu in snapshot.gpus:
        threshold = gpu.temperature_threshold
        temperature = gpu.temperature_c.value

        if threshold is not None and temperature is not None:
            status = threshold.evaluate(temperature)
            if status.is_actionable:
                found.append(
                    Issue(
                        id=f"gpu.temperature.{gpu.model}",
                        title=f"Видеокарта греется до {temperature:.0f}°C",
                        detail=(
                            f"{gpu.model}: {temperature:.0f}°C. Драйвер сообщает, что "
                            f"максимальная рабочая температура "
                            f"{threshold.warning:.0f}°C, а снижение частот "
                            f"начинается при {threshold.critical:.0f}°C. Проверьте "
                            "продув корпуса и запылённость."
                        ),
                        severity=(
                            Severity.CRITICAL
                            if status is HealthStatus.CRITICAL
                            else Severity.WARNING
                        ),
                        subsystem="gpu",
                        fixable=False,
                        fix_hint="Очистите пылевые фильтры и проверьте вентиляторы корпуса.",
                        threshold_kind=ThresholdKind.VENDOR,
                    )
                )

        if gpu.thermal_throttling:
            found.append(
                Issue(
                    id=f"gpu.thermal_throttle.{gpu.model}",
                    title="Видеокарта прямо сейчас в тепловом троттлинге",
                    detail=(
                        f"Драйвер снижает частоты {gpu.model} из-за "
                        "температуры. Частота кадров будет ниже и менее "
                        "стабильной, чем способна эта видеокарта. Причины: "
                        f"{', '.join(gpu.throttle_reasons)}."
                    ),
                    severity=Severity.CRITICAL,
                    subsystem="gpu",
                    fixable=False,
                    fix_hint="Улучшите охлаждение; программно это не исправить.",
                    threshold_kind=ThresholdKind.VENDOR,
                )
            )
        elif gpu.power_throttling:
            found.append(
                Issue(
                    id=f"gpu.power_throttle.{gpu.model}",
                    title="Видеокарта упирается в лимит питания",
                    detail=(
                        f"{gpu.model} потребляет "
                        f"{gpu.power_watts.display(precision=0)} при лимите "
                        f"{gpu.power_limit_watts.display(precision=0)}. Это "
                        "нормально под полной нагрузкой; постоянное ограничение "
                        "в простое указывает на проблему питания."
                    ),
                    severity=Severity.INFO,
                    subsystem="gpu",
                    fixable=False,
                    threshold_kind=ThresholdKind.VENDOR,
                )
            )
    return found


def _disk_issues(snapshot: HardwareSnapshot) -> list[Issue]:
    found: list[Issue] = []
    for disk in snapshot.disks:
        free = disk.free_percent.value
        if free is None:
            continue  # unreadable is not a finding

        if free <= DISK_FREE_CRITICAL_PERCENT:
            severity = Severity.CRITICAL
        elif free <= DISK_FREE_WARNING_PERCENT:
            severity = Severity.WARNING
        else:
            severity = None  # type: ignore[assignment]

        if severity is not None:
            where = "Системный диск" if disk.is_system_disk else "Диск"
            found.append(
                Issue(
                    id=f"storage.low_free_space.{disk.device_id}",
                    title=f"{where} {disk.device_id}: мало места",
                    detail=(
                        f"{disk.free_gb.display(precision=1)} свободно из "
                        f"{disk.total_gb.display(precision=1)} ({free:.0f}%). "
                        "Почти заполненный системный диск вызывает подтормаживания, "
                        "сбои записи кэша шейдеров и обновлений Windows."
                    ),
                    severity=severity,
                    subsystem="storage",
                    fixable=True,
                    fix_hint="Запустите очистку временных файлов.",
                    threshold_kind=ThresholdKind.HEURISTIC,
                )
            )

        if disk.smart_healthy.value is False:
            found.append(
                Issue(
                    id=f"storage.smart_unhealthy.{disk.device_id}",
                    title=f"Диск {disk.device_id} сообщает об аппаратной проблеме",
                    detail=(
                        f"Windows считает этот диск неисправным "
                        f"({disk.model or 'модель неизвестна'}). Сделайте резервную "
                        f"копию его "
                        "содержимое и запланируйте замену."
                    ),
                    severity=Severity.CRITICAL,
                    subsystem="storage",
                    fixable=False,
                    fix_hint="Замените диск. Программно это не чинится.",
                    threshold_kind=ThresholdKind.VENDOR,
                )
            )
    return found


def _display_issues(snapshot: HardwareSnapshot) -> list[Issue]:
    found: list[Issue] = []
    for monitor in snapshot.monitors:
        if not monitor.is_running_below_capability or monitor.current_mode is None:
            continue
        best = monitor.max_refresh_at_current_resolution.value
        found.append(
            Issue(
                id=f"display.below_max_refresh.{monitor.device_name}",
                title=(
                    f"Монитор работает на {monitor.current_mode.refresh_hz} Гц "
                    f"вместо {best} Гц"
                ),
                detail=(
                    f"{monitor.friendly_name or monitor.device_name} поддерживает "
                    f"{best} Гц на текущем разрешении "
                    f"({monitor.current_mode.width}x{monitor.current_mode.height}), "
                    f"но выставлено {monitor.current_mode.refresh_hz} Гц."
                ),
                severity=Severity.WARNING,
                subsystem="display",
                fixable=True,
                fix_hint=f"Установите частоту обновления {best} Гц.",
                threshold_kind=ThresholdKind.DERIVED,
            )
        )
    return found


def _memory_issues(snapshot: HardwareSnapshot) -> list[Issue]:
    usage = snapshot.ram.usage_percent.value
    if usage is None or usage < RAM_USAGE_WARNING_PERCENT:
        return []
    return [
        Issue(
            id="memory.high_usage",
            title=f"Память занята на {usage:.0f}% без запущенной игры",
            detail=(
                "Высокое использование памяти до начала сессии оставляет мало "
                "запаса. Проверьте фоновые приложения и автозагрузку."
            ),
            severity=Severity.WARNING,
            subsystem="memory",
            fixable=False,
            fix_hint="Проверьте автозагрузку и фоновые приложения.",
            threshold_kind=ThresholdKind.HEURISTIC,
        )
    ]


def _scan_issues(snapshot: HardwareSnapshot) -> list[Issue]:
    """Surface scan failures rather than hiding them behind blank fields."""
    return [
        Issue(
            id=f"scan.warning.{index}",
            title="Часть системы не удалось прочитать",
            detail=warning,
            severity=Severity.INFO,
            subsystem="scan",
            fixable=False,
            fix_hint="Запустите от администратора, если повторяется.",
        )
        for index, warning in enumerate(snapshot.warnings)
    ]
