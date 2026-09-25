"""Individual power settings inside a scheme, rather than the whole scheme.

Switching to High Performance is a blunt instrument: it moves dozens of
values at once and says nothing about which of them mattered. These read and
write one setting at a time, so a change can be described, verified and put
back on its own.

Parsing ``powercfg /query`` without depending on the language
-------------------------------------------------------------
Every label in that output is localised — on a Russian install the current
index is announced as "Текущий индекс настройки питания от сети" — so the
parse has to find the numbers by structure.

The first attempt took "the first two hex numbers", which worked for an
enumerated setting like USB selective suspend, where the possible values
print in decimal::

          Индекс возможной настройки: 000
        Текущий индекс настройки питания от сети: 0x00000001
        Текущий индекс настройки питания от батарей: 0x00000001

and was wrong for a *range* setting like the minimum processor state, where
the bounds print in hex as well::

          Минимальная возможная настройка: 0x00000000
          Максимальная возможная настройка: 0x00000064
          Инкремент возможных настроек: 0x00000001
        Текущий индекс настройки питания от сети: 0x00000064
        Текущий индекс настройки питания от батарей: 0x00000005

It read the lower bound, 0, as the current value — and a machine pinned at
100% looked like one sitting at 0.

Indentation is the discriminator that holds for both. powercfg nests the
block describing what a setting *accepts* one level deeper than the setting
itself, while the current AC and DC indices sit at the setting's own level.
So: among the lines carrying a hex number, the least-indented ones are the
current values, in AC then DC order. That is structural, and no label is
read.

Applying
--------
``powercfg /setacvalueindex`` writes the value into the scheme, but the
running system keeps using the settings it loaded when the scheme was
activated. ``/setactive`` on the same scheme is what makes Windows re-read
them, and without it the change is stored and inert — which would verify as
written and behave as if nothing happened.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ...utilities.command_runner import CommandRunner
from ...utilities.exceptions import PgmError
from ...utilities.logging_setup import audit_event, get_logger

_log = get_logger(__name__)

#: The scheme powercfg understands as "whichever is active now".
CURRENT_SCHEME = "SCHEME_CURRENT"

_HEX_INDEX = re.compile(r"0x([0-9a-fA-F]{1,8})")


def _current_indices(output: str) -> list[int]:
    """The AC and DC indices from one ``powercfg /query`` block.

    See the module docstring: the current values are the hex numbers at the
    shallowest indentation, because powercfg indents the description of
    what a setting accepts one level deeper than the setting itself.
    """
    found: list[tuple[int, int]] = []
    for line in output.splitlines():
        match = _HEX_INDEX.search(line)
        if match is None:
            continue
        indent = len(line) - len(line.lstrip())
        found.append((indent, int(match.group(1), 16)))

    if not found:
        return []
    shallowest = min(indent for indent, _ in found)
    return [value for indent, value in found if indent == shallowest]


@dataclass(frozen=True, slots=True)
class PowerSettingIndex:
    """What a setting is currently set to, on mains and on battery."""

    ac: int
    dc: int | None = None


class PowerSettings:
    """Reads and writes one power setting at a time."""

    def __init__(self, runner: CommandRunner | None = None) -> None:
        self.runner = runner or CommandRunner()

    def read(
        self, subgroup: str, setting: str, scheme: str = CURRENT_SCHEME
    ) -> PowerSettingIndex | None:
        """The setting's current indices, or ``None`` if it is not present.

        A setting a given machine does not expose is not an error: laptops
        and desktops publish different subgroups, and a missing one means
        "there is nothing here to change".
        """
        result = self.runner.run(
            ["powercfg", "/query", scheme, subgroup, setting], check=False
        )
        if result.exit_code != 0:
            _log.debug(
                "powercfg /query %s %s: exit %d", subgroup, setting,
                result.exit_code,
            )
            return None

        indices = _current_indices(result.stdout)
        if not indices:
            return None
        return PowerSettingIndex(
            ac=indices[0], dc=indices[1] if len(indices) > 1 else None
        )

    def set_ac(
        self,
        subgroup: str,
        setting: str,
        index: int,
        scheme: str = CURRENT_SCHEME,
    ) -> None:
        """Set the mains value and make the running system pick it up.

        Only the mains value. On a desktop that is the only one that runs,
        and on a laptop leaving the battery profile alone is the
        conservative choice: a player who unplugs should still get the
        power behaviour they configured.

        Raises:
            PgmError: powercfg refused the change.
        """
        self.runner.run(
            [
                "powercfg", "/setacvalueindex", scheme, subgroup, setting,
                str(index),
            ],
            check=True,
        )
        # Stored is not applied. Re-activating the scheme is what makes
        # Windows load the value it has just been given.
        self.runner.run(["powercfg", "/setactive", scheme], check=True)

        audit_event(
            "power",
            "set_value_index",
            target=f"{subgroup}\\{setting}",
            new_state={"ac_index": index},
            result="SUCCESS",
        )

    def set_ac_checked(
        self,
        subgroup: str,
        setting: str,
        index: int,
        scheme: str = CURRENT_SCHEME,
    ) -> bool:
        """Set the value and report whether reading it back agrees."""
        try:
            self.set_ac(subgroup, setting, index, scheme)
        except PgmError as exc:
            _log.warning("could not set %s\\%s: %s", subgroup, setting, exc.what)
            return False
        observed = self.read(subgroup, setting, scheme)
        return observed is not None and observed.ac == index


# --------------------------------------------------------------------------
# The GUIDs this program uses, each documented by Microsoft and stable.
# --------------------------------------------------------------------------

#: USB settings.
SUBGROUP_USB = "2a737441-1930-4402-8d77-b2bebba308a3"
#: USB selective suspend: 0 disabled, 1 enabled.
SETTING_USB_SELECTIVE_SUSPEND = "48e6b7a6-50f5-4782-a5d4-53bb8f07e226"

#: Processor power management.
SUBGROUP_PROCESSOR = "54533251-82be-4824-96c1-47b60b740d00"
#: Minimum processor state, as a percentage. High Performance and Ultimate
#: Performance both hold this at 100; Balanced lets it fall.
SETTING_PROCESSOR_MIN_STATE = "893dee8e-2bef-41e0-89c6-b55d0929964c"

#: PCI Express settings.
SUBGROUP_PCIE = "501a4d13-42af-4429-9fd1-a8218c268e20"
#: Link State Power Management: 0 off, 1 moderate, 2 maximum saving.
SETTING_PCIE_ASPM = "ee12f906-d277-404b-b6da-e5fa1a576df5"


__all__ = [
    "CURRENT_SCHEME",
    "SETTING_PCIE_ASPM",
    "SETTING_PROCESSOR_MIN_STATE",
    "SETTING_USB_SELECTIVE_SUSPEND",
    "SUBGROUP_PCIE",
    "SUBGROUP_PROCESSOR",
    "SUBGROUP_USB",
    "PowerSettingIndex",
    "PowerSettings",
]
