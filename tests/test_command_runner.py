"""Tests for the command execution layer.

Encoding tests are Windows-only and assert against the *real* console
codepage, because that is the property that actually broke during development:
`powercfg` on a Russian-locale machine emits cp866, and decoding it as UTF-8
silently corrupts every parsed value.
"""

from __future__ import annotations

import os
import re

import pytest

from pudge_gaming_manager.utilities.command_runner import CommandRunner, CommandResult
from pudge_gaming_manager.utilities.exceptions import (
    CommandFailedError,
    CommandNotFoundError,
    CommandTimeoutError,
    PrivilegeError,
)
from pudge_gaming_manager.utilities.privileges import is_admin

windows_only = pytest.mark.skipif(os.name != "nt", reason="Windows-only behaviour")


# -- result object ---------------------------------------------------------


def test_result_ok_reflects_exit_code() -> None:
    assert CommandResult(("x",), 0, "", "", 0.1).ok
    assert not CommandResult(("x",), 1, "", "", 0.1).ok


def test_result_lines_strips_and_drops_blanks() -> None:
    result = CommandResult(("x",), 0, "  a  \n\n  b\n   \n", "", 0.0)
    assert result.lines() == ["a", "b"]


# -- argument handling -----------------------------------------------------


def test_empty_args_rejected() -> None:
    with pytest.raises(ValueError):
        CommandRunner().run([])


def test_missing_executable_raises_actionable_error() -> None:
    with pytest.raises(CommandNotFoundError) as excinfo:
        CommandRunner().run(["pgm-no-such-program-xyz"])

    error = excinfo.value
    assert "pgm-no-such-program-xyz" in error.what
    # Rule #36: the user is told what to do, not shown a traceback.
    assert error.remedy
    assert "Причина:" in error.user_message()


def test_try_run_returns_none_for_missing_program() -> None:
    """Optional probes must not interrupt a scan."""
    assert CommandRunner().try_run(["pgm-no-such-program-xyz"]) is None


# -- timeout ---------------------------------------------------------------


@windows_only
def test_timeout_raises_with_duration() -> None:
    runner = CommandRunner()
    with pytest.raises(CommandTimeoutError) as excinfo:
        # ping -n 10 to localhost takes ~9s; we allow 1s.
        runner.run(["ping", "-n", "10", "127.0.0.1"], timeout_s=1.0)
    assert excinfo.value.timeout_s == 1.0


# -- check / failure -------------------------------------------------------


@windows_only
def test_check_raises_on_nonzero_exit() -> None:
    runner = CommandRunner()
    with pytest.raises(CommandFailedError) as excinfo:
        runner.run(["cmd", "/c", "exit 3"], check=True)
    assert excinfo.value.exit_code == 3


@windows_only
def test_no_check_returns_failure_without_raising() -> None:
    result = CommandRunner().run(["cmd", "/c", "exit 3"])
    assert result.exit_code == 3
    assert not result.ok


# -- privilege gate --------------------------------------------------------


def test_requires_admin_checks_before_launching(monkeypatch: pytest.MonkeyPatch) -> None:
    """The privilege error must arrive before the process starts.

    Otherwise the user sees an opaque access-denied from inside the tool
    instead of "run as Administrator".
    """
    import pudge_gaming_manager.utilities.command_runner as mod

    launched: list[object] = []
    monkeypatch.setattr(
        mod, "require_admin", lambda op: (_ for _ in ()).throw(PrivilegeError(op))
    )
    monkeypatch.setattr(
        mod.subprocess, "run", lambda *a, **k: launched.append(a)
    )

    with pytest.raises(PrivilegeError):
        CommandRunner().run(["cmd", "/c", "echo hi"], requires_admin=True)

    assert launched == [], "process must not start when elevation is missing"


# -- encoding (the bug this layer exists to prevent) -----------------------


@windows_only
def test_oem_codepage_is_detected() -> None:
    encoding = CommandRunner().encoding
    assert re.fullmatch(r"cp\d+|utf-8", encoding), f"odd encoding {encoding!r}"


@windows_only
def test_legacy_console_output_decodes_without_replacement_chars() -> None:
    """`powercfg /list` must decode cleanly in the console codepage.

    U+FFFD in the output means the decode was wrong and any value parsed from
    it is untrustworthy.
    """
    result = CommandRunner().run(["powercfg", "/list"], timeout_s=20, check=True)
    assert result.stdout
    assert "�" not in result.stdout, "output decoded with the wrong codepage"


@windows_only
def test_power_scheme_guids_are_parseable() -> None:
    """The downstream property that actually matters to the power manager."""
    result = CommandRunner().run(["powercfg", "/list"], timeout_s=20, check=True)
    guids = re.findall(
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
        result.stdout,
    )
    assert len(guids) >= 2, "expected at least the built-in power schemes"


# -- auditing --------------------------------------------------------------


@windows_only
def test_every_command_is_audited() -> None:
    records: list[dict] = []
    runner = CommandRunner(audit=records.append)
    runner.run(["cmd", "/c", "echo hi"])

    assert len(records) == 1
    # The audit names the binary that actually ran, by absolute path.
    assert records[0]["target"].lower().endswith(r"\system32\cmd.exe")
    assert records[0]["exit_code"] == 0
    assert records[0]["timed_out"] is False


@windows_only
def test_audit_failure_never_breaks_the_command() -> None:
    """Losing an audit line must not lose the operation."""

    def broken(_record: dict) -> None:
        raise RuntimeError("audit sink is down")

    result = CommandRunner(audit=broken).run(["cmd", "/c", "echo hi"])
    assert result.ok


@windows_only
def test_timeout_is_audited() -> None:
    records: list[dict] = []
    runner = CommandRunner(audit=records.append)
    with pytest.raises(CommandTimeoutError):
        runner.run(["ping", "-n", "10", "127.0.0.1"], timeout_s=1.0)

    assert records and records[0]["timed_out"] is True


def test_admin_state_is_reported_consistently() -> None:
    """Sanity: the privilege probe returns a bool and does not raise."""
    assert isinstance(is_admin(), bool)


# -- executable resolution (planted-binary defence) ------------------------


@pytest.mark.skipif(os.name != "nt", reason="Windows search order")
def test_bare_name_never_resolves_to_the_working_directory(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PGM runs elevated; a powercfg.exe planted in the working directory
    must not be the one that runs."""
    from pudge_gaming_manager.utilities.command_runner import resolve_executable

    (tmp_path / "powercfg.exe").write_bytes(b"MZ not really")
    (tmp_path / "evil-tool.exe").write_bytes(b"MZ")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PATH", str(tmp_path))

    resolved = resolve_executable("powercfg")
    assert resolved is not None
    assert os.path.dirname(resolved).lower().endswith("system32")
    assert resolve_executable("evil-tool") is None


def test_an_explicit_path_is_used_as_given(tmp_path) -> None:
    from pudge_gaming_manager.utilities.command_runner import resolve_executable

    tool = tmp_path / "tool.exe"
    tool.write_bytes(b"MZ")
    assert resolve_executable(str(tool)) == str(tool)
    assert resolve_executable(str(tmp_path / "missing.exe")) is None
