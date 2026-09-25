"""The fast refresh, and the honesty it must not trade away for speed.

A refresh reuses the hardware *inventory* and skips the network scan. That
is only defensible if nothing carried over is presented as a fresh
measurement, so most of these tests are about what the refresh is *not*
allowed to do: invent a ping, keep a stale processor load, or answer the
rescan button with a shortcut.
"""

from __future__ import annotations

import pathlib
import sys
import time
from typing import Any

import pytest

from pudge_gaming_manager.core.scanner.hardware_scan import HardwareScanner
from pudge_gaming_manager.hardware.models import HardwareSnapshot

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))


class CountingPowerShell:
    """Stands in for the inventory call, counting how often it is made."""

    def __init__(self) -> None:
        self.calls = 0

    def run_json(self, *_args: Any, **_kwargs: Any) -> list[dict[str, Any]]:
        self.calls += 1
        return [
            {
                "Processor": [{"Name": f"Test CPU {self.calls}"}],
                "Memory": [],
                "Video": [],
                "Physical": [],
                "VolumeMap": [],
            }
        ]


def _scanner() -> tuple[HardwareScanner, CountingPowerShell]:
    powershell = CountingPowerShell()
    return HardwareScanner(powershell=powershell, use_wmi=False), powershell  # type: ignore[arg-type]


# -- the inventory is read once ---------------------------------------------


def test_a_refresh_does_not_re_read_the_inventory() -> None:
    """The whole point: that PowerShell call is 2.3 of the 2.4 seconds."""
    scanner, powershell = _scanner()

    scanner.scan()
    assert powershell.calls == 1

    scanner.scan(reuse_inventory=True)
    scanner.scan(reuse_inventory=True)
    assert powershell.calls == 1


def test_a_full_scan_always_re_reads_it() -> None:
    """The rescan button must not be answered with a cached answer.

    Hardware really can appear mid-session — a drive attached, a monitor
    plugged in — and an explicit rescan is how the operator asks about it.
    """
    scanner, powershell = _scanner()

    scanner.scan()
    scanner.scan(reuse_inventory=True)
    scanner.scan()

    assert powershell.calls == 2


def test_the_first_scan_reads_it_even_when_reuse_is_requested() -> None:
    """There is nothing to reuse yet, so it must fall back, not fail."""
    scanner, powershell = _scanner()

    snapshot = scanner.scan(reuse_inventory=True)

    assert powershell.calls == 1
    assert isinstance(snapshot, HardwareSnapshot)


def test_a_failed_inventory_is_not_cached_as_an_answer() -> None:
    """An empty result is a failure, and caching it would make it permanent."""

    class Failing:
        def __init__(self) -> None:
            self.calls = 0

        def run_json(self, *_a: Any, **_k: Any) -> list[dict[str, Any]]:
            self.calls += 1
            return []

    failing = Failing()
    scanner = HardwareScanner(powershell=failing, use_wmi=False)  # type: ignore[arg-type]

    scanner.scan()
    scanner.scan(reuse_inventory=True)

    assert failing.calls == 2


# -- what must still be measured --------------------------------------------


def test_a_refresh_takes_a_new_timestamp() -> None:
    scanner, _ = _scanner()
    first = scanner.scan()
    time.sleep(1.1)  # the timestamp has one-second resolution

    second = scanner.scan(reuse_inventory=True)

    assert second.captured_at != first.captured_at


def test_a_refresh_re_measures_free_disk_space(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The one number a cleanup actually changes."""
    scanner, _ = _scanner()
    scanner.scan()

    from pudge_gaming_manager.hardware import storage

    calls: list[int] = []
    real = storage.scan_disks
    monkeypatch.setattr(
        storage,
        "scan_disks",
        lambda *a, **k: (calls.append(1), real(*a, **k))[1],
    )

    scanner.scan(reuse_inventory=True)

    assert calls, "a refresh that does not re-read the disks is useless"


def test_a_refresh_re_reads_the_display_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A refresh-rate tweak changes this, and OPTIMIZE verifies through it."""
    scanner, _ = _scanner()
    scanner.scan()

    from pudge_gaming_manager.core.scanner import hardware_scan

    calls: list[int] = []
    monkeypatch.setattr(
        hardware_scan.display,
        "scan_monitors",
        lambda *a, **k: (calls.append(1), [])[1],
    )

    scanner.scan(reuse_inventory=True)

    assert calls


def test_a_refresh_is_much_faster_than_a_full_scan() -> None:
    """Pins the property the whole change exists for.

    Deliberately a loose bound. The absolute numbers depend on the machine;
    what must hold is that a refresh does not pay for the inventory call.
    """
    scanner, powershell = _scanner()
    scanner.scan()

    before = powershell.calls
    for _ in range(20):
        scanner.scan(reuse_inventory=True)

    assert powershell.calls == before


# -- the network is carried, never invented ---------------------------------


def test_the_refresh_worker_does_not_touch_the_network() -> None:
    """A cleanup cannot change the ping, so measuring it again is waste.

    More importantly, a refresh must not *pretend* to have measured it.
    """
    from pudge_gaming_manager.app.controllers.scan_controller import ScanWorker
    from pudge_gaming_manager.network.models import NetworkSnapshot

    class ExplodingNetwork:
        def scan(self) -> NetworkSnapshot:
            raise AssertionError("a refresh must not scan the network")

    previous = NetworkSnapshot()
    scanner, _ = _scanner()
    scanner.scan()

    worker = ScanWorker(
        scanner=scanner,
        network_scanner=ExplodingNetwork(),  # type: ignore[arg-type]
        quick=True,
        previous_network=previous,
    )

    published: list[Any] = []
    worker.finished.connect(published.append)
    worker.failed.connect(lambda message: pytest.fail(message))
    worker.run()

    assert len(published) == 1
    # The same object, not a rebuilt one: nothing was re-measured, and the
    # dashboard keeps showing exactly the reading it was already showing.
    assert published[0].network is previous


def test_a_full_scan_does_scan_the_network() -> None:
    from pudge_gaming_manager.app.controllers.scan_controller import ScanWorker
    from pudge_gaming_manager.network.models import NetworkSnapshot

    class CountingNetwork:
        def __init__(self) -> None:
            self.calls = 0

        def scan(self) -> NetworkSnapshot:
            self.calls += 1
            return NetworkSnapshot()

    network = CountingNetwork()
    scanner, _ = _scanner()

    worker = ScanWorker(
        scanner=scanner,
        network_scanner=network,  # type: ignore[arg-type]
        quick=False,
    )
    worker.finished.connect(lambda _result: None)
    worker.run()

    assert network.calls == 1
