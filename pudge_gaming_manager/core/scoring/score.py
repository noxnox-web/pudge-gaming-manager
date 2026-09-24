"""Gaming Score — a transparent, explainable readiness indicator.

Rule #30 requires the formula to be visible and the weights configurable.
Rule #29 requires that the score is never presented as a frame-rate figure.

Unavailable inputs
------------------
A component PGM cannot measure is **excluded and the remaining weights are
renormalised**. Scoring it as zero would punish the PC for PGM's own blind
spot — on this build, GPU telemetry is out of scope, so a machine with a
perfectly healthy GPU would otherwise lose 25 points for no reason.

Every result carries its components, their weights and the list of excluded
inputs, so the UI can show the arithmetic instead of a mystery number.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from ...hardware.models import HardwareSnapshot, HealthStatus
from ...network.models import LinkKind, NetworkSnapshot

#: Default weights (rule #30). Configurable; they need not sum to 100 because
#: the result is normalised by the weights actually used.
DEFAULT_WEIGHTS: dict[str, float] = {
    "gpu": 25.0,
    "cpu": 20.0,
    "network": 15.0,
    "ram": 10.0,
    "storage": 10.0,
    "display": 10.0,
    "system": 10.0,
}


@dataclass(frozen=True, slots=True)
class Component:
    """One scored dimension."""

    key: str
    label: str
    score: float | None
    """0-100, or ``None`` when the inputs were unavailable."""

    weight: float
    explanation: str

    @property
    def available(self) -> bool:
        return self.score is not None


@dataclass(frozen=True, slots=True)
class GamingScore:
    """The overall figure plus everything needed to explain it."""

    value: float | None
    components: tuple[Component, ...] = ()
    excluded: tuple[str, ...] = ()

    @property
    def available(self) -> bool:
        return self.value is not None

    @property
    def effective_weight(self) -> float:
        return sum(c.weight for c in self.components if c.available)

    def display(self) -> str:
        return f"{self.value:.0f} / 100" if self.value is not None else "UNAVAILABLE"

    def formula_lines(self) -> list[str]:
        """The arithmetic, for display in the UI (rule #30)."""
        lines: list[str] = []
        total = self.effective_weight
        for component in self.components:
            if not component.available:
                lines.append(f"{component.label}: excluded — {component.explanation}")
                continue
            share = (component.weight / total * 100) if total else 0
            lines.append(
                f"{component.label}: {component.score:.0f}/100 "
                f"x {share:.0f}% — {component.explanation}"
            )
        if self.excluded:
            lines.append(
                "Excluded inputs are not counted as zero; the remaining "
                "weights are rescaled to 100%."
            )
        return lines


def _clamp(value: float) -> float:
    return max(0.0, min(100.0, value))


def score_cpu(snapshot: HardwareSnapshot, weight: float) -> Component:
    """Score CPU headroom from current utilization.

    Deliberately modest in ambition: without thermal data this measures
    whether the processor is currently swamped, not how good it is.
    """
    utilization = snapshot.cpu.utilization_percent.value
    if utilization is None:
        return Component("cpu", "CPU", None, weight, "utilization not readable")
    # 0% busy -> 100. 80%+ busy at idle is a real problem -> near 0.
    value = _clamp(100.0 - (utilization / 80.0) * 100.0)
    return Component(
        "cpu", "CPU", value, weight,
        f"{utilization:.0f}% busy (heuristic: 80%+ at idle scores 0)",
    )


def score_ram(snapshot: HardwareSnapshot, weight: float) -> Component:
    usage = snapshot.ram.usage_percent.value
    if usage is None:
        return Component("ram", "Memory", None, weight, "usage not readable")
    value = _clamp(100.0 - (usage - 40.0) * (100.0 / 50.0)) if usage > 40 else 100.0
    return Component(
        "ram", "Memory", value, weight,
        f"{usage:.0f}% in use (heuristic: 90%+ scores 0)",
    )


def score_storage(snapshot: HardwareSnapshot, weight: float) -> Component:
    """Score the system disk on free space and media type.

    Free space dominates: a nearly-full system drive causes stutter,
    failed shader-cache writes and failed updates.
    """
    disk = snapshot.system_disk
    if disk is None or disk.free_percent.value is None:
        return Component("storage", "Storage", None, weight, "no readable system disk")

    free = disk.free_percent.value
    # 20% free is the common guidance floor; below 10% is trouble.
    space_score = _clamp((free - 5.0) * (100.0 / 20.0))
    detail = f"{free:.0f}% free"

    if disk.media_type == "HDD":
        # A mechanical system disk is a genuine handicap for load times.
        space_score = min(space_score, 60.0)
        detail += ", mechanical system disk"
    elif disk.media_type == "SSD":
        detail += ", SSD"

    if disk.smart_healthy.value is False:
        space_score = 0.0
        detail += ", SMART reports a problem"

    return Component("storage", "Storage", space_score, weight,
                     f"{detail} (heuristic: 25%+ free scores 100)")


def score_display(snapshot: HardwareSnapshot, weight: float) -> Component:
    """Score displays on whether they run at their achievable refresh rate."""
    monitors = [m for m in snapshot.monitors if m.current_mode]
    if not monitors:
        return Component("display", "Display", None, weight, "no display detected")

    penalised: list[str] = []
    for monitor in monitors:
        if monitor.is_running_below_capability:
            best = monitor.max_refresh_at_current_resolution.value
            penalised.append(
                f"{monitor.current_mode.refresh_hz} Hz of {best} Hz"  # type: ignore[union-attr]
            )

    if not penalised:
        rates = ", ".join(f"{m.current_mode.refresh_hz} Hz" for m in monitors)  # type: ignore[union-attr]
        return Component("display", "Display", 100.0, weight,
                         f"running at maximum ({rates})")

    value = _clamp(100.0 - 50.0 * len(penalised))
    return Component("display", "Display", value, weight,
                     "below capability: " + "; ".join(penalised))


def score_gpu(snapshot: HardwareSnapshot, weight: float) -> Component:
    """Score the GPU on capacity and, where NVML is available, thermals.

    Temperature is graded against the driver's own limits, so the thermal
    part of this score carries vendor authority rather than a guess. A GPU
    that is actively throttling is capped hard: no amount of VRAM
    compensates for a card that cannot hold its clocks.
    """
    gpu = snapshot.primary_gpu
    if gpu is None:
        return Component("gpu", "GPU", None, weight, "no GPU detected")

    vram = gpu.vram_total_mb.value
    if vram is None:
        return Component(
            "gpu", "GPU", None, weight, "VRAM and telemetry both unreadable"
        )

    # 8 GB is a reasonable floor for current competitive titles at 1080p.
    value = _clamp((vram / 8192.0) * 100.0)
    notes = [f"{vram // 1024} GB VRAM"]

    threshold = gpu.temperature_threshold
    temperature = gpu.temperature_c.value
    if threshold is not None and temperature is not None:
        status = threshold.evaluate(temperature)
        notes.append(f"{temperature:.0f}°C ({threshold.describe()})")
        if status is HealthStatus.CRITICAL:
            value = min(value, 20.0)
        elif status is HealthStatus.WARNING:
            value = min(value, 60.0)
    else:
        notes.append("temperature unavailable")

    if gpu.thermal_throttling:
        value = min(value, 25.0)
        notes.append("thermally throttling now")
    elif gpu.power_throttling:
        value = min(value, 70.0)
        notes.append("power-limited now")

    return Component("gpu", "GPU", value, weight, ", ".join(notes))


def score_system(snapshot: HardwareSnapshot, weight: float) -> Component:
    """Penalise problems the scan itself encountered."""
    if snapshot.warnings:
        value = _clamp(100.0 - 25.0 * len(snapshot.warnings))
        return Component("system", "System", value, weight,
                         f"{len(snapshot.warnings)} scan warning(s)")
    return Component("system", "System", 100.0, weight, "scan completed cleanly")


def score_network(network: NetworkSnapshot | None, weight: float) -> Component:
    """Score the connection from what was actually measured.

    Every deduction is a PGM heuristic and is named in the explanation. A
    measurement that could not be trusted (a VPN answering ping itself, an
    ICMP filter) contributes nothing either way rather than a guess.
    """
    if network is None:
        return Component("network", "Network", None, weight, "network was not scanned")
    if network.unavailable_reason:
        return Component("network", "Network", None, weight, network.unavailable_reason)
    if not network.connected:
        return Component("network", "Network", 0.0, weight, "no network connection")

    value = 100.0
    notes: list[str] = []
    uplink = network.uplink
    if uplink is not None:
        speed = uplink.link_speed_mbps
        notes.append(f"{uplink.kind_label}" + (f" {speed} Mbps" if speed else ""))
        if uplink.kind is LinkKind.WIRELESS:
            value -= 20
            notes.append("Wi-Fi -20")
        elif uplink.kind is LinkKind.WIRED and speed is not None and speed < 1000:
            value -= 20
            notes.append("below gigabit -20")
        if uplink.full_duplex is False:
            value -= 20
            notes.append("half duplex -20")

    measured = False
    for stats, jitter_free_ms, latency_free_ms in (
        (network.gateway_ping, None, None),
        (network.internet_ping, 5.0, 50.0),
    ):
        if stats is None or not stats.trustworthy or not stats.reachable:
            if stats is not None:
                notes.append(f"{stats.label}: {stats.summary()}")
            continue
        measured = True
        notes.append(f"{stats.label}: {stats.summary()}")
        value -= min(60.0, stats.loss_percent * 3)
        if jitter_free_ms is not None and stats.jitter_ms is not None:
            value -= min(30.0, max(0.0, stats.jitter_ms - jitter_free_ms) * 2)
        if latency_free_ms is not None and stats.avg_ms is not None:
            value -= min(30.0, max(0.0, stats.avg_ms - latency_free_ms) * 0.5)

    if uplink is None and not measured:
        return Component(
            "network", "Network", None, weight,
            "; ".join(notes) or "no link or latency could be measured",
        )
    return Component(
        "network", "Network", _clamp(value), weight,
        "; ".join(notes) + " (heuristic deductions: loss x3, jitter over 5 ms "
        "x2, latency over 50 ms x0.5)",
    )


def compute_score(
    snapshot: HardwareSnapshot,
    weights: dict[str, float] | None = None,
    extra: Sequence[Component] = (),
    network: NetworkSnapshot | None = None,
) -> GamingScore:
    """Compute the Gaming Score from a hardware snapshot.

    Args:
        weights: Override the defaults. Missing keys fall back.
        extra: Additional components.
        network: The network scan. Without one the Network component is
            excluded, not scored as zero.
    """
    active = {**DEFAULT_WEIGHTS, **(weights or {})}

    components: list[Component] = [
        score_network(network, active["network"]),
        score_gpu(snapshot, active["gpu"]),
        score_cpu(snapshot, active["cpu"]),
        score_ram(snapshot, active["ram"]),
        score_storage(snapshot, active["storage"]),
        score_display(snapshot, active["display"]),
        score_system(snapshot, active["system"]),
    ]
    components.extend(extra)

    usable = [c for c in components if c.available]
    total_weight = sum(c.weight for c in usable)

    value: float | None
    if not usable or total_weight <= 0:
        value = None
    else:
        value = sum(c.score * c.weight for c in usable) / total_weight  # type: ignore[operator]

    return GamingScore(
        value=value,
        components=tuple(components),
        excluded=tuple(c.label for c in components if not c.available),
    )
