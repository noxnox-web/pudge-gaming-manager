"""Tests for the PowerShell 5.1 layer.

The injection tests use payloads whose *execution* would produce output that
differs from their *literal text* — e.g. ``$(9*9)`` yields ``81`` if evaluated
and stays ``$(9*9)`` if treated as data. A payload containing a marker word is
useless as a test, because the marker appears in the output either way.
"""

from __future__ import annotations

import os

import pytest

from pudge_gaming_manager.utilities.command_runner import CommandRunner
from pudge_gaming_manager.utilities.powershell_runner import PowerShellRunner

windows_only = pytest.mark.skipif(os.name != "nt", reason="Windows-only behaviour")
pytestmark = windows_only


@pytest.fixture(scope="module")
def ps() -> PowerShellRunner:
    return PowerShellRunner(runner=CommandRunner())


# -- basic execution -------------------------------------------------------


def test_locates_windows_powershell(ps: PowerShellRunner) -> None:
    assert ps.executable and ps.executable.lower().endswith("powershell.exe")


def test_runs_a_script_and_captures_output(ps: PowerShellRunner) -> None:
    result = ps.run("Write-Output 'hello'")
    assert result.ok
    assert result.stdout.strip() == "hello"


def test_utf8_round_trips_through_the_preamble(ps: PowerShellRunner) -> None:
    """Non-ASCII must survive; club PCs run localised Windows."""
    result = ps.run("Write-Output 'Привет — ok'")
    assert "Привет" in result.stdout
    assert "\ufffd" not in result.stdout


def test_errors_become_nonzero_exit(ps: PowerShellRunner) -> None:
    """ErrorActionPreference=Stop turns a silent failure into a real one."""
    from pudge_gaming_manager.utilities.exceptions import CommandFailedError

    with pytest.raises(CommandFailedError):
        ps.run("Get-Item 'Z:\\definitely\\missing\\path\\xyz'")


# -- JSON shape normalisation ---------------------------------------------


def test_single_object_becomes_a_one_item_list(ps: PowerShellRunner) -> None:
    """PS 5.1 emits a bare object for one result; we always want a list."""
    rows = ps.run_json("Get-CimInstance Win32_Processor | Select-Object Name")
    assert isinstance(rows, list)
    assert len(rows) == 1
    assert rows[0]["Name"]


def test_multiple_objects_become_a_list(ps: PowerShellRunner) -> None:
    rows = ps.run_json("Get-CimInstance Win32_LogicalDisk | Select-Object DeviceID")
    assert len(rows) >= 1
    assert all("DeviceID" in r for r in rows)


def test_no_objects_becomes_an_empty_list(ps: PowerShellRunner) -> None:
    rows = ps.run_json(
        "Get-Service -Name 'PgmNoSuchServiceXyz' "
        "-ErrorAction SilentlyContinue | Select-Object Name"
    )
    assert rows == []


def test_nested_objects_survive_the_depth_setting(ps: PowerShellRunner) -> None:
    """PS 5.1 defaults ConvertTo-Json to depth 2 and truncates silently."""
    rows = ps.run_json(
        "[pscustomobject]@{ a = [pscustomobject]@{ "
        "b = [pscustomobject]@{ c = [pscustomobject]@{ d = 'deep' } } } }",
        depth=8,
    )
    assert rows[0]["a"]["b"]["c"]["d"] == "deep"


# -- parameter safety ------------------------------------------------------


def test_parameter_value_is_data_not_code(ps: PowerShellRunner) -> None:
    """A parameter must never be evaluated as PowerShell."""
    result = ps.run(
        "Write-Output $Payload", parameters={"Payload": "$(9*9)"}
    )
    out = result.stdout.strip()
    assert out == "$(9*9)", f"payload was evaluated: {out!r}"
    assert "81" not in out


def test_parameter_cannot_terminate_the_statement(ps: PowerShellRunner) -> None:
    """Quote-and-semicolon breakout must not start a new statement."""
    payload = "'; $x = 7*6; Write-Output $x; '"
    result = ps.run("Write-Output $Payload", parameters={"Payload": payload})
    out = result.stdout.strip()
    assert out == payload, f"statement was broken: {out!r}"
    assert "42" not in out


def test_parameter_with_newlines_stays_one_value(ps: PowerShellRunner) -> None:
    payload = "line1\nWrite-Output 'INJECTED-EXEC'\nline3"
    result = ps.run("Write-Output $Payload", parameters={"Payload": payload})
    # The text appears (it is the value) but only as a single echoed value,
    # never as an executed statement producing a bare marker line.
    assert "INJECTED-EXEC" in result.stdout
    assert "Write-Output 'INJECTED-EXEC'" in result.stdout


def test_invalid_parameter_name_is_rejected(ps: PowerShellRunner) -> None:
    """Names reach the script text, so they are validated as identifiers."""
    with pytest.raises(ValueError, match="Invalid PowerShell parameter name"):
        ps.run("Write-Output 'x'", parameters={"bad name; rm -rf": "1"})


def test_booleans_render_predictably(ps: PowerShellRunner) -> None:
    result = ps.run(
        "if ($Flag -eq 'True') { Write-Output 'yes' } else { Write-Output 'no' }",
        parameters={"Flag": True},
    )
    assert result.stdout.strip() == "yes"


# -- optional probes -------------------------------------------------------


def test_try_run_json_swallows_failure(ps: PowerShellRunner) -> None:
    assert ps.try_run_json("Get-Item 'Z:\\missing\\xyz'") == []
