"""Which USB controller each input device hangs off, read from WMI.

What the guides ask an operator to check by hand in Device Manager: is the
mouse on its own controller, or sharing one with a webcam and a headset;
is the controller running with message-signalled interrupts; may Windows
power the device down. All of it is available without a click:

* ``Win32_USBControllerDevice`` links every device — hubs, composite
  devices, and the HID children a mouse actually appears as — to its host
  controller. Verified on real hardware: the HID nodes are included.
* ``Win32_PnPEntity.PNPClass`` says which of them are mice and keyboards,
  independent of the Windows display language.
* ``DEVPKEY_Device_BusReportedDeviceDesc`` is the product name the device
  itself reports ("2.4G Wireless Device"), where the PnP name is only a
  generic "HID-compliant mouse".

What is not here
----------------
**Polling rate.** Windows exposes the endpoint's requested interval only to
drivers, and what a gaming mouse actually delivers is set in its own
firmware. Reporting a number here would be reporting a guess, so the audit
says so instead.
"""

from __future__ import annotations

import ctypes
import re
import uuid
from dataclasses import dataclass, field

from ...utilities import wmi
from ...utilities.command_runner import CommandRunner
from ...utilities.exceptions import PgmError
from ...utilities.logging_setup import get_logger
from ...utilities.powershell_runner import PowerShellRunner
from . import msi

_log = get_logger(__name__)

_QUERY = r"""
$controllers = @(Get-CimInstance Win32_USBController -ErrorAction SilentlyContinue)
$links = @(Get-CimInstance Win32_USBControllerDevice -ErrorAction SilentlyContinue)
$ids = @($links | ForEach-Object { [string]$_.Dependent.DeviceID })
$entities = @{}
Get-CimInstance Win32_PnPEntity -ErrorAction SilentlyContinue |
  Where-Object { $ids -contains $_.PNPDeviceID } |
  ForEach-Object { $entities[[string]$_.PNPDeviceID] = $_ }
$products = @{}
foreach ($id in $ids) {
  if ($id -match '^USB\\VID_[0-9A-F]{4}&PID_[0-9A-F]{4}\\') {
    try {
      $p = Get-PnpDeviceProperty -InstanceId $id `
        -KeyName DEVPKEY_Device_BusReportedDeviceDesc -ErrorAction Stop
      if ($p.Data) { $products[$id] = [string]$p.Data }
    } catch { }
  }
}
foreach ($c in $controllers) {
  [pscustomobject]@{ Kind = 'controller'; Id = [string]$c.PNPDeviceID; Name = [string]$c.Name }
}
foreach ($l in $links) {
  $id = [string]$l.Dependent.DeviceID
  $e = $entities[$id]
  [pscustomobject]@{
    Kind = 'device'
    Id = $id
    Controller = [string]$l.Antecedent.DeviceID
    Name = if ($e) { [string]$e.Name } else { '' }
    Class = if ($e) { [string]$e.PNPClass } else { '' }
    Product = [string]$products[$id]
  }
}
"""

#: ``PNPClass`` values that are input devices, and how to name them.
INPUT_CLASSES: dict[str, str] = {"Mouse": "мышь", "Keyboard": "клавиатура"}

_VID_PID = re.compile(r"VID_([0-9A-F]{4})&PID_([0-9A-F]{4})", re.IGNORECASE)


def vid_pid(instance_id: str) -> str | None:
    """``"1D57:FA60"`` for any node of that device, HID or USB."""
    match = _VID_PID.search(instance_id)
    return f"{match.group(1)}:{match.group(2)}".upper() if match else None


@dataclass(frozen=True, slots=True)
class UsbController:
    instance_id: str
    name: str
    msi: msi.MsiState


@dataclass(frozen=True, slots=True)
class InputDevice:
    """A mouse or keyboard, with the controller it is attached to."""

    kind: str
    name: str
    product: str
    instance_id: str
    controller_id: str

    @property
    def vid_pid(self) -> str | None:
        return vid_pid(self.instance_id)

    @property
    def label(self) -> str:
        product = f" «{self.product}»" if self.product else ""
        ids = f" [{self.vid_pid}]" if self.vid_pid else ""
        return f"{self.kind}{product}{ids}"


@dataclass(frozen=True, slots=True)
class UsbInventory:
    controllers: tuple[UsbController, ...] = ()
    inputs: tuple[InputDevice, ...] = ()
    devices_per_controller: dict[str, int] = field(default_factory=dict)
    """Physical USB devices (not interfaces or HID nodes) per controller."""

    error: str = ""

    def controller(self, instance_id: str) -> UsbController | None:
        for controller in self.controllers:
            if controller.instance_id.lower() == instance_id.lower():
                return controller
        return None


def build(rows: list[dict], msi_reader=msi.read) -> UsbInventory:
    """Assemble the inventory from the query's rows. Pure, so it is testable."""
    controllers = tuple(
        UsbController(
            instance_id=str(r.get("Id") or ""),
            name=str(r.get("Name") or ""),
            msi=msi_reader(str(r.get("Id") or "")),
        )
        for r in rows
        if r.get("Kind") == "controller" and r.get("Id")
    )

    devices = [r for r in rows if r.get("Kind") == "device"]
    products: dict[str, str] = {}
    physical: dict[str, set[str]] = {}
    for row in devices:
        instance = str(row.get("Id") or "")
        key = vid_pid(instance)
        controller = str(row.get("Controller") or "")
        if key and row.get("Product"):
            products.setdefault(key, str(row["Product"]))
        # One physical device is one USB\VID_x&PID_y node without &MI_.
        if instance.upper().startswith("USB\\VID_") and "&MI_" not in instance.upper():
            physical.setdefault(controller, set()).add(instance.upper())

    inputs = []
    seen: set[tuple[str, str]] = set()
    for row in devices:
        kind = INPUT_CLASSES.get(str(row.get("Class") or ""))
        if kind is None:
            continue
        instance = str(row.get("Id") or "")
        key = vid_pid(instance) or instance
        # A gaming mouse exposes several HID collections; show it once per kind.
        if (key, kind) in seen:
            continue
        seen.add((key, kind))
        inputs.append(
            InputDevice(
                kind=kind,
                name=str(row.get("Name") or ""),
                product=products.get(vid_pid(instance) or "", ""),
                instance_id=instance,
                controller_id=str(row.get("Controller") or ""),
            )
        )

    return UsbInventory(
        controllers=controllers,
        inputs=tuple(inputs),
        devices_per_controller={k: len(v) for k, v in physical.items()},
    )


class _DevPropKey(ctypes.Structure):
    _fields_ = [("fmtid", ctypes.c_byte * 16), ("pid", ctypes.c_ulong)]


#: DEVPKEY_Device_BusReportedDeviceDesc {540b947e-8b40-45bc-a8a2-6a0b894cbda2}, 4
_BUS_REPORTED_DESC = _DevPropKey(
    (ctypes.c_byte * 16).from_buffer_copy(
        uuid.UUID("540b947e-8b40-45bc-a8a2-6a0b894cbda2").bytes_le
    ),
    4,
)


def bus_reported_name(instance_id: str) -> str:
    """The product name a device reports about itself, or ``""``.

    ``CM_Get_DevNode_PropertyW`` from the configuration manager — the source
    ``Get-PnpDeviceProperty`` reads, without starting PowerShell.
    """
    try:
        cfg = ctypes.WinDLL("cfgmgr32")
    except OSError:
        return ""
    node = ctypes.c_ulong()
    if cfg.CM_Locate_DevNodeW(ctypes.byref(node), ctypes.c_wchar_p(instance_id), 0) != 0:
        return ""
    prop_type = ctypes.c_ulong()
    size = ctypes.c_ulong(512)
    buffer = ctypes.create_string_buffer(size.value)
    result = cfg.CM_Get_DevNode_PropertyW(
        node, ctypes.byref(_BUS_REPORTED_DESC), ctypes.byref(prop_type),
        buffer, ctypes.byref(size), 0,
    )
    if result != 0:
        return ""
    return buffer.raw[: size.value].decode("utf-16-le", errors="replace").rstrip("\0")


def _reference_id(reference: str) -> str:
    """``...Win32_PnPEntity.DeviceID="USB\\\\VID_..."`` -> ``USB\\VID_...``."""
    _, _, quoted = str(reference or "").partition('DeviceID="')
    return quoted.rstrip('"').replace("\\\\", "\\")


def _rows_via_wmi() -> list[dict]:
    """The rows :data:`_QUERY` produced, read in-process (0.4 s, not 5 s)."""
    data = wmi.query({
        "controllers": ("root/cimv2", "SELECT PNPDeviceID, Name FROM Win32_USBController"),
        "links": ("root/cimv2", "SELECT Antecedent, Dependent FROM Win32_USBControllerDevice"),
        "inputs": ("root/cimv2", "SELECT PNPDeviceID, Name, PNPClass FROM Win32_PnPEntity "
                   "WHERE PNPClass='Mouse' OR PNPClass='Keyboard'"),
    })
    entities = {str(e.get("PNPDeviceID") or "").upper(): e for e in data["inputs"]}
    rows: list[dict] = [
        {"Kind": "controller", "Id": str(c.get("PNPDeviceID") or ""), "Name": str(c.get("Name") or "")}
        for c in data["controllers"]
    ]
    for link in data["links"]:
        device = _reference_id(link.get("Dependent"))
        entity = entities.get(device.upper(), {})
        is_device_node = _PHYSICAL.match(device) is not None
        rows.append({
            "Kind": "device",
            "Id": device,
            "Controller": _reference_id(link.get("Antecedent")),
            "Name": str(entity.get("Name") or ""),
            "Class": str(entity.get("PNPClass") or ""),
            "Product": bus_reported_name(device) if is_device_node else "",
        })
    return rows


#: One physical USB device: ``USB\VID_x&PID_y\serial``, no interface suffix.
_PHYSICAL = re.compile(r"^USB\\VID_[0-9A-F]{4}&PID_[0-9A-F]{4}\\", re.IGNORECASE)


def inventory(powershell: PowerShellRunner | None = None) -> UsbInventory:
    """Read the live inventory. Never raises.

    In-process through WMI and the configuration manager; PowerShell only
    when a runner is injected or COM is unavailable.
    """
    if powershell is None:
        try:
            return build(_rows_via_wmi())
        except wmi.WmiError as exc:
            _log.info("usb inventory via WMI unavailable: %s", exc.reason)
    runner = powershell or PowerShellRunner(CommandRunner())
    try:
        rows = runner.run_json(_QUERY, timeout_s=90, operation="usb inventory")
    except PgmError as exc:
        return UsbInventory(error=exc.reason or exc.what)
    return build(rows)


__all__ = [
    "INPUT_CLASSES",
    "InputDevice",
    "UsbController",
    "UsbInventory",
    "build",
    "inventory",
    "vid_pid",
]
