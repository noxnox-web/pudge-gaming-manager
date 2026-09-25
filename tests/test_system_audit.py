"""Device tweaks, the system audit and its probes, startup protection, load recording.

Every probe is a fake: no PowerShell, no WMI, no registry write (rule #48).
"""

from __future__ import annotations

import json
import struct
from dataclasses import replace
from datetime import date

import pytest

from pudge_gaming_manager.core.audit import policy
from pudge_gaming_manager.core.audit.report import Level, Probes, build_report
from pudge_gaming_manager.core.benchmark.load_monitor import LoadReport, Sample, Stats
from pudge_gaming_manager.core.optimization.tweak import Outcome, TweakContext
from pudge_gaming_manager.core.optimization.tweaks.devices import (
    DisableEnergyEfficientEthernetTweak,
    DisableUsbPowerSavingTweak,
)
from pudge_gaming_manager.core.optimization.tweaks.power import (
    SetPowerPlanTweak,
    needs_balanced_scheme,
)
from pudge_gaming_manager.hardware.cpu import topology
from pudge_gaming_manager.network.adapter.advanced import AdvancedProperty, NetworkAdapter
from pudge_gaming_manager.utilities.exceptions import PgmError
from pudge_gaming_manager.windows.devices import usb
from pudge_gaming_manager.windows.devices.msi import MsiState
from pudge_gaming_manager.windows.devices.power import DevicePowerState
from pudge_gaming_manager.windows.security import status as security
from pudge_gaming_manager.windows.startup import publisher
from pudge_gaming_manager.windows.startup.manager import (
    StartupEntry,
    StartupManager,
    StartupSource,
)


# -- device power -----------------------------------------------------------


class FakeDevices:
    def __init__(self, states: dict[str, bool]) -> None:
        self.states = dict(states)
        self.writes: list[tuple[list[str], bool]] = []

    def read(self) -> list[DevicePowerState]:
        return [DevicePowerState(f"{k}_0", v) for k, v in self.states.items()]

    def write(self, names: list[str], enabled: bool) -> list[str]:
        self.writes.append((list(names), enabled))
        for name in names:
            self.states[name[:-2]] = enabled
        return []


def test_usb_power_saving_touches_only_usb_devices_that_have_it_on() -> None:
    fake = FakeDevices({
        r"USB\ROOT_HUB30\4&1": True,
        r"USB\VID_1D57&PID_FA60&MI_00\6&1": True,
        r"USB\VID_0951&PID_16E6&MI_00\6&2": False,  # already off: left alone
        r"PCI\VEN_8086&DEV_43ED\3&1": True,          # a controller: not USB\
    })
    tweak = DisableUsbPowerSavingTweak(fake.read, fake.write)
    ctx = TweakContext()

    state = tweak.scan(ctx)
    backup = tweak.backup(ctx, state)
    assert tweak.apply(ctx, state).outcome is Outcome.SUCCESS
    assert tweak.verify(ctx, state).confirmed

    changed = json.loads(state.current_value)
    assert len(changed) == 2 and all(n.upper().startswith("USB\\") for n in changed)

    tweak.rollback(ctx, backup)
    assert fake.states[r"USB\VID_0951&PID_16E6&MI_00\6&2"] is False
    assert fake.states[r"PCI\VEN_8086&DEV_43ED\3&1"] is True
    assert fake.writes[-1] == (changed, True)


def test_nothing_to_change_is_not_a_change() -> None:
    fake = FakeDevices({r"USB\ROOT_HUB30\4&1": False})

    assert not DisableUsbPowerSavingTweak(fake.read, fake.write).scan(TweakContext()).needs_change


def _adapter(name: str, wired: bool, props: dict[str, tuple[str, str]]) -> NetworkAdapter:
    return NetworkAdapter(
        name=name, description=name, status="Up", link_speed="1 Gbps", wired=wired,
        pnp_id=f"PCI\\{name}", driver_version="1", driver_date="2024-01-01",
        driver_provider="Realtek", ndis_version="6.80",
        properties=tuple(
            AdvancedProperty(k, k, value, value, tuple(valid.split(",")))
            for k, (value, valid) in props.items()
        ),
    )


def test_eee_is_turned_off_only_where_the_driver_accepts_zero() -> None:
    adapters = [
        _adapter("Ethernet", True, {"*EEE": ("1", "0,1"), "*InterruptModeration": ("1", "0,1")}),
        _adapter("Ethernet 2", True, {"EnableGreenEthernet": ("1", "1,2")}),  # no 0: skip
        _adapter("Wi-Fi", False, {"*EEE": ("1", "0,1")}),                     # not wired
    ]
    written: list[tuple[str, str, str]] = []

    def setter(adapter, keyword, value):
        """Store the value, as the driver would, so verify can read it back."""
        written.append((adapter, keyword, value))
        for index, a in enumerate(adapters):
            if a.name == adapter:
                props = tuple(
                    replace(p, value=value) if p.keyword == keyword else p
                    for p in a.properties
                )
                adapters[index] = replace(a, properties=props)

    tweak = DisableEnergyEfficientEthernetTweak(lambda: adapters, setter)
    ctx = TweakContext()
    state = tweak.scan(ctx)
    backup = tweak.backup(ctx, state)
    result = tweak.apply(ctx, state)

    assert written == [("Ethernet", "*EEE", "0")]
    assert result.needs_restart
    assert tweak.verify(ctx, state).confirmed
    tweak.rollback(ctx, backup)
    assert written[-1] == ("Ethernet", "*EEE", "1")


# -- USB inventory ----------------------------------------------------------


CTRL = r"PCI\VEN_8086&DEV_43ED\3&1"


def test_usb_inventory_names_each_input_device_once_with_its_product() -> None:
    rows = [
        {"Kind": "controller", "Id": CTRL, "Name": "xHCI"},
        {"Kind": "device", "Id": r"USB\VID_1D57&PID_FA60\5&9", "Controller": CTRL,
         "Name": "Составное USB устройство", "Class": "USB", "Product": "2.4G Wireless Device"},
        {"Kind": "device", "Id": r"HID\VID_1D57&PID_FA60&MI_01\7&1", "Controller": CTRL,
         "Name": "HID-совместимая мышь", "Class": "Mouse", "Product": ""},
        {"Kind": "device", "Id": r"HID\VID_1D57&PID_FA60&MI_02&COL01\7&2", "Controller": CTRL,
         "Name": "HID-совместимая мышь", "Class": "Mouse", "Product": ""},
        {"Kind": "device", "Id": r"USB\VID_1D57&PID_FA60&MI_00\6&1", "Controller": CTRL,
         "Name": "", "Class": "HIDClass", "Product": ""},
    ]

    inventory = usb.build(rows, msi_reader=lambda _id: MsiState(True, 1))

    [mouse] = inventory.inputs
    assert mouse.kind == "мышь" and mouse.product == "2.4G Wireless Device"
    assert mouse.vid_pid == "1D57:FA60"
    assert inventory.devices_per_controller[CTRL] == 1  # interfaces are not devices
    assert inventory.controller(CTRL.lower()) is not None


# -- security ---------------------------------------------------------------


def test_disabled_defender_and_update_service_are_findings() -> None:
    status = security.build(
        {"DefenderRealtime": False, "DefenderService": True, "UpdateService": "Disabled",
         "TpmPresent": True, "TpmEnabled": True, "TpmSpec": "2.0, 0, 1.59"},
        {"secure_boot": 1, "hvci_enabled": 0, "update_reboot_pending": False},
    )
    by_name = {f.name: f for f in status.findings}

    assert by_name["Защитник Windows"].ok is False
    assert "ОТКЛЮЧЕНА" in by_name["Центр обновления Windows"].value
    assert by_name["TPM"].value == "включён, версия 2.0"


def test_a_third_party_antivirus_is_not_flagged_as_unprotected() -> None:
    status = security.build(
        {"DefenderRealtime": False, "Antivirus": "Kaspersky"}, {"secure_boot": 1}
    )
    defender = next(f for f in status.findings if f.name == "Защитник Windows")

    assert defender.ok is True


# -- the audit --------------------------------------------------------------


def _probes(**overrides) -> Probes:
    base = dict(
        settings=lambda: [],
        topology=lambda: topology.CpuTopology(12, 6, 6, 0, 1),
        usb=lambda: usb.UsbInventory(),
        adapters=lambda: [],
        device_power=lambda: [],
        gpu_ids=lambda: [],
        msi_read=lambda _i: MsiState(True),
        security=lambda: security.SecurityStatus(()),
        mouse=lambda: None,
        power_scheme=lambda: None,
        power_schemes=lambda: [],
        startup=lambda: [],
        today=lambda: date(2026, 9, 25),
    )
    base.update(overrides)
    return Probes(**base)


def test_a_failing_probe_becomes_a_line_not_a_crash() -> None:
    def broken():
        raise PgmError(what="WMI", reason="служба не отвечает")

    report = build_report(None, _probes(usb=broken, security=broken))

    text = report.text()
    assert "служба не отвечает" in text
    assert "Намеренно не меняется".upper() in text


def test_an_old_generic_network_driver_needs_attention() -> None:
    old = NetworkAdapter(
        "Ethernet", "Realtek PCIe GbE", "Up", "1 Gbps", True, "PCI\\X",
        "9.1.410.2015", "2015-04-10", "Microsoft", "6.40",
    )

    report = build_report(None, _probes(adapters=lambda: [old]))
    driver = next(i for s in report.sections for i in s.items if i.name.endswith("драйвер"))

    assert driver.level is Level.ATTENTION
    assert "от Microsoft" in driver.note


def test_manual_steps_follow_the_gpu_vendor() -> None:
    assert policy.manual_steps(["NVIDIA"]) and policy.manual_steps(["AMD"])
    assert policy.manual_steps(["Intel"]) == []


def test_the_exclusions_cover_what_the_guides_argue_about() -> None:
    names = " ".join(e.name for e in policy.NOT_CHANGED)
    for topic in ("Защитника", "обновления", "HPET", "ISLC", "affinity", "Realtime", "MSI"):
        assert topic in names, topic


# -- CPU topology and the power plan -----------------------------------------


def _record(core: int, llc: int, efficiency: int) -> bytes:
    body = struct.pack("<IIIHBBBBBB", 32, 0, 0, 0, 0, core, llc, 0, efficiency, 0)
    return body + bytes(32 - len(body))


def test_topology_counts_hybrid_cores_and_cache_domains() -> None:
    # Two P-cores with two threads each, two E-cores, two cache domains.
    buffer = b"".join([
        _record(0, 0, 1), _record(0, 0, 1), _record(1, 0, 1), _record(1, 0, 1),
        _record(2, 1, 0), _record(3, 1, 0),
    ])

    topo = topology.parse(buffer)

    assert (topo.logical, topo.physical) == (6, 4)
    assert (topo.performance_cores, topo.efficiency_cores) == (2, 2)
    assert topo.cache_domains == 2 and topo.smt and topo.hybrid


@pytest.mark.parametrize(
    "model,balanced",
    [
        ("AMD Ryzen 9 7950X3D 16-Core Processor", True),
        ("AMD Ryzen 9 9900X3D 12-Core Processor", True),
        ("AMD Ryzen 7 7800X3D 8-Core Processor", False),
        ("11th Gen Intel(R) Core(TM) i5-11400F", False),
    ],
)
def test_dual_ccd_x3d_keeps_the_balanced_scheme(model: str, balanced: bool) -> None:
    assert needs_balanced_scheme(model) is balanced


def test_the_power_plan_tweak_refuses_on_a_dual_ccd_x3d() -> None:
    from pudge_gaming_manager.core.optimization.tweak import TweakState

    tweak = SetPowerPlanTweak(cpu_model="AMD Ryzen 9 7950X3D 16-Core Processor")

    validation = tweak.validate(TweakContext(), TweakState(needs_change=True))

    assert not validation.ok and "Сбалансированная" in validation.reason


# -- startup ----------------------------------------------------------------


@pytest.mark.parametrize(
    "command,expected",
    [
        (r'"C:\Program Files\App\app.exe" --tray', r"C:\Program Files\App\app.exe"),
        (r"C:\Tools\tool.exe /silent", r"C:\Tools\tool.exe"),
        (r"rundll32.exe C:\Drivers\x.dll,Start", r"C:\Drivers\x.dll"),
        ("", None),
    ],
)
def test_the_program_behind_a_command_line(command: str, expected: str | None) -> None:
    assert publisher.executable(command) == expected


def test_club_software_and_the_security_icon_are_protected() -> None:
    assert publisher.protected_reason("SecurityHealth", r"%windir%\system32\SecurityHealthSystray.exe")
    assert publisher.protected_reason("Client", r"C:\SmartShell\client.exe")
    assert publisher.protected_reason("Spotify", r"C:\Spotify.exe") == ""


def test_a_protected_entry_is_refused_where_the_write_happens() -> None:
    class NoWrites:
        def write(self, *_a, **_k):
            raise AssertionError("must not write")

    entry = StartupEntry(
        "SecurityHealth", "x", StartupSource.RUN_MACHINE, True,
        protected_reason="значок",
    )

    with pytest.raises(PgmError):
        StartupManager(registry=NoWrites()).set_enabled(entry, False)  # type: ignore[arg-type]


# -- load recording ---------------------------------------------------------


def test_stats_are_plain_order_statistics() -> None:
    stats = Stats.of([float(v) for v in range(1, 101)])

    assert (stats.low, stats.high) == (1, 100)
    assert 95 <= stats.p95 <= 96
    assert Stats.of([]) is None


def test_a_cpu_limited_recording_says_so_and_never_claims_fps() -> None:
    samples = [
        Sample(at=i, cpu_total=40, cpu_max_core=99, ram_percent=92, gpu_util=60)
        for i in range(10)
    ]
    report = LoadReport(samples=samples)

    findings = " ".join(report.findings())

    assert "упор в процессор" in findings
    assert "Память доходила до 92%" in findings
    assert "FPS и время кадра не измерялись" in findings
    assert report.duration_s == 9


# -- restore point ----------------------------------------------------------


def test_the_pipeline_asks_for_a_restore_point_only_for_a_real_run(tmp_path) -> None:
    from pudge_gaming_manager.core.optimization.engine import Plan
    from pudge_gaming_manager.core.optimization.pipeline import (
        OptimizationPipeline,
        OptimizationPreview,
    )
    from pudge_gaming_manager.core.scoring.score import GamingScore
    from pudge_gaming_manager.database.connection import Database

    calls: list[int] = []
    pipeline = OptimizationPipeline(
        Database(tmp_path / "r.db"),
        restore_point=lambda: (calls.append(1), (True, "создана"))[1],
    )
    empty = OptimizationPreview(Plan(run_id="x", changes=()), GamingScore(value=None))

    pipeline.apply(empty)
    assert calls == []  # nothing to change, nothing to protect
