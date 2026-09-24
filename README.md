# Pudge Gaming Manager

**v1.0.0-dev** — local diagnostics, optimization and configuration management
for gaming-club PCs. Runs entirely offline. No server, no account, no telemetry.

> **Status: core in development.** The foundation, hardware scanner,
> tweak/rollback engine, cleaner, OPTIMIZE pipeline, Golden Profile
> capture/compare, network diagnostics, Steam club-reset and dashboard are
> implemented and tested. Profile *restore* and the maintenance agent are not
> yet built. See [Current state](#current-state).

---

## What it does

An administrator installs PGM on a club PC, picks a profile, and presses one
button. The PC scans itself, reports what is wrong, shows exactly what it
intends to change, applies only what was approved, verifies each change took
effect, and undoes anything that failed.

## What it is not

It is not an "FPS booster". PGM does not disable Defender, does not turn off
Windows Update, and does not apply registry tweaks it cannot justify. Every
change answers six questions before it ships (rule #64):

1. What does it change?
2. Why?
3. What is the expected effect?
4. What are the risks?
5. How is it verified?
6. How is it rolled back?

A change that cannot answer all six is not included.

---

## Safety model

```
SCAN -> DIAGNOSE -> PLAN -> [preview] -> BACKUP -> APPLY -> VERIFY
                                                              |
                                                    failed ->  ROLLBACK
```

Enforced by `TweakEngine`, not by individual tweaks:

- **No change without a durable backup.** The backup row is committed to SQLite
  *before* the mutation. A tweak whose backup fails is skipped, whatever its
  risk level.
- **No unverified success.** After applying, PGM re-reads the setting. If it
  did not hold, the change is rolled back automatically.
- **Success means "changed and verified"** — never "performance improved".
  Performance is measured separately by the benchmark subsystem (rule #65).
- **Risk gating.** `SAFE` and `LOW` may auto-apply. `MEDIUM` and above require
  administrator rights *and* explicit opt-in. `CRITICAL` is never auto-applied.
- **Dry run always available**, and always shown before anything mutates. The
  operator can untick any planned change; unticked changes are skipped.
- **No stale plans.** Just before applying, each tweak re-reads the system. If
  the setting changed since the preview, the change is skipped, not forced.
- **Interrupted changes are surfaced.** A change is marked applied only after
  it verifies. If PGM dies mid-change, the next start lists what was left
  unverified, with the original value — it does not roll back unattended.

### Honest reporting

A value PGM cannot read is shown as `UNAVAILABLE` with the reason. It is never
shown as `0`, and never guessed. Thresholds carry their provenance: a
vendor-documented limit and a PGM heuristic do not look alike in the UI.

---

## Requirements

| | |
|---|---|
| OS | Windows 10 (19045+) or Windows 11 x64 |
| Python | 3.12+ (developed and tested on **3.14.7**) |
| Shell | Windows PowerShell 5.1 (present on all supported versions) |
| Privileges | Not required to scan. Required to change system settings. |

Read-only scanning deliberately works without administrator rights.

## Installation (development)

```bash
git clone <repo> pudge-gaming-manager
cd pudge-gaming-manager
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt
```

## Running the tests

```bash
.venv/Scripts/python.exe -m pytest
```

The suite includes architecture tests that fail the build if the layering is
violated or if `subprocess` is used outside its one sanctioned module.
Destructive operations are exercised only against fakes — never the live
machine.

## Building the executable

```bash
.venv/Scripts/python.exe -m pip install pyinstaller
.venv/Scripts/python.exe -m PyInstaller --noconfirm --clean \
    --name PudgeGamingManager --onefile --windowed --uac-admin run.py
```

The result is `dist/PudgeGamingManager.exe`, a single self-contained file.
`--uac-admin` embeds a manifest requesting elevation, so Windows shows a UAC
prompt at launch: the executable always runs as administrator, which the
optimize and Steam-reset actions need. `run.py` is the entry point;
`PGM_SELFTEST=1` makes it construct the dashboard off-screen and exit, which
is how a build is smoke-tested without a prompt.

---

## Current state

### Implemented and tested

| Component | Notes |
|---|---|
| Exception hierarchy | Every error carries *what / reason / remedy* (rule #36) |
| `CommandRunner` | The only module allowed to use `subprocess`; timeout, capture, OEM-codepage decoding. Bare program names resolve **only** from `System32`/`Windows`, never PATH or the working directory, so a planted `powercfg.exe` cannot run elevated |
| `PowerShellRunner` | PS 5.1, `-EncodedCommand`, env-var parameters (injection-safe), explicit JSON depth |
| `privileges` | Token-based elevation detection, per-operation gating |
| `logging_setup` | `application.log`, `audit.log` (JSON lines), `errors.log` |
| SQLite layer | 14 tables, migrations, WAL, **append-only audit log enforced by trigger**, written for every applied change, rollback and interrupted change. Refuses a data folder or database created first by another account (ProgramData lets any user create one) |
| Hardware scanner | CPU, RAM, GPU, storage, displays in a single batched CIM call (~3 s) |
| Display module | `EnumDisplaySettingsExW` mode enumeration, refresh-rate capability detection, `CDS_TEST` dry run |
| `TweakEngine` | Full lifecycle with all safety invariants |
| Tweak contract | Rationale required at class-definition time |
| `PowerManager` | Locale-independent `powercfg` parsing (GUID + `*` marker) |
| `SetPowerPlanTweak` | First real tweak — **verified apply/rollback on real hardware** |
| `SetRefreshRateTweak` | Raises a display to its maximum refresh rate at the current resolution |
| Cleanup engine | Rule-based temp-file cleanup, counted in the preview before anything is deleted; marked irreversible in the UI |
| `OptimizationPipeline` | Scan findings -> tweaks -> preview -> apply -> rescan, with a before/after report |
| `RegistryManager` / `ServiceManager` | Allowlist matched per path segment and per hive; `Services` and `Run` keys deliberately excluded. Type-preserving backups, protected-service list. Not yet used by any tweak |
| Network diagnostics | Physical uplink (cable/Wi-Fi, negotiated speed, duplex) kept apart from the path Windows actually routes through, so a VPN is reported as a VPN and never as the network card. Router and internet latency, loss and jitter via `IcmpSendEcho` — no admin rights, no parsing of translated `ping.exe` output. Runs beside the hardware scan |
| Golden Profile | Capture this PC, compare another against it, load/save as JSON. Loading is a trust boundary: size-limited, unknown fields rejected. Capture also records the installed Steam games as the club-reset keep-list |
| Steam club reset | As SteamWiper: removes every game except the profile's kept app IDs; clears `downloading`, `temp`, `shadercache`, `workshop` (whole — including kept games' mods/maps) and `sourcemods` in every library, plus `appcache`, `logs`, `dumps`, `userdata`; **signs every account out** — empties `config` (keeping `config.vdf` and `libraryfolders.vdf`) and deletes each Windows user's saved tokens (`local.vdf`) and Steam web cookies (`htmlcache`). Preview-first with sizes and the number of remembered accounts; everything deleted by handle (junction-swap safe); Steam stopped first |
| Gaming Score | Transparent, weights configurable, unavailable inputs excluded and renormalised |
| Issue detection | Findings carry severity, remedy and threshold provenance |
| Dashboard GUI | PySide6 dark theme; scan, optimize, profile and Steam-reset work run on worker threads; score explainer; **Save as profile… / Compare with profile… / Reset Steam games…**. A write in progress (apply or wipe) blocks the window from closing |

**429 tests passing**, plus one opt-in live test that changes and restores
the active power plan (`PGM_LIVE_SYSTEM_TESTS=1`).

### Running it

```bash
.venv/Scripts/python.exe -m pudge_gaming_manager
```

### Not yet built

Startup manager · Process analyzer · DNS diagnostics ·
Windows repair (SFC/DISM) · Self-healing · **Restore Golden Profile** ·
Per-game tuning profiles · Session mode · Benchmark · Maintenance agent ·
Installer

**`OPTIMIZE PC` acts on scan findings only** — today that means display
refresh rate and disk cleanup. It does not act on Golden Profile drift, and
it never changes the power plan on its own. Comparing against a profile is
read-only: rows PGM has a tweak for say so, but restoring a profile is not
built, and the comparison window does not pretend otherwise.

The profile comparison checks hardware, power plan and display. The schema
also accepts `cleanup` and `services` sections; those are validated but not
yet compared.

---

## Known limitations

These are stated rather than worked around. Each is a deliberate scope
decision, not an oversight.

**GPU telemetry is not available.** v1.0 NVIDIA support is detection-only:
model, driver version/date and VRAM capacity. No temperature, no utilization,
no per-game profiles. Writing NVIDIA control-panel settings has no public
documented API — it requires `NvAPI_DRS_*`, which is thinly documented and
needs reverse-engineered setting IDs. Deferred rather than guessed.

**`Win32_VideoController.AdapterRAM` is not used.** It is a 32-bit field and
reports a 12 GB RTX 3060 as 4 GB. VRAM comes from a one-shot `nvidia-smi`
query instead; on AMD/Intel GPUs VRAM is reported as unavailable.

**CPU temperature is not available.** Windows exposes no reliable general CPU
thermal sensor. `MSAcpi_ThermalZoneTemperature` reports an ACPI zone that is
often the motherboard and frequently wrong on desktops. Reporting it as "CPU
temperature" would be a fabrication.

**SSD temperature and raw SMART attributes are not read.** These need a
privileged raw device handle and vendor-specific decoding. Health comes from
`MSFT_PhysicalDisk.HealthStatus` instead, which is documented and reliable.

**Internet latency is measured to a reference point, not a game server.**
The reference is `1.1.1.1`. Latency to a particular game depends on where
that game's servers are; PGM does not claim otherwise.

**Latency through a VPN or tunnel is often not measurable.** Tunnel
software commonly answers ping itself. Before measuring, PGM sends one echo
to `192.0.2.1` (TEST-NET-1, RFC 5737), which is routed nowhere; if it
answers, replies on that path are fabricated, and PGM reports the latency as
not measurable instead of showing a 0 ms that never happened.

**Round-trip times have 1 ms resolution** (`IcmpSendEcho` reports whole
milliseconds), so a LAN hop reads `<1 ms`. Network diagnostics are IPv4
only. A router that ignores ping is treated as filtering ICMP, not as a
broken LAN. When traffic to the router itself is routed through a VPN, the
router's latency is reported as not measurable. A connected card with no
default gateway (a LAN-only event) is reported as "no internet route", not
as disconnected.

**HDR and VRR/G-SYNC state are not detected.** Not exposed by the Win32
display API; would require vendor libraries.

### Unverified on the target platform

The development machine is **Windows 10 Pro 19045** with no club software
installed. The following are implemented from documentation and have **not**
been executed in their target environment:

- `OPTIMIZE PC` **apply** from the GUI on a machine that needs changes. The
  preview path has run live; the dev machine had no findings to apply.
- Network findings for Wi-Fi, sub-gigabit links, half duplex, packet loss
  and a disconnected PC. The dev machine is on healthy gigabit Ethernet
  behind a VPN, so only the VPN detection and the fabricated-reply check
  have fired live; the rest are covered by unit tests.
- The Steam reset **removing** games from the GUI. Detection, enumeration
  (12 real games across two libraries), sizing and the dry-run preview have
  run live on this machine; the actual deletion is covered by tests against a
  fake Steam tree (including a junction-swap attempt), never the real install.

- Every Windows 11-specific code path.
- SmartShell protection — no SmartShell installation exists here. The
  protected-application list is config-driven and untested against the real
  product.
- Multi-GPU and AMD/Intel-only machines. Only a single NVIDIA RTX 3060 was
  available.
- A monitor running below its maximum refresh rate. Both attached displays
  already run at their maximum (180 Hz and 240 Hz), so the detection logic is
  covered by unit tests with constructed values rather than live hardware.

---

## Documentation

| File | Contents |
|---|---|
| `ARCHITECTURE.md` | Layering, change lifecycle, rollback, privilege and security model |
| `README.md` | This file |

Planned: `SECURITY.md`, `TWEAKS.md`, `PROFILE_SCHEMA.md`, `DEVELOPMENT.md`,
`CHANGELOG.md`.

---

## Privacy

PGM does not collect personal documents, file contents, passwords, cookies,
messages or game credentials. It stores no secret of any kind, which is the
cheapest way to never leak one. All data stays in
`%ProgramData%\PudgeGamingManager\`.
