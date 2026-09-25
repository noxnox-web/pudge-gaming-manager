"""A catalogue setting set to one chosen option.

:class:`RegistryValueTweak` already owns the lifecycle — read, back up with
type and absence, write, read back, restore or delete. What a catalogue
setting adds is only *which* value, and that is decided by the operator's
choice rather than hard-coded, so the class is configured per instance.

One setting is not a plain value: ``DirectXUserGlobalSettings`` is a string
of ``key=value;`` pairs that several Settings pages share. Overwriting it
with ``SwapEffectUpgradeEnable=1;`` would silently reset whatever else was
in it (auto HDR, VRR optimisation), so :class:`FlagStringTweak` rewrites
only its own pair and keeps the rest byte for byte.
"""

from __future__ import annotations

from typing import Any

from ...settings.catalog import SettingSpec, windows_build
from ..tweak import TweakContext, TweakState, Validation
from .registry_value import ABSENT, RegistryValueTweak


class RegistryChoiceTweak(RegistryValueTweak):
    """Set one catalogue setting to one of its options."""

    # Class-level identity so the tweak contract is satisfied; every
    # instance overrides all three from its spec.
    id = "setting"
    name = "Настройка Windows"
    rationale = "Настройка из каталога; обоснование — в описании варианта."

    def __init__(self, spec: SettingSpec, option_key: str, registry=None) -> None:
        super().__init__(registry)
        option = spec.option(option_key)
        self.spec = spec
        self.option = option
        self.id = spec.tweak_id
        self.name = spec.name
        self.description = spec.what
        self.rationale = spec.why
        self.risk = spec.risk
        self.subsystem = "settings"
        self.hive = spec.hive
        self.subkey = spec.subkey
        self.value_name = spec.value_name
        self.value_type = spec.value_type
        self.desired_data = option.data
        self.restart_required = spec.activation.value == "RESTART"
        self.requires_admin = spec.hive.requires_admin

    def validate(self, ctx: TweakContext, state: TweakState) -> Validation:
        build = windows_build()
        if self.spec.min_build and build and build < self.spec.min_build:
            return Validation.refuse(
                f"настройка появилась в сборке Windows {self.spec.min_build}, "
                f"а здесь {build} — Windows её не прочитает"
            )
        return super().validate(ctx, state)

    def describe(self, data: Any) -> str:
        if data == ABSENT:
            absent = self.spec.extra.get("absent_label")
            if absent:
                return f"не задано ({absent})"
            try:
                return f"не задано ({self.spec.option(self.spec.absent_means).label})"
            except KeyError:
                return "не задано"
        option = self.spec.option_for(data)
        return option.label if option else str(data)


def parse_flags(text: str) -> list[tuple[str, str]]:
    """``"A=1;B=0;"`` -> ``[("A", "1"), ("B", "0")]``, order kept."""
    pairs: list[tuple[str, str]] = []
    for chunk in (text or "").split(";"):
        chunk = chunk.strip()
        if not chunk:
            continue
        key, _, value = chunk.partition("=")
        pairs.append((key.strip(), value.strip()))
    return pairs


def flag_value(text: str | None, flag: str) -> str | None:
    """The value of ``flag`` inside a ``key=value;`` string, if present."""
    for key, value in parse_flags(text or ""):
        if key.lower() == flag.lower():
            return value
    return None


def with_flag(text: str | None, flag: str, value: str) -> str:
    """``text`` with ``flag`` set to ``value``; every other pair untouched."""
    pairs = parse_flags(text or "")
    replaced = False
    for index, (key, _old) in enumerate(pairs):
        if key.lower() == flag.lower():
            pairs[index] = (key, value)
            replaced = True
    if not replaced:
        pairs.append((flag, value))
    return "".join(f"{k}={v};" for k, v in pairs)


class FlagStringTweak(RegistryChoiceTweak):
    """A choice whose value is one pair inside a shared ``key=value;`` string.

    The desired string is recomputed from what is there at scan time, so
    the write carries every other pair unchanged. The backup is the whole
    old string (or its absence), which is what rollback must restore.
    """

    def _merged(self) -> str:
        current = self._read()
        base = "" if current.absent else str(current.data or "")
        return with_flag(base, self.spec.flag, str(self.option.data))

    def scan(self, ctx: TweakContext) -> TweakState:
        try:
            self.desired_data = self._merged()
        except Exception:  # noqa: BLE001 - the base scan reports the read failure
            pass
        state = super().scan(ctx)
        if not state.needs_change or state.current_absent:
            return state
        current = state.current_value
        before = (
            flag_value(None if current == ABSENT else str(current), self.spec.flag)
        )
        before_label = self._flag_label(before)
        return TweakState(
            current_value=state.current_value,
            desired_value=state.desired_value,
            needs_change=True,
            summary=f"{self.name}: {before_label} -> {self.option.label}",
        )

    def _flag_label(self, value: str | None) -> str:
        if value is None:
            return "не задано"
        option = self.spec.option_for(value)
        return option.label if option else value

    def describe(self, data: Any) -> str:
        if data == ABSENT:
            return "не задано"
        return self._flag_label(flag_value(str(data), self.spec.flag))


def tweak_for(spec: SettingSpec, option_key: str, registry=None) -> RegistryChoiceTweak:
    """The tweak that sets ``spec`` to ``option_key``."""
    cls = FlagStringTweak if spec.flag else RegistryChoiceTweak
    return cls(spec, option_key, registry)


__all__ = [
    "FlagStringTweak",
    "RegistryChoiceTweak",
    "flag_value",
    "parse_flags",
    "tweak_for",
    "with_flag",
]
