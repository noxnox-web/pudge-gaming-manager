"""Tests for registry and service management.

Registry tests write only inside a temporary key under HKCU that the test
creates and deletes. No shipped registry path is ever written to.

Service tests are read-only or use fakes. Nothing here starts, stops or
reconfigures a real service (rule #48).
"""

from __future__ import annotations

import os
import uuid

import pytest

windows_only = pytest.mark.skipif(os.name != "nt", reason="Windows-only behaviour")
pytestmark = windows_only

import winreg  # noqa: E402  (guarded by the skip above)

from pudge_gaming_manager.utilities.exceptions import (  # noqa: E402
    ProtectedResourceError,
    RegistryError,
    ServiceError,
)
from pudge_gaming_manager.windows.registry.manager import (  # noqa: E402
    Hive,
    RegistryManager,
    RegistryView,
    parse_hive,
)
from pudge_gaming_manager.windows.services.manager import (  # noqa: E402
    PROTECTED_SERVICES,
    ServiceManager,
    ServiceState,
    StartupType,
)


@pytest.fixture()
def scratch_key():
    """A throwaway HKCU key, removed afterwards."""
    subkey = f"Software\\PudgeGamingManagerTests\\{uuid.uuid4().hex}"
    winreg.CreateKey(winreg.HKEY_CURRENT_USER, subkey)
    yield subkey
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, subkey)
    except OSError:
        pass


@pytest.fixture()
def registry() -> RegistryManager:
    # The allowlist protects shipped paths; these tests use their own key.
    return RegistryManager(enforce_allowlist=False)


# -- registry: absent vs empty --------------------------------------------


def test_missing_value_reports_absent_not_empty(
    registry: RegistryManager, scratch_key: str
) -> None:
    value = registry.read(Hive.HKCU, scratch_key, "NoSuchValue")
    assert value.absent
    assert value.data is None


def test_empty_string_is_not_absent(
    registry: RegistryManager, scratch_key: str
) -> None:
    """The distinction the whole rollback design depends on."""
    registry.write(Hive.HKCU, scratch_key, "Empty", "", "REG_SZ")
    value = registry.read(Hive.HKCU, scratch_key, "Empty")

    assert not value.absent
    assert value.data == ""


def test_missing_key_reports_absent(registry: RegistryManager) -> None:
    value = registry.read(
        Hive.HKCU, "Software\\PudgeGamingManagerTests\\NoSuchKey", "X"
    )
    assert value.absent


# -- registry: types round-trip -------------------------------------------


@pytest.mark.parametrize(
    ("name", "data", "type_name"),
    [
        ("AString", "hello", "REG_SZ"),
        ("ADword", 42, "REG_DWORD"),
        ("AQword", 2**33, "REG_QWORD"),
        ("ABinary", b"\x01\x02\x03", "REG_BINARY"),
        ("AMulti", ["one", "two"], "REG_MULTI_SZ"),
        ("AnExpand", "%TEMP%\\x", "REG_EXPAND_SZ"),
        ("Cyrillic", "Максимальная", "REG_SZ"),
    ],
)
def test_value_types_round_trip(
    registry: RegistryManager, scratch_key: str, name, data, type_name
) -> None:
    registry.write(Hive.HKCU, scratch_key, name, data, type_name)
    value = registry.read(Hive.HKCU, scratch_key, name)

    assert value.data == data
    assert value.type_name == type_name


def test_unsupported_type_is_refused(
    registry: RegistryManager, scratch_key: str
) -> None:
    with pytest.raises(RegistryError, match="не поддерживается"):
        registry.write(Hive.HKCU, scratch_key, "X", "y", "REG_NONSENSE")


# -- registry: backup and restore -----------------------------------------


def test_restore_puts_back_value_and_type(
    registry: RegistryManager, scratch_key: str
) -> None:
    registry.write(Hive.HKCU, scratch_key, "Setting", 1, "REG_DWORD")
    backup = registry.backup(Hive.HKCU, scratch_key, "Setting")

    registry.write(Hive.HKCU, scratch_key, "Setting", 999, "REG_DWORD")
    registry.restore(backup)

    restored = registry.read(Hive.HKCU, scratch_key, "Setting")
    assert restored.data == 1
    assert restored.type_name == "REG_DWORD"


def test_restoring_an_absent_value_deletes_it(
    registry: RegistryManager, scratch_key: str
) -> None:
    """Writing an empty string where nothing existed is not a restore."""
    backup = registry.backup(Hive.HKCU, scratch_key, "WasNeverThere")
    assert backup.absent

    registry.write(Hive.HKCU, scratch_key, "WasNeverThere", "now set", "REG_SZ")
    assert not registry.read(Hive.HKCU, scratch_key, "WasNeverThere").absent

    registry.restore(backup)

    assert registry.read(Hive.HKCU, scratch_key, "WasNeverThere").absent
    assert not registry.exists(Hive.HKCU, scratch_key, "WasNeverThere")


def test_restore_of_empty_string_keeps_it_present(
    registry: RegistryManager, scratch_key: str
) -> None:
    registry.write(Hive.HKCU, scratch_key, "Empty", "", "REG_SZ")
    backup = registry.backup(Hive.HKCU, scratch_key, "Empty")

    registry.write(Hive.HKCU, scratch_key, "Empty", "changed", "REG_SZ")
    registry.restore(backup)

    value = registry.read(Hive.HKCU, scratch_key, "Empty")
    assert not value.absent, "an existing empty value must not become absent"
    assert value.data == ""


# -- registry: guards ------------------------------------------------------


def test_allowlist_refuses_an_arbitrary_key() -> None:
    """A profile may not introduce new registry paths (rule #56)."""
    guarded = RegistryManager(enforce_allowlist=True)
    with pytest.raises(RegistryError) as excinfo:
        guarded.write(
            Hive.HKLM, "SOFTWARE\\Attacker\\Payload", "X", "y", "REG_SZ"
        )
    assert "нет в списке разрешённых" in excinfo.value.reason


@pytest.mark.parametrize(
    ("hive", "subkey"),
    [
        # Code-execution and persistence primitives are not on the list.
        (Hive.HKLM, r"SYSTEM\CurrentControlSet\Services\WinDefend"),
        (Hive.HKLM, r"SYSTEM\CurrentControlSet\Services\Spooler\ImagePath"),
        (Hive.HKCU, r"Software\Microsoft\Windows\CurrentVersion\Run"),
        (Hive.HKLM, r"Software\Microsoft\Windows\CurrentVersion\RunOnce"),
        # A shared name prefix is not a shared path segment.
        (Hive.HKCU, r"Software\Microsoft\GamesXYZ"),
        # The right key under the wrong hive.
        (Hive.HKCU, r"SYSTEM\CurrentControlSet\Control\GraphicsDrivers"),
        (Hive.HKLM, r"Control Panel\Desktop"),
    ],
)
def test_allowlist_refuses_dangerous_or_lookalike_keys(hive, subkey) -> None:
    from pudge_gaming_manager.windows.registry.manager import is_allowed_key

    assert not is_allowed_key(hive, subkey)


def test_allowlist_accepts_a_subkey_and_doubled_separators() -> None:
    from pudge_gaming_manager.windows.registry.manager import is_allowed_key

    assert is_allowed_key(Hive.HKLM, r"SYSTEM\CurrentControlSet\Control\GraphicsDrivers")
    assert is_allowed_key(Hive.HKCU, r"Software\Microsoft\Games\\Sub/Key")


def test_allowlist_permits_a_shipped_prefix(
    scratch_key: str
) -> None:
    guarded = RegistryManager(enforce_allowlist=True)
    # Does not write; only proves the guard accepts the path.
    guarded._check_allowed(
        Hive.HKCU, "Software\\Microsoft\\Windows\\CurrentVersion\\Explorer\\Advanced"
    )


def test_delete_of_a_missing_value_is_not_an_error(
    registry: RegistryManager, scratch_key: str
) -> None:
    assert registry.delete_value(Hive.HKCU, scratch_key, "Nope") is False


def test_parse_hive_accepts_abbreviations() -> None:
    assert parse_hive("HKLM") is Hive.HKLM
    assert parse_hive("hkey_current_user") is Hive.HKCU


def test_parse_hive_rejects_nonsense() -> None:
    from pudge_gaming_manager.utilities.exceptions import PgmError

    with pytest.raises(PgmError, match="Неизвестный раздел реестра"):
        parse_hive("HKEY_MADE_UP")


def test_registry_view_flags_differ() -> None:
    """32- and 64-bit views must not silently collapse together."""
    assert RegistryView.BIT64.flag != RegistryView.BIT32.flag


# -- services: protection --------------------------------------------------


@pytest.mark.parametrize(
    "service",
    ["WinDefend", "MpsSvc", "EasyAntiCheat", "vgc", "SmartShell", "RpcSs"],
)
def test_protected_services_cannot_be_reconfigured(service: str) -> None:
    manager = ServiceManager()
    with pytest.raises(ProtectedResourceError):
        manager._require_modifiable(service, "reconfigure")


def test_protection_is_case_insensitive() -> None:
    manager = ServiceManager()
    with pytest.raises(ProtectedResourceError):
        manager._require_modifiable("windefend", "stop")


def test_security_services_are_protected() -> None:
    """Rule #64: never disable Defender or the firewall for performance."""
    for name in ("windefend", "mpssvc", "securityhealthservice", "bfe"):
        assert name in PROTECTED_SERVICES


def test_anticheat_services_are_protected() -> None:
    """Breaking anti-cheat gets players banned."""
    for name in ("easyanticheat", "beservice", "vgc"):
        assert name in PROTECTED_SERVICES


def test_unknown_service_reports_clearly() -> None:
    manager = ServiceManager()
    with pytest.raises(ServiceError) as excinfo:
        manager._require_modifiable("PgmNoSuchServiceXyz", "stop")
    assert "не установлена" in excinfo.value.reason


# -- services: read-only against the live machine -------------------------


def test_live_service_listing_is_populated() -> None:
    services = ServiceManager().list_services()
    assert len(services) > 20
    assert all(s.name for s in services)


def test_live_known_service_is_readable() -> None:
    info = ServiceManager().get_status("EventLog")
    assert info is not None
    assert info.name.lower() == "eventlog"
    assert info.state is not ServiceState.UNKNOWN
    assert info.startup_type is not StartupType.UNKNOWN


def test_live_missing_service_returns_none() -> None:
    assert ServiceManager().get_status("PgmNoSuchServiceXyz") is None


def test_live_protected_services_are_flagged() -> None:
    services = {s.name.lower(): s for s in ServiceManager().list_services()}
    defender = services.get("windefend")
    if defender is not None:
        assert defender.is_protected
