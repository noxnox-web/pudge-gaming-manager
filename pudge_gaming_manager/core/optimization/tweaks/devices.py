"""Device power saving and Energy-Efficient Ethernet — the advanced tier.

Three changes the guides agree on for a desktop that is always plugged in:
do not let Windows power down USB devices or the network adapter to save
energy, and do not let the Ethernet link drop into its low-power idle
state between packets. Each is a real, documented switch with a real cost
on a laptop and next to none on a club desktop.

Why advanced, not in ОПТИМИЗИРОВАТЬ ПК
--------------------------------------
They touch many devices at once, each through its driver, and a driver can
refuse or behave oddly. So they live in «Настройки Windows», are chosen
explicitly, and each records every device's previous state so rollback puts
back exactly what was there — including devices that already had the
setting off, which are left alone rather than "restored" to on.

The backup is JSON in ``old_value``: a list of the devices actually changed,
with their old values. Rollback touches only those.
"""

from __future__ import annotations

import json
from typing import Any

from ....network.adapter import advanced as nic
from ....utilities.exceptions import PgmError
from ....windows.devices import power
from ..tweak import (
    ApplyResult,
    BackupRecord,
    BackupScope,
    Outcome,
    RiskLevel,
    Tweak,
    TweakContext,
    TweakState,
    Validation,
    Verification,
)


class _DevicePowerTweak(Tweak):
    """Turn off «allow the computer to turn off this device» for a set."""

    abstract_base = True
    scope = BackupScope.DEVICE
    risk = RiskLevel.MEDIUM
    requires_admin = True

    def __init__(self, reader=power.read, writer=power.set_enabled) -> None:
        self._read = reader
        self._write = writer

    def _selects(self, instance_id: str) -> bool:
        raise NotImplementedError

    def _targets(self) -> list[str]:
        """Instance names that may currently be powered down."""
        return sorted(
            s.instance_name
            for s in self._read()
            if s.enabled and self._selects(s.instance_id)
        )

    def scan(self, ctx: TweakContext) -> TweakState:
        try:
            targets = self._targets()
        except PgmError as exc:
            return TweakState(
                needs_change=False, current_absent=True,
                summary=f"{self.name}: не удалось прочитать — {exc.reason or exc.what}",
            )
        return TweakState(
            current_value=json.dumps(targets),
            desired_value="[]",
            needs_change=bool(targets),
            summary=(
                f"{self.name}: запретить — устройств: {len(targets)}"
                if targets
                else f"{self.name}: уже запрещено"
            ),
        )

    def validate(self, ctx: TweakContext, state: TweakState) -> Validation:
        if state.current_absent:
            return Validation.refuse("Не удалось прочитать состояние устройств.")
        return Validation.allow(requires_admin=True)

    def backup(self, ctx: TweakContext, state: TweakState) -> BackupRecord:
        return BackupRecord(
            backup_id=BackupRecord.new_id(),
            tweak_id=self.id,
            tweak_version=self.version,
            scope=BackupScope.DEVICE,
            target=self.id,
            old_value=state.current_value,
            old_value_kind="JSON_DEVICE_LIST",
            new_value="[]",
        )

    def apply(self, ctx: TweakContext, state: TweakState) -> ApplyResult:
        names = json.loads(state.current_value or "[]")
        failed = self._write(names, False)
        if len(failed) == len(names):
            return ApplyResult(Outcome.FAILED, "Ни одно устройство не приняло изменение.")
        detail = f"Отключение запрещено для устройств: {len(names) - len(failed)}."
        if failed:
            detail += f" Не приняли: {len(failed)}."
        return ApplyResult(Outcome.SUCCESS, detail)

    def verify(self, ctx: TweakContext, state: TweakState) -> Verification:
        wanted = {n.lower() for n in json.loads(state.current_value or "[]")}
        still = [s.instance_name for s in self._read() if s.enabled and s.instance_name.lower() in wanted]
        if still:
            return Verification.deny(
                json.dumps(still), f"Всё ещё разрешено отключение: {len(still)} устр."
            )
        return Verification.confirm("[]", "Windows подтверждает новое состояние устройств.")

    def rollback(self, ctx: TweakContext, backup: BackupRecord) -> ApplyResult:
        names = json.loads(str(backup.old_value or "[]"))
        failed = self._write(names, True)
        if failed:
            return ApplyResult(
                Outcome.FAILED,
                f"Не удалось вернуть прежнее состояние: {len(failed)} устр.",
            )
        return ApplyResult(
            Outcome.SUCCESS, f"Возвращено прежнее состояние: {len(names)} устр."
        )


class DisableUsbPowerSavingTweak(_DevicePowerTweak):
    """USB hubs and devices: never powered down to save energy."""

    id = "devices.usb.disable_power_saving"
    version = 1
    name = "USB: отключение для экономии энергии"
    description = (
        "Снимает галочку «Разрешить отключение этого устройства для экономии "
        "энергии» у USB-концентраторов и USB-устройств — та же галочка, что "
        "в диспетчере устройств."
    )
    rationale = (
        "Windows может обесточить простаивающее USB-устройство или целый "
        "концентратор, а на пробуждение уходит время — мышь или клавиатура "
        "могут «проснуться» с задержкой или отвалиться до переподключения. "
        "На стационарном клубном ПК экономия энергии здесь ничтожна. "
        "Дополняет, а не заменяет, отключение выборочной приостановки USB "
        "в схеме питания: это две разные настройки."
    )
    subsystem = "input"

    def _selects(self, instance_id: str) -> bool:
        return instance_id.upper().startswith("USB\\")


class DisableNicPowerSavingTweak(_DevicePowerTweak):
    """Physical network adapters: never powered down to save energy."""

    id = "devices.network.disable_power_saving"
    version = 1
    name = "Сетевая карта: отключение для экономии энергии"
    description = (
        "Снимает галочку «Разрешить отключение этого устройства для экономии "
        "энергии» у физических сетевых адаптеров."
    )
    rationale = (
        "Обесточенный адаптер после пробуждения заново согласует линк, и на "
        "несколько секунд сеть пропадает — в онлайн-игре это разрыв. На "
        "стационарном ПК экономии от этого почти нет."
    )
    subsystem = "network"

    def __init__(self, reader=power.read, writer=power.set_enabled, adapters=nic.adapters) -> None:
        super().__init__(reader, writer)
        self._adapters = adapters
        self._nic_ids: set[str] | None = None

    def _selects(self, instance_id: str) -> bool:
        if self._nic_ids is None:
            self._nic_ids = {a.pnp_id.upper() for a in self._adapters() if a.pnp_id}
        return instance_id.upper() in self._nic_ids


class DisableEnergyEfficientEthernetTweak(Tweak):
    """Energy-Efficient Ethernet off on every physical wired adapter."""

    id = "network.eee.disable"
    version = 1
    name = "Energy-Efficient Ethernet"
    description = (
        "Выключает энергосберегающий Ethernet (EEE, Green Ethernet) на "
        "проводных адаптерах. Значение сохраняется сразу, а действует после "
        "перезапуска адаптера или перезагрузки — сеть посреди сессии не "
        "обрывается."
    )
    rationale = (
        "EEE переводит линк в режим пониженного энергопотребления между "
        "пакетами, и на выход из него уходят микросекунды на каждом "
        "пробуждении; с некоторыми коммутаторами он же вызывает обрывы "
        "линка. Для ПК, который всегда подключён к сети, выгоды нет. "
        "Меняются только известные ключи EEE и только если драйвер "
        "принимает значение 0."
    )
    risk = RiskLevel.MEDIUM
    subsystem = "network"
    scope = BackupScope.NETWORK
    requires_admin = True

    def __init__(self, adapters=nic.adapters, setter=nic.set_property) -> None:
        self._adapters = adapters
        self._set = setter

    def _changes(self) -> list[dict[str, Any]]:
        found = []
        for adapter in self._adapters():
            if not adapter.wired:
                continue
            for prop in adapter.eee_properties():
                if prop.value != "0":
                    found.append(
                        {"adapter": adapter.name, "keyword": prop.keyword, "value": prop.value}
                    )
        return found

    def scan(self, ctx: TweakContext) -> TweakState:
        try:
            changes = self._changes()
        except PgmError as exc:
            return TweakState(
                needs_change=False, current_absent=True,
                summary=f"{self.name}: не удалось прочитать — {exc.reason or exc.what}",
            )
        names = ", ".join(f"{c['adapter']} ({c['keyword']})" for c in changes)
        return TweakState(
            current_value=json.dumps(changes, ensure_ascii=False),
            desired_value="0",
            needs_change=bool(changes),
            summary=(
                f"{self.name}: выключить — {names}"
                if changes
                else f"{self.name}: выключен или не поддерживается драйвером"
            ),
        )

    def validate(self, ctx: TweakContext, state: TweakState) -> Validation:
        if state.current_absent:
            return Validation.refuse("Не удалось прочитать свойства сетевых адаптеров.")
        return Validation.allow(requires_admin=True)

    def backup(self, ctx: TweakContext, state: TweakState) -> BackupRecord:
        return BackupRecord(
            backup_id=BackupRecord.new_id(),
            tweak_id=self.id,
            tweak_version=self.version,
            scope=BackupScope.NETWORK,
            target=self.id,
            old_value=state.current_value,
            old_value_kind="JSON_ADAPTER_PROPERTIES",
            new_value="0",
        )

    def _write_all(self, changes: list[dict[str, Any]], value_of) -> list[str]:
        failed = []
        for change in changes:
            try:
                self._set(change["adapter"], change["keyword"], value_of(change))
            except PgmError as exc:
                failed.append(f"{change['adapter']}: {exc.reason or exc.what}")
        return failed

    def apply(self, ctx: TweakContext, state: TweakState) -> ApplyResult:
        changes = json.loads(state.current_value or "[]")
        failed = self._write_all(changes, lambda _c: "0")
        if len(failed) == len(changes):
            return ApplyResult(Outcome.FAILED, "; ".join(failed))
        return ApplyResult(
            Outcome.SUCCESS,
            "EEE выключен; вступит в силу после перезапуска адаптера или "
            "перезагрузки." + (f" Не удалось: {'; '.join(failed)}." if failed else ""),
            needs_restart=True,
        )

    def verify(self, ctx: TweakContext, state: TweakState) -> Verification:
        wanted = {(c["adapter"], c["keyword"]) for c in json.loads(state.current_value or "[]")}
        remaining = [c for c in self._changes() if (c["adapter"], c["keyword"]) in wanted]
        if remaining:
            return Verification.deny(
                json.dumps(remaining, ensure_ascii=False),
                "Драйвер не сохранил новое значение.",
            )
        return Verification.confirm(
            "0", "Значение сохранено драйвером; действует после перезапуска адаптера."
        )

    def rollback(self, ctx: TweakContext, backup: BackupRecord) -> ApplyResult:
        changes = json.loads(str(backup.old_value or "[]"))
        failed = self._write_all(changes, lambda c: c["value"])
        if failed:
            return ApplyResult(Outcome.FAILED, "; ".join(failed))
        return ApplyResult(
            Outcome.SUCCESS,
            f"EEE возвращён в прежнее состояние на {len(changes)} адаптер(ах).",
            needs_restart=True,
        )


def device_tweaks() -> list[Tweak]:
    return [
        DisableUsbPowerSavingTweak(),
        DisableNicPowerSavingTweak(),
        DisableEnergyEfficientEthernetTweak(),
    ]


__all__ = [
    "DisableEnergyEfficientEthernetTweak",
    "DisableNicPowerSavingTweak",
    "DisableUsbPowerSavingTweak",
    "device_tweaks",
]
