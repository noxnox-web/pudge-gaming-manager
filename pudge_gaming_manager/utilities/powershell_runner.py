"""PowerShell 5.1 execution layer.

The development and target machines have **Windows PowerShell 5.1** only, so
everything here must be 5.1-compatible: no ``??``, no ``?.``, no ternary, no
``ConvertFrom-Json -AsHashtable``.

Three deliberate design choices
-------------------------------
1. **Scripts are passed via ``-EncodedCommand``** (base64 UTF-16LE), the
   documented mechanism. There is no shell quoting to get wrong, and no amount
   of odd characters in a script can break out of it.

2. **Parameters are passed through environment variables, never interpolated
   into the script text.** String-building a script from values is how command
   injection happens; a profile from a USB stick must never be able to reach
   a PowerShell prompt.

3. **``ConvertTo-Json`` always gets an explicit ``-Depth``.** PS 5.1 defaults
   to depth 2 and silently replaces deeper structures with type names rather
   than failing — a data-loss bug that looks like working code.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import shutil
from dataclasses import dataclass
from typing import Any, Mapping

from .command_runner import CommandResult, CommandRunner
from .exceptions import CommandFailedError, CommandNotFoundError

_log = logging.getLogger(__name__)

DEFAULT_JSON_DEPTH = 6
_PARAM_PREFIX = "PGM_P_"

#: Emitted before every script. Forces UTF-8 output so the runner can decode
#: deterministically instead of guessing at the console codepage, and makes
#: non-terminating errors terminate so failures surface as a non-zero exit
#: rather than as empty output that parses as "nothing found".
_PREAMBLE = (
    "$ErrorActionPreference = 'Stop';"
    "$ProgressPreference = 'SilentlyContinue';"
    "[Console]::OutputEncoding = [Text.Encoding]::UTF8;"
    "$OutputEncoding = [Text.Encoding]::UTF8;"
)


def _find_powershell() -> str:
    """Locate Windows PowerShell 5.1.

    Resolved by absolute path first: ``powershell.exe`` on PATH can be
    shadowed, and this process runs privileged operations.
    """
    system_root = os.environ.get("SystemRoot", r"C:\Windows")
    candidate = os.path.join(
        system_root, "System32", "WindowsPowerShell", "v1.0", "powershell.exe"
    )
    if os.path.isfile(candidate):
        return candidate
    found = shutil.which("powershell.exe")
    if found:
        return found
    raise CommandNotFoundError(
        "powershell.exe", context={"searched": candidate}
    )


@dataclass(slots=True)
class PowerShellRunner:
    """Runs PowerShell 5.1 scripts safely and parses their output."""

    runner: CommandRunner
    executable: str | None = None
    default_timeout_s: float = 90.0

    def __post_init__(self) -> None:
        if self.executable is None:
            self.executable = _find_powershell()

    # -- execution ---------------------------------------------------------

    def run(
        self,
        script: str,
        *,
        parameters: Mapping[str, str | int | float | bool] | None = None,
        timeout_s: float | None = None,
        check: bool = True,
        requires_admin: bool = False,
        operation: str | None = None,
        remedy: str | None = None,
    ) -> CommandResult:
        """Execute a PowerShell script.

        Args:
            script: Script body. Parameters declared in ``parameters`` are
                available as ordinary PowerShell variables, e.g. ``$Name``.
            parameters: Values bound into the script. Passed via the
                environment, so no value can alter the script's structure.
            check: Raise on a non-zero exit code.
            requires_admin: Verify elevation before launching.

        Raises:
            PrivilegeError, CommandTimeoutError, CommandFailedError
        """
        params = dict(parameters or {})
        full_script = _PREAMBLE + self._binding_preamble(params) + "\n" + script

        encoded = base64.b64encode(full_script.encode("utf-16-le")).decode("ascii")

        env = dict(os.environ)
        for name, value in params.items():
            env[_PARAM_PREFIX + name] = _to_env_value(value)

        return self.runner.run(
            [
                str(self.executable),
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-EncodedCommand",
                encoded,
            ],
            timeout_s=self.default_timeout_s if timeout_s is None else timeout_s,
            check=check,
            requires_admin=requires_admin,
            operation=operation,
            env=env,
            remedy=remedy,
            # The preamble sets Console.OutputEncoding to UTF-8, so this
            # output must be decoded as UTF-8 rather than the OEM codepage
            # used by legacy console tools.
            encoding="utf-8",
        )

    def run_json(
        self,
        script: str,
        *,
        parameters: Mapping[str, str | int | float | bool] | None = None,
        depth: int = DEFAULT_JSON_DEPTH,
        timeout_s: float | None = None,
        requires_admin: bool = False,
        operation: str | None = None,
    ) -> list[dict[str, Any]]:
        """Execute a script and parse its output as a list of objects.

        The script's final expression is piped to ``ConvertTo-Json`` with an
        explicit depth. ``@(...)`` forces array semantics so a single result
        and multiple results parse identically — PS 5.1 otherwise emits a bare
        object for one item and an array for several, a classic source of
        "works with two adapters, crashes with one".

        Returns:
            A list of dictionaries. Empty when the script produced no output.
        """
        wrapped = (
            "$__pgm_result = @(\n"
            f"{script}\n"
            ")\n"
            f"if ($__pgm_result.Count -eq 0) {{ '[]' }} "
            f"else {{ ConvertTo-Json -InputObject $__pgm_result "
            f"-Depth {int(depth)} -Compress }}"
        )

        result = self.run(
            wrapped,
            parameters=parameters,
            timeout_s=timeout_s,
            check=True,
            requires_admin=requires_admin,
            operation=operation,
        )

        text = result.stdout.strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            raise CommandFailedError(
                result.display_command,
                exit_code=result.exit_code,
                stderr=f"Output was not valid JSON: {exc}",
                remedy=None,
                context={"stdout": text[:2000]},
            ) from exc

        if parsed is None:
            return []
        if isinstance(parsed, dict):
            return [parsed]
        if isinstance(parsed, list):
            return [item for item in parsed if isinstance(item, dict)]
        return []

    def try_run_json(
        self,
        script: str,
        *,
        parameters: Mapping[str, str | int | float | bool] | None = None,
        depth: int = DEFAULT_JSON_DEPTH,
        timeout_s: float | None = None,
    ) -> list[dict[str, Any]]:
        """Like :meth:`run_json` but returns ``[]`` instead of raising.

        For optional probes during a scan, where an unavailable data source is
        an expected outcome rather than a failure.
        """
        try:
            return self.run_json(
                script, parameters=parameters, depth=depth, timeout_s=timeout_s
            )
        except (CommandFailedError, CommandNotFoundError) as exc:
            _log.debug("optional PowerShell probe failed: %s", exc.what)
            return []

    # -- internals ---------------------------------------------------------

    @staticmethod
    def _binding_preamble(params: Mapping[str, Any]) -> str:
        """Build the lines that lift environment values into PS variables.

        Only the *names* are emitted into the script, and they are validated
        as identifiers. The values never appear in the script text.
        """
        lines: list[str] = []
        for name in params:
            if not name.isidentifier():
                raise ValueError(
                    f"Invalid PowerShell parameter name {name!r}: "
                    "must be a valid identifier"
                )
            lines.append(f"${name} = $env:{_PARAM_PREFIX}{name};")
        return "".join(lines)


def _to_env_value(value: str | int | float | bool) -> str:
    """Render a parameter for the environment.

    Booleans become ``'True'``/``'False'`` so that PowerShell's
    ``[bool]::Parse`` and ``-eq 'True'`` both behave predictably.
    """
    if isinstance(value, bool):
        return "True" if value else "False"
    return str(value)
