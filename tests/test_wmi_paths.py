"""The in-process WMI readers: their parsing, and their fallback.

``wmi.query`` is replaced by a fake returning what COM hands over on a real
machine (captured on the development PC), so these run without WMI.
"""

from __future__ import annotations

import pytest

from pudge_gaming_manager.core.scanner import hardware_scan
from pudge_gaming_manager.network.adapter import advanced
from pudge_gaming_manager.utilities import wmi
from pudge_gaming_manager.windows.devices import usb
from pudge_gaming_manager.windows.security import status


def test_an_association_reference_becomes_a_device_id() -> None:
    # As COM hands it over: backslashes inside the quoted key are doubled.
    ref = (
        r'\\HOME-PC\root\cimv2:Win32_PnPEntity.DeviceID='
        r'"USB\\VID_0951&PID_16E6\\5&244075AE&0&2"'
    )
    assert usb._reference_id(ref) == r"USB\VID_0951&PID_16E6\5&244075AE&0&2"
    assert usb._reference_id("") == ""


@pytest.mark.parametrize(
    "bps,text",
    [("1000000000", "1 Gbps"), ("100000000", "100 Mbps"), ("2500000000", "2.5 Gbps"),
     ("0", ""), (None, ""), ("junk", "")],
)
def test_link_speed_reads_like_get_netadapter(bps, text) -> None:
    assert advanced._link_speed(bps) == text


def test_drive_letters_arrive_as_char_codes(monkeypatch) -> None:
    monkeypatch.setattr(wmi, "query", lambda q: {
        "Processor": [], "Memory": [], "Video": [], "Physical": [],
        "Partitions": [{"DriveLetter": 67, "DiskNumber": 0},
                       {"DriveLetter": 0, "DiskNumber": 0},  # no letter: skipped
                       {"DriveLetter": 68, "DiskNumber": 1}],
    })
    rows = hardware_scan._fetch_wmi()
    assert rows["VolumeMap"] == [{"Drive": "C:", "Index": 0}, {"Drive": "D:", "Index": 1}]


def test_a_failed_wmi_read_falls_back_to_powershell(monkeypatch) -> None:
    def broken(_q):
        raise wmi.WmiError(what="WMI", reason="RPC unavailable")

    monkeypatch.setattr(wmi, "query", broken)

    class FakePowerShell:
        calls = 0

        def run_json(self, *_a, **_k):
            FakePowerShell.calls += 1
            return [{"Processor": [{"Name": "CPU"}], "Memory": [], "Video": [],
                     "Physical": [], "VolumeMap": []}]

    scanner = hardware_scan.HardwareScanner(powershell=FakePowerShell())  # type: ignore[arg-type]
    rows = scanner._fetch_cim([])
    assert FakePowerShell.calls == 1 and rows["Processor"][0]["Name"] == "CPU"


def test_security_sources_fail_one_at_a_time(monkeypatch) -> None:
    def query(q):
        key = next(iter(q))
        if key == "tpm":
            raise wmi.WmiError(what="WMI", reason="Access denied")
        return {
            "vbs": {"vbs": [{"VirtualizationBasedSecurityStatus": 2,
                             "SecurityServicesRunning": (0, 2)}]},
            "defender": {"defender": [{"AMServiceEnabled": True,
                                       "RealTimeProtectionEnabled": True}]},
            "antivirus": {"antivirus": [{"displayName": "Windows Defender"}]},
            "update": {"update": [{"StartMode": "Disabled"}]},
        }[key]

    monkeypatch.setattr(wmi, "query", query)
    cim = status._cim_via_wmi()

    assert cim["ServicesRunning"] == "0,2"
    assert cim["TpmError"] == "Access denied"
    assert cim["DefenderRealtime"] is True
    assert cim["UpdateService"] == "Disabled"  # spelled as Get-Service does
