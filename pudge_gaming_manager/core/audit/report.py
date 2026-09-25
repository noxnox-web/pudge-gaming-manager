"""The system audit: one read-only report of how this PC is set up for games.

It answers, in one place, what the guides make an operator check in a dozen
Windows dialogs: which CPU topology, which GPU driver, which power scheme,
whether pointer acceleration is on, which USB controller the mouse is on,
whether the network driver is ten years old, whether MSI is in use, where
Defender and Windows Update stand — and, for every catalogue setting, the
current value next to the recommendation and the reason.

Nothing in here changes anything. Every probe is injected, so the report is
testable without the machine, and every probe is allowed to fail: a probe
that could not run becomes a line saying so, never a missing section.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import Any, Callable

from ...hardware.cpu import topology
from ...hardware.models import HardwareSnapshot
from ...network.adapter import advanced as nic
from ...utilities.formatting import format_size
from ...utilities.logging_setup import get_logger
from ...windows.devices import msi, power, usb
from ...windows.input import mouse
from ...windows.power.manager import PowerManager
from ...windows.security import status as security
from ...windows.startup.manager import StartupManager
from ..settings import state as settings_state
from .policy import NOT_CHANGED, manual_steps

_log = get_logger(__name__)

#: A network driver older than this is worth a look.
_OLD_DRIVER_YEARS = 3


class Level(str, Enum):
    OK = "OK"
    ATTENTION = "ATTENTION"
    INFO = "INFO"
    MANUAL = "MANUAL"
    EXCLUDED = "EXCLUDED"

    @property
    def mark(self) -> str:
        return {
            Level.OK: "✓",
            Level.ATTENTION: "⚠",
            Level.INFO: "•",
            Level.MANUAL: "✎",
            Level.EXCLUDED: "✕",
        }[self]


@dataclass(frozen=True, slots=True)
class AuditItem:
    name: str
    value: str
    level: Level = Level.INFO
    note: str = ""

    def line(self) -> str:
        text = f"{self.level.mark} {self.name}: {self.value}"
        return f"{text}\n    {self.note}" if self.note else text


@dataclass(frozen=True, slots=True)
class AuditSection:
    title: str
    items: tuple[AuditItem, ...]


@dataclass(frozen=True, slots=True)
class SystemReport:
    sections: tuple[AuditSection, ...]
    generated_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))

    @property
    def attention(self) -> list[AuditItem]:
        return [i for s in self.sections for i in s.items if i.level is Level.ATTENTION]

    def text(self) -> str:
        lines = [f"Аудит системы — {self.generated_at.replace('T', ' ')}", ""]
        for section in self.sections:
            lines.append(section.title.upper())
            lines.extend(item.line() for item in section.items)
            lines.append("")
        return "\n".join(lines)


@dataclass(slots=True)
class Probes:
    """Every source the report reads, injectable for tests."""

    settings: Callable[[], list] = settings_state.read_all
    topology: Callable[[], topology.CpuTopology] = topology.read
    usb: Callable[[], usb.UsbInventory] = usb.inventory
    adapters: Callable[[], list] = nic.adapters
    device_power: Callable[[], list] = power.read
    gpu_ids: Callable[[], list] = msi.display_adapters
    msi_read: Callable[[str], msi.MsiState] = msi.read
    security: Callable[[], security.SecurityStatus] = security.read
    mouse: Callable[[], Any] = mouse.read
    power_scheme: Callable[[], Any] = field(
        default_factory=lambda: (lambda: PowerManager().get_active_scheme())
    )
    power_schemes: Callable[[], list] = field(
        default_factory=lambda: (lambda: PowerManager().list_schemes())
    )
    startup: Callable[[], list] = field(
        default_factory=lambda: (lambda: StartupManager().entries())
    )
    today: Callable[[], date] = date.today


def _safe(probe: Callable[[], Any], what: str) -> tuple[Any, str]:
    try:
        return probe(), ""
    except Exception as exc:  # noqa: BLE001 - a failed probe is a line, not a crash
        _log.warning("audit probe %s failed: %s", what, exc)
        return None, str(getattr(exc, "reason", "") or getattr(exc, "what", "") or exc)


def _unavailable(name: str, error: str) -> AuditItem:
    return AuditItem(name, f"не удалось прочитать ({error})", Level.INFO)


# -- sections ---------------------------------------------------------------


def _system(snapshot: HardwareSnapshot | None, probes: Probes) -> AuditSection:
    items: list[AuditItem] = []
    topo, error = _safe(probes.topology, "topology")
    if snapshot is not None:
        os_info = snapshot.os
        items.append(AuditItem("Windows", f"{os_info.caption} (сборка {os_info.build})"))
        cpu = snapshot.cpu
        items.append(AuditItem("Процессор", cpu.model or "неизвестно"))
    if topo is not None and not topo.error:
        detail = f"{topo.physical} ядер / {topo.logical} потоков"
        if topo.hybrid:
            detail += f"; P-ядер {topo.performance_cores}, E-ядер {topo.efficiency_cores}"
        detail += "; SMT/Hyper-Threading " + ("включён" if topo.smt else "нет или выключен")
        note = ""
        if topo.cache_domains > 1:
            note = (
                f"Кэш-доменов (CCD): {topo.cache_domains}. На двухкристальных "
                "Ryzen X3D игру на кристалл с 3D-кэшем переводит драйвер AMD — "
                "схема «Сбалансированная» и игровой режим должны оставаться включены."
            )
        items.append(AuditItem("Топология", detail, Level.INFO, note))
    elif error or (topo and topo.error):
        items.append(_unavailable("Топология", error or topo.error))
    if snapshot is not None:
        ram = snapshot.ram.total_mb.value
        if ram:
            items.append(AuditItem("Память", f"{ram / 1024:.0f} ГБ"))
        for gpu in snapshot.gpus:
            driver = gpu.driver_version or "?"
            if gpu.driver_date:
                driver += f" от {gpu.driver_date}"
            items.append(AuditItem("Видеокарта", f"{gpu.model} — драйвер {driver}"))
        for monitor in snapshot.monitors:
            mode = monitor.current_mode
            if mode is None:
                continue
            best = monitor.max_refresh_at_current_resolution.value
            low = bool(best and best > mode.refresh_hz)
            items.append(
                AuditItem(
                    f"Монитор {monitor.friendly_name or monitor.device_name}",
                    f"{mode.width}×{mode.height} @ {mode.refresh_hz} Гц"
                    + (f" (умеет {best} Гц)" if low else ""),
                    Level.ATTENTION if low else Level.OK,
                    "ОПТИМИЗИРОВАТЬ ПК предложит максимальную частоту." if low else "",
                )
            )
        for disk in snapshot.disks:
            free = disk.free_gb.value
            percent = disk.free_percent.value
            if free is None:
                continue
            tight = percent is not None and percent < 15
            items.append(
                AuditItem(
                    f"Диск {disk.device_id}",
                    f"свободно {format_size(int(free * 1024 ** 3))}"
                    + (f" ({percent:.0f}%)" if percent is not None else ""),
                    Level.ATTENTION if tight else Level.OK,
                    "Мало места: «ОЧИСТКА ДИСКА» и «Что занимает место»." if tight else "",
                )
            )
    return AuditSection("Железо и Windows", tuple(items))


def _settings(probes: Probes) -> AuditSection:
    states, error = _safe(probes.settings, "settings")
    if states is None:
        return AuditSection("Настройки Windows", (_unavailable("Настройки", error),))
    items = []
    for s in states:
        if not s.available:
            items.append(AuditItem(s.spec.name, f"недоступно: {s.unavailable_reason}"))
            continue
        match = s.matches_recommendation
        level = Level.INFO if match is None else (Level.OK if match else Level.ATTENTION)
        note = f"Рекомендуется: {s.recommended_label}. {s.spec.why}"
        if s.spec.conflict:
            note += f" {s.spec.conflict}"
        items.append(
            AuditItem(f"{s.spec.name} [{s.spec.tier.label.lower()}]", s.current_label, level, note)
        )
    return AuditSection("Настройки Windows", tuple(items))


def _power_and_input(probes: Probes, adapters: list | None) -> AuditSection:
    items: list[AuditItem] = []
    scheme, error = _safe(probes.power_scheme, "power scheme")
    if scheme is not None:
        name = scheme.name
        if not name:
            schemes, _ = _safe(probes.power_schemes, "power schemes")
            for candidate in schemes or []:
                if candidate.normalized_guid == scheme.normalized_guid:
                    name = candidate.name
        items.append(AuditItem("Схема питания", name or scheme.guid))
    else:
        items.append(_unavailable("Схема питания", error))

    accel, error = _safe(probes.mouse, "mouse")
    if accel is not None:
        items.append(
            AuditItem(
                "Повышенная точность указателя (ускорение мыши)",
                "включена" if accel.enabled else "выключена",
                Level.ATTENTION if accel.enabled else Level.OK,
                "Ускорение делает путь курсора зависимым от скорости руки. "
                "ОПТИМИЗИРОВАТЬ ПК предлагает выключить." if accel.enabled else "",
            )
        )
    else:
        items.append(_unavailable("Ускорение мыши", error or "нет ответа"))

    states, error = _safe(probes.device_power, "device power")
    if states is None:
        items.append(_unavailable("Экономия энергии устройств", error))
    else:
        usb_on = [s for s in states if s.instance_id.upper().startswith("USB\\") and s.enabled]
        nic_ids = {a.pnp_id.upper() for a in adapters or []}
        nic_on = [s for s in states if s.instance_id.upper() in nic_ids and s.enabled]
        items.append(
            AuditItem(
                "USB: разрешено отключение для экономии энергии",
                f"у {len(usb_on)} устройств" if usb_on else "нигде",
                Level.ATTENTION if usb_on else Level.OK,
                "Меняется в «Настройки Windows → Расширенные»." if usb_on else "",
            )
        )
        if nic_ids:
            items.append(
                AuditItem(
                    "Сетевая карта: разрешено отключение для экономии энергии",
                    "да" if nic_on else "нет",
                    Level.ATTENTION if nic_on else Level.OK,
                )
            )
    return AuditSection("Питание и ввод", tuple(items))


def _usb(probes: Probes) -> AuditSection:
    inventory, error = _safe(probes.usb, "usb")
    if inventory is None or inventory.error:
        return AuditSection("USB и устройства ввода", (_unavailable("USB", error or inventory.error),))
    items = []
    for controller in inventory.controllers:
        count = inventory.devices_per_controller.get(controller.instance_id, 0)
        items.append(
            AuditItem(
                "Контроллер",
                f"{controller.name}: устройств {count}; MSI {controller.msi.label}",
            )
        )
    for device in inventory.inputs:
        controller = inventory.controller(device.controller_id)
        shared = inventory.devices_per_controller.get(device.controller_id, 0)
        items.append(
            AuditItem(
                device.label,
                f"на контроллере «{controller.name if controller else device.controller_id}»",
                Level.INFO,
                (f"На этом контроллере ещё устройств: {shared - 1}. Мышь лучше "
                 "подключить в порт другого контроллера, где соседей меньше.")
                if shared > 2 and len(inventory.controllers) > 1 and device.kind == "мышь"
                else "",
            )
        )
    items.append(
        AuditItem(
            "Частота опроса мыши",
            "не измеряется",
            Level.INFO,
            "Windows не сообщает её приложениям, а игровые мыши задают её в "
            "своём ПО. Проверяется в ПО мыши.",
        )
    )
    return AuditSection("USB и устройства ввода", tuple(items))


def _network(probes: Probes, adapters: list | None, error: str) -> AuditSection:
    if adapters is None:
        return AuditSection("Сеть", (_unavailable("Сетевые адаптеры", error),))
    items = []
    today = probes.today()
    for adapter in adapters:
        items.append(
            AuditItem(
                adapter.name,
                f"{adapter.description}; {adapter.status}"
                + (f", {adapter.link_speed}" if adapter.is_up else ""),
            )
        )
        old = _driver_age_years(adapter.driver_date, today)
        generic = "microsoft" in adapter.driver_provider.lower()
        attention = old is not None and old >= _OLD_DRIVER_YEARS
        note = ""
        if attention:
            note = (
                f"Драйверу {old} лет" + (" и он стандартный, от Microsoft" if generic else "")
                + ". Свежий драйвер производителя карты обычно исправляет "
                "обрывы и задержки; ставится вручную с сайта производителя."
            )
        items.append(
            AuditItem(
                f"{adapter.name}: драйвер",
                f"{adapter.driver_provider} {adapter.driver_version} от {adapter.driver_date}"
                + (f", NDIS {adapter.ndis_version}" if adapter.ndis_version else ""),
                Level.ATTENTION if attention else Level.OK,
                note,
            )
        )
        eee = adapter.eee_properties()
        if eee:
            on = [p for p in eee if p.value != "0"]
            items.append(
                AuditItem(
                    f"{adapter.name}: Energy-Efficient Ethernet",
                    "включён" if on else "выключен",
                    Level.ATTENTION if on and adapter.wired else Level.OK,
                )
            )
        for keyword, label in nic.DIAGNOSTIC_KEYWORDS.items():
            prop = adapter.prop(keyword)
            if prop is not None:
                items.append(
                    AuditItem(f"{adapter.name}: {label}", prop.display_value or prop.value)
                )
    return AuditSection("Сеть", tuple(items))


def _driver_age_years(text: str, today: date) -> int | None:
    try:
        driver = date.fromisoformat(text[:10])
    except ValueError:
        return None
    return max(0, (today - driver).days // 365)


def _interrupts(probes: Probes, adapters: list | None) -> AuditSection:
    items = []
    gpus, _ = _safe(probes.gpu_ids, "gpu ids")
    for name, instance in gpus or []:
        items.append(AuditItem(f"Видеокарта {name}", f"MSI {probes.msi_read(instance).label}"))
    for adapter in adapters or []:
        if adapter.pnp_id.upper().startswith("PCI\\"):
            items.append(
                AuditItem(f"Сеть {adapter.description}", f"MSI {probes.msi_read(adapter.pnp_id).label}")
            )
    items.append(
        AuditItem(
            "Режим MSI", "только просмотр", Level.EXCLUDED,
            "Принудительное включение MSI для устройства, чей драйвер его не "
            "поддерживает, оставляет его без прерываний после перезагрузки. "
            "Программа показывает режим и не меняет его.",
        )
    )
    return AuditSection("Прерывания", tuple(items))


def _security(probes: Probes) -> AuditSection:
    status, error = _safe(probes.security, "security")
    if status is None:
        return AuditSection("Безопасность и обновления", (_unavailable("Безопасность", error),))
    items = []
    for finding in status.findings:
        level = Level.INFO if finding.ok is None else (Level.OK if finding.ok else Level.ATTENTION)
        items.append(AuditItem(finding.name, finding.value, level, finding.note))
    return AuditSection("Безопасность и обновления", tuple(items))


def _startup(probes: Probes) -> AuditSection:
    entries, error = _safe(probes.startup, "startup")
    if entries is None:
        return AuditSection("Автозагрузка", (_unavailable("Автозагрузка", error),))
    enabled = [e for e in entries if e.enabled]
    return AuditSection(
        "Автозагрузка",
        (
            AuditItem(
                "Запускается при входе",
                f"{len(enabled)} из {len(entries)}",
                Level.ATTENTION if len(enabled) > 8 else Level.OK,
                "Каждый пункт — процесс в памяти всю сессию. Отключить можно "
                "в «Автозагрузка…»; отключение обратимо.",
            ),
        ),
    )


def build_report(
    snapshot: HardwareSnapshot | None = None, probes: Probes | None = None
) -> SystemReport:
    """Read everything and assemble the report. Changes nothing."""
    probes = probes or Probes()
    adapters, adapter_error = _safe(probes.adapters, "network adapters")
    vendors = [g.vendor or g.model for g in snapshot.gpus] if snapshot else []
    sections = [
        _system(snapshot, probes),
        _settings(probes),
        _power_and_input(probes, adapters),
        _usb(probes),
        _network(probes, adapters, adapter_error),
        _interrupts(probes, adapters),
        _security(probes),
        _startup(probes),
        AuditSection(
            "Вручную: панель драйвера видеокарты",
            tuple(
                AuditItem(f"{step.where} → {step.setting}", step.value, Level.MANUAL, step.why)
                for step in manual_steps(vendors)
            )
            or (AuditItem("Видеокарта", "NVIDIA/AMD не обнаружены — ручных шагов нет"),),
        ),
        AuditSection(
            "Намеренно не меняется",
            tuple(AuditItem(e.name, "не меняется", Level.EXCLUDED, e.reason) for e in NOT_CHANGED),
        ),
    ]
    return SystemReport(tuple(sections))


__all__ = ["AuditItem", "AuditSection", "Level", "Probes", "SystemReport", "build_report"]
