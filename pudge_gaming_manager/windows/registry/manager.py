"""Registry access (rule #51).

Every registry operation in PGM goes through this class. Nothing else opens
a key, so there is exactly one place where a write can happen and exactly
one place that has to get the hard parts right.

The hard parts
--------------
**Absent is not empty.** ``read`` distinguishes "the value does not exist"
from "the value exists and is an empty string". Restoring the wrong one
leaves the machine in a state it was never in, which is the single most
common rollback bug in tools of this kind.

**Type is part of the value.** A ``REG_DWORD`` restored as ``REG_SZ`` is not
a restore. Backups carry the original type and rollback rewrites it.

**Registry redirection is explicit.** A 64-bit process reading
``HKLM\\Software`` sees a different key than a 32-bit one. The view is a
parameter rather than an accident of how PGM happens to be built.

**Paths are allowlisted.** A profile arriving on a USB stick may not name an
arbitrary key (rule #56).
"""

from __future__ import annotations

import winreg
from dataclasses import dataclass
from enum import Enum
from typing import Any

from ...utilities.exceptions import PgmError, RegistryError
from ...utilities.logging_setup import audit_event, get_logger

_log = get_logger(__name__)


class Hive(str, Enum):
    """The registry roots PGM is willing to touch."""

    HKLM = "HKEY_LOCAL_MACHINE"
    HKCU = "HKEY_CURRENT_USER"
    HKCR = "HKEY_CLASSES_ROOT"
    HKU = "HKEY_USERS"

    @property
    def handle(self) -> int:
        return {
            Hive.HKLM: winreg.HKEY_LOCAL_MACHINE,
            Hive.HKCU: winreg.HKEY_CURRENT_USER,
            Hive.HKCR: winreg.HKEY_CLASSES_ROOT,
            Hive.HKU: winreg.HKEY_USERS,
        }[self]

    @property
    def requires_admin(self) -> bool:
        """Writing to a machine-wide hive needs elevation."""
        return self in (Hive.HKLM, Hive.HKCR, Hive.HKU)


class RegistryView(str, Enum):
    """Which registry view to use under WOW64 redirection."""

    DEFAULT = "DEFAULT"
    """Whatever matches this process. Ambiguous — prefer an explicit view."""

    BIT64 = "BIT64"
    BIT32 = "BIT32"

    @property
    def flag(self) -> int:
        return {
            RegistryView.DEFAULT: 0,
            RegistryView.BIT64: winreg.KEY_WOW64_64KEY,
            RegistryView.BIT32: winreg.KEY_WOW64_32KEY,
        }[self]


#: Value types PGM can read, write and restore faithfully.
SUPPORTED_TYPES: dict[int, str] = {
    winreg.REG_SZ: "REG_SZ",
    winreg.REG_EXPAND_SZ: "REG_EXPAND_SZ",
    winreg.REG_DWORD: "REG_DWORD",
    winreg.REG_QWORD: "REG_QWORD",
    winreg.REG_BINARY: "REG_BINARY",
    winreg.REG_MULTI_SZ: "REG_MULTI_SZ",
}

TYPES_BY_NAME: dict[str, int] = {name: code for code, name in SUPPORTED_TYPES.items()}

_USER_OR_MACHINE = frozenset({"HKLM", "HKCU"})

#: Keys a tweak may target, each with the hives it may live under. Anything
#: else is refused before the key is opened (rule #56). Lower-case,
#: backslash-separated, matched on whole path segments.
#:
#: Deliberately absent: ``...\Services`` (a service's ``Start`` value turns
#: Defender off behind ServiceManager's protected list, and ``ImagePath``
#: runs code as SYSTEM) and ``...\CurrentVersion\Run*`` (persistence). No
#: shipped tweak needs either; a future one must argue its way back in with a
#: key as narrow as the setting it changes.
ALLOWED_KEYS: tuple[tuple[frozenset[str], str], ...] = (
    (_USER_OR_MACHINE, r"software\microsoft\windows\currentversion\explorer"),
    (_USER_OR_MACHINE, r"software\microsoft\windows nt\currentversion\multimedia"),
    (_USER_OR_MACHINE, r"software\microsoft\games"),
    (_USER_OR_MACHINE, r"software\microsoft\directx"),
    (frozenset({"HKLM"}), r"system\currentcontrolset\control\graphicsdrivers"),
    # The real key has no space. The earlier spelling, "priority control",
    # matched nothing, so it allowed nothing — harmless, but a write there
    # would have been refused as outside the list.
    (frozenset({"HKLM"}), r"system\currentcontrolset\control\prioritycontrol"),
    (frozenset({"HKCU"}), r"control panel\desktop"),
    # Pointer acceleration is set through SystemParametersInfo, not here;
    # this entry exists so the Mouse key can be *read* for diagnostics
    # without widening anything that writes.
    (frozenset({"HKCU"}), r"control panel\mouse"),
    # Game DVR's two switches. Narrow on purpose: the per-user store and
    # the one policy key, not the whole Policies tree, which is where a
    # mistake would reach Defender and Windows Update.
    (frozenset({"HKCU"}), r"system\gameconfigstore"),
    (frozenset({"HKLM"}), r"software\policies\microsoft\windows\gamedvr"),
    # Game Mode's per-user switch, and the transparency effect. Each is the
    # one key its Settings page writes, not the tree around it.
    (frozenset({"HKCU"}), r"software\microsoft\gamebar"),
    (
        frozenset({"HKCU"}),
        r"software\microsoft\windows\currentversion\themes\personalize",
    ),
)


def is_allowed_key(hive: "Hive", subkey: str) -> bool:
    r"""True when ``hive\subkey`` is at or beneath an allowlisted key.

    Segment-wise, so ``...\games`` does not admit ``...\gamesxyz``; empty
    segments from doubled separators are collapsed first.
    """
    parts = [p for p in subkey.replace("/", "\\").lower().split("\\") if p]
    for hives, allowed in ALLOWED_KEYS:
        if hive.name not in hives:
            continue
        prefix = allowed.split("\\")
        if parts[: len(prefix)] == prefix:
            return True
    return False


@dataclass(frozen=True, slots=True)
class RegistryValue:
    """One registry value, including the fact that it may not exist."""

    hive: Hive
    subkey: str
    name: str
    data: Any = None
    type_name: str = ""
    absent: bool = False
    """True when the value does not exist.

    Distinct from ``data=''``, which means it exists and is empty.
    """

    @property
    def path(self) -> str:
        return f"{self.hive.value}\\{self.subkey}\\{self.name}"


class RegistryManager:
    """Reads and writes registry values, with backup and restore."""

    def __init__(self, *, enforce_allowlist: bool = True) -> None:
        """
        Args:
            enforce_allowlist: Refuse keys outside
                :data:`ALLOWED_KEYS`. Disabled only in tests,
                which operate inside their own temporary key.
        """
        self.enforce_allowlist = enforce_allowlist

    # -- guards ------------------------------------------------------------

    def _check_allowed(self, hive: Hive, subkey: str) -> None:
        if not self.enforce_allowlist:
            return
        if not is_allowed_key(hive, subkey):
            raise RegistryError(
                what=f"Отказано в доступе к ключу «{hive.value}\\{subkey}»",
                reason="Этого ключа нет в списке разрешённых для изменения.",
                remedy=(
                    "Менять можно только настройки, встроенные в Pudge "
                    "Cleaner. Профиль не может добавить новые пути реестра."
                ),
                context={"hive": hive.value, "subkey": subkey},
            )

    # -- read --------------------------------------------------------------

    def exists(
        self,
        hive: Hive,
        subkey: str,
        name: str | None = None,
        view: RegistryView = RegistryView.BIT64,
    ) -> bool:
        """True when the key — or the named value inside it — exists."""
        try:
            with winreg.OpenKey(
                hive.handle, subkey, 0, winreg.KEY_READ | view.flag
            ) as key:
                if name is None:
                    return True
                try:
                    winreg.QueryValueEx(key, name)
                    return True
                except FileNotFoundError:
                    return False
        except FileNotFoundError:
            return False
        except OSError:
            return False

    def read(
        self,
        hive: Hive,
        subkey: str,
        name: str,
        view: RegistryView = RegistryView.BIT64,
    ) -> RegistryValue:
        """Read one value.

        A missing key or value is **not** an error: it returns a
        :class:`RegistryValue` with ``absent=True``, because "not set" is a
        legitimate state that rollback must be able to reproduce.
        """
        try:
            with winreg.OpenKey(
                hive.handle, subkey, 0, winreg.KEY_READ | view.flag
            ) as key:
                data, type_code = winreg.QueryValueEx(key, name)
        except FileNotFoundError:
            return RegistryValue(hive, subkey, name, absent=True)
        except PermissionError as exc:
            raise RegistryError(
                what=f"Не удалось прочитать «{hive.value}\\{subkey}\\{name}»",
                reason="Отказано в доступе.",
                remedy="Запустите Pudge Cleaner от имени администратора.",
                context={"hive": hive.value, "subkey": subkey, "name": name},
            ) from exc
        except OSError as exc:
            raise RegistryError(
                what=f"Не удалось прочитать «{hive.value}\\{subkey}\\{name}»",
                reason=str(exc),
                remedy="Проверьте в редакторе реестра, что настройка существует.",
            ) from exc

        return RegistryValue(
            hive=hive,
            subkey=subkey,
            name=name,
            data=data,
            type_name=SUPPORTED_TYPES.get(type_code, f"UNKNOWN({type_code})"),
            absent=False,
        )

    # -- write -------------------------------------------------------------

    def write(
        self,
        hive: Hive,
        subkey: str,
        name: str,
        data: Any,
        type_name: str,
        view: RegistryView = RegistryView.BIT64,
        *,
        create_missing: bool = True,
    ) -> None:
        """Write one value, creating the key if needed."""
        self._check_allowed(hive, subkey)

        type_code = TYPES_BY_NAME.get(type_name)
        if type_code is None:
            raise RegistryError(
                what=f"Не удалось записать «{name}»",
                reason=f"Тип значения реестра «{type_name}» не поддерживается.",
                remedy=(
                    "Поддерживаются: " + ", ".join(sorted(TYPES_BY_NAME))
                ),
            )

        access = winreg.KEY_SET_VALUE | view.flag
        try:
            if create_missing:
                key = winreg.CreateKeyEx(hive.handle, subkey, 0, access)
            else:
                key = winreg.OpenKey(hive.handle, subkey, 0, access)
            with key:
                winreg.SetValueEx(key, name, 0, type_code, data)
        except PermissionError as exc:
            raise RegistryError(
                what=f"Не удалось изменить «{hive.value}\\{subkey}\\{name}»",
                reason="Отказано в доступе.",
                remedy="Запустите Pudge Cleaner от имени администратора.",
            ) from exc
        except FileNotFoundError as exc:
            raise RegistryError(
                what=f"Не удалось изменить «{hive.value}\\{subkey}\\{name}»",
                reason="Такого ключа реестра нет.",
                remedy="Сделать ничего нельзя: этой настройки на ПК нет.",
            ) from exc
        except OSError as exc:
            raise RegistryError(
                what=f"Не удалось изменить «{hive.value}\\{subkey}\\{name}»",
                reason=str(exc),
                remedy=None,
            ) from exc

        audit_event(
            "registry", "write",
            target=f"{hive.value}\\{subkey}\\{name}",
            new_state={"data": data, "type": type_name},
        )

    def delete_value(
        self,
        hive: Hive,
        subkey: str,
        name: str,
        view: RegistryView = RegistryView.BIT64,
    ) -> bool:
        """Delete one value. Returns ``False`` when it was already absent."""
        self._check_allowed(hive, subkey)
        try:
            with winreg.OpenKey(
                hive.handle, subkey, 0, winreg.KEY_SET_VALUE | view.flag
            ) as key:
                winreg.DeleteValue(key, name)
        except FileNotFoundError:
            return False
        except PermissionError as exc:
            raise RegistryError(
                what=f"Не удалось удалить «{hive.value}\\{subkey}\\{name}»",
                reason="Отказано в доступе.",
                remedy="Запустите Pudge Cleaner от имени администратора.",
            ) from exc
        except OSError as exc:
            raise RegistryError(
                what=f"Не удалось удалить «{hive.value}\\{subkey}\\{name}»",
                reason=str(exc),
            ) from exc

        audit_event(
            "registry", "delete", target=f"{hive.value}\\{subkey}\\{name}"
        )
        return True

    # -- backup / restore --------------------------------------------------

    def backup(
        self,
        hive: Hive,
        subkey: str,
        name: str,
        view: RegistryView = RegistryView.BIT64,
    ) -> RegistryValue:
        """Capture a value's current state, including its absence."""
        return self.read(hive, subkey, name, view)

    def restore(
        self, backup: RegistryValue, view: RegistryView = RegistryView.BIT64
    ) -> None:
        """Put a value back exactly as :meth:`backup` found it.

        A value that did not exist is **deleted**, not written as empty.
        Writing an empty string where nothing existed changes behaviour for
        any code that tests for presence.
        """
        if backup.absent:
            self.delete_value(backup.hive, backup.subkey, backup.name, view)
            audit_event(
                "registry", "restore",
                target=backup.path,
                new_state="absent (value removed)",
            )
            return

        self.write(
            backup.hive,
            backup.subkey,
            backup.name,
            backup.data,
            backup.type_name,
            view,
        )
        audit_event(
            "registry", "restore",
            target=backup.path,
            new_state={"data": backup.data, "type": backup.type_name},
        )


def parse_hive(text: str) -> Hive:
    """Resolve a hive name, accepting the usual abbreviations."""
    lookup = {
        "hklm": Hive.HKLM, "hkey_local_machine": Hive.HKLM,
        "hkcu": Hive.HKCU, "hkey_current_user": Hive.HKCU,
        "hkcr": Hive.HKCR, "hkey_classes_root": Hive.HKCR,
        "hku": Hive.HKU, "hkey_users": Hive.HKU,
    }
    hive = lookup.get(text.strip().lower())
    if hive is None:
        raise PgmError(
            what=f"Неизвестный раздел реестра «{text}»",
            reason="Поддерживаются только HKLM, HKCU, HKCR и HKU.",
            remedy="Исправьте профиль и повторите.",
        )
    return hive
