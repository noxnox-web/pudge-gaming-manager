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
                        title=f"GPU is running at {temperature:.0f}°C",
                        detail=(
                            f"{gpu.model} is at {temperature:.0f}°C. The driver "
                            f"reports its maximum operating temperature as "
                            f"{threshold.warning:.0f}°C and starts reducing clocks "
                            f"at {threshold.critical:.0f}°C. Check case airflow "
                            "and dust build-up."
                        ),
                        severity=(
                            Severity.CRITICAL
                            if status is HealthStatus.CRITICAL
                            else Severity.WARNING
                        ),
                        subsystem="gpu",
                        fixable=False,
                        fix_hint="Clean dust filters and check case fans.",
                        threshold_kind=ThresholdKind.VENDOR,
                    )
                )

        if gpu.thermal_throttling:
            found.append(
                Issue(
                    id=f"gpu.thermal_throttle.{gpu.model}",
                    title="GPU is thermally throttling right now",
                    detail=(
                        f"The driver is reducing {gpu.model} clocks because of "
                        "temperature. Frame rates will be lower and less "
                        "consistent than this card is capable of. Reported "
                        f"reasons: {', '.join(gpu.throttle_reasons)}."
                    ),
                    severity=Severity.CRITICAL,
                    subsystem="gpu",
                    fixable=False,
                    fix_hint="Improve cooling; software cannot fix this.",
                    threshold_kind=ThresholdKind.VENDOR,
                )
            )
        elif gpu.power_throttling:
            found.append(
                Issue(
                    id=f"gpu.power_throttle.{gpu.model}",
                    title="GPU is limited by its power cap",
                    detail=(
                        f"{gpu.model} is drawing "
                        f"{gpu.power_watts.display(precision=0)} against a limit "
                        f"of {gpu.power_limit_watts.display(precision=0)}. This is "
                        "normal under sustained full load; persistent power "
                        "braking at idle suggests a power-delivery problem."
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
            where = "System drive" if disk.is_system_disk else "Drive"
            found.append(
                Issue(
                    id=f"storage.low_free_space.{disk.device_id}",
                    title=f"{where} {disk.device_id} is low on space",
                    detail=(
                        f"{disk.free_gb.display(precision=1)} free of "
                        f"{disk.total_gb.display(precision=1)} ({free:.0f}%). "
                        "A nearly full system drive causes stutter, failed "
                        "shader-cache writes and failed Windows updates."
                    ),
                    severity=severity,
                    subsystem="storage",
                    fixable=True,
                    fix_hint="Run the Cleaner to remove temporary files.",
                    threshold_kind=ThresholdKind.HEURISTIC,
                )
            )

        if disk.smart_healthy.value is False:
            found.append(
                Issue(
                    id=f"storage.smart_unhealthy.{disk.device_id}",
                    title=f"Drive {disk.device_id} reports a hardware problem",
                    detail=(
                        f"Windows reports this disk as not healthy "
                        f"({disk.model or 'unknown model'}). Back up its "
                        "contents and plan a replacement."
                    ),
                    severity=Severity.CRITICAL,
                    subsystem="storage",
                    fixable=False,
                    fix_hint="Replace the drive. Software cannot repair this.",
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
                    f"Display is running at {monitor.current_mode.refresh_hz} Hz "
                    f"instead of {best} Hz"
                ),
                detail=(
                    f"{monitor.friendly_name or monitor.device_name} supports "
                    f"{best} Hz at the current resolution "
                    f"({monitor.current_mode.width}x{monitor.current_mode.height}) "
                    f"but is set to {monitor.current_mode.refresh_hz} Hz."
                ),
                severity=Severity.WARNING,
                subsystem="display",
                fixable=True,
                fix_hint=f"Set the refresh rate to {best} Hz.",
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
            title=f"Memory is {usage:.0f}% used with no game running",
            detail=(
                "High memory use before a session starts leaves little "
                "headroom. Check background applications and startup items."
            ),
            severity=Severity.WARNING,
            subsystem="memory",
            fixable=False,
            fix_hint="Review Startup and Background Apps.",
            threshold_kind=ThresholdKind.HEURISTIC,
        )
    ]


def _scan_issues(snapshot: HardwareSnapshot) -> list[Issue]:
    """Surface scan failures rather than hiding them behind blank fields."""
    return [
        Issue(
            id=f"scan.warning.{index}",
            title="Part of the system could not be read",
            detail=warning,
            severity=Severity.INFO,
            subsystem="scan",
            fixable=False,
            fix_hint="Run as Administrator if this persists.",
        )
        for index, warning in enumerate(snapshot.warnings)
    ]
