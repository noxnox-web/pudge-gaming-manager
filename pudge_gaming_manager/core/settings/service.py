"""Applying what the operator chose in «Настройки Windows».

The dialog is where the advanced and experimental settings live, and where
nothing is pre-selected: every row starts at "не менять", and a change
happens only because the operator picked an option for that row. That pick
*is* the explicit per-change consent rule #38 asks for, so the engine here
runs with MEDIUM and HIGH allowed — and CRITICAL still refused by the engine
itself, whatever is selected.

The flow is the same one ОПТИМИЗИРОВАТЬ ПК uses, not a shortcut around it:
plan (scan + validate, no mutation) → the operator reads the plan → restore
point → backup → apply → verify → rollback on failure. Every change lands in
the same history and can be undone from it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from ...database.connection import Database
from ..optimization.engine import Plan, PlannedChange, RunReport, TweakEngine
from ..optimization.tweak import Tweak, TweakContext
from ..optimization.tweaks.choice import tweak_for
from ..optimization.tweaks.devices import device_tweaks
from .catalog import SETTINGS_BY_ID
from .state import SettingState, read_all


@dataclass(frozen=True, slots=True)
class SettingsView:
    """What the dialog shows when it opens."""

    states: tuple[SettingState, ...]
    device_changes: tuple[PlannedChange, ...]
    """Each device-level action, already scanned: what it would change here."""


class SettingsService:
    def __init__(
        self,
        database: Database,
        *,
        restore_point: Callable[[], tuple[bool, str]] | None = None,
        states: Callable[[], list[SettingState]] = read_all,
        devices: Callable[[], list[Tweak]] = device_tweaks,
    ) -> None:
        self.database = database
        self.restore_point = restore_point
        self._states = states
        self._devices = devices

    def _engine(self) -> TweakEngine:
        return TweakEngine(
            self.database, TweakContext(database=self.database), allow_risk_above_low=True
        )

    def load(self) -> SettingsView:
        """Read every setting and scan every device action. Changes nothing."""
        tweaks = self._devices()
        plan = self._engine().plan(tweaks)
        return SettingsView(tuple(self._states()), plan.changes)

    def preview(self, choices: dict[str, str], device_ids: set[str]) -> Plan:
        """Plan exactly the chosen changes. Changes nothing.

        Args:
            choices: setting id -> option key, for rows the operator changed.
            device_ids: tweak ids of the device actions the operator ticked.
        """
        tweaks: list[Tweak] = [
            tweak_for(SETTINGS_BY_ID[spec_id], option)
            for spec_id, option in choices.items()
        ]
        tweaks.extend(t for t in self._devices() if t.id in device_ids)
        engine = self._engine()
        engine.register(tweaks)
        return engine.plan(tweaks)

    def apply(self, plan: Plan) -> tuple[RunReport, list[str]]:
        """Execute an approved plan. Returns the report and notes."""
        notes: list[str] = []
        if plan.applicable and self.restore_point is not None:
            try:
                _created, message = self.restore_point()
                notes.append(message)
            except Exception as exc:  # noqa: BLE001 - never blocks the run
                notes.append(f"Точка восстановления не создана: {exc}")
        return self._engine().apply(plan), notes


__all__ = ["SettingsService", "SettingsView"]
