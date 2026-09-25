"""WMI queries in-process, through COM, instead of through PowerShell.

A PowerShell call costs about 0.8 s before it runs a single line — the
interpreter has to start — and the hardware inventory spent 2.3 s of a 3.1 s
scan in one. The same five CIM classes read here through the WMI scripting
API take about 0.2 s together, because nothing is spawned: pywin32 (already a
dependency) talks to the WMI service directly.

Rows come back as plain dicts with the property names as keys, the same
shape ``PowerShellRunner.run_json`` produced, so callers do not change.
Values are whatever COM hands over — ints, strings, ``None``; uint64 arrives
as a string and CIM datetimes as ``yyyymmddHHMMSS.ffffff±UUU``, and the
existing parsers already accept both.

Every call initialises COM on the calling thread and uninitialises it after:
the scan runs on a worker thread that owns no apartment of its own. Any COM
failure raises :class:`WmiError`, and callers fall back to PowerShell — the
point is speed, never a lost reading.
"""

from __future__ import annotations

from typing import Any

from .exceptions import PgmError


class WmiError(PgmError):
    """WMI could not be queried in-process."""


def query(queries: dict[str, tuple[str, str]]) -> dict[str, list[dict[str, Any]]]:
    """Run several WQL queries on one connection per namespace.

    Args:
        queries: ``key -> (namespace, wql)``; the result maps each key to
            its rows.

    Raises:
        WmiError: COM or WMI is unavailable, or a query failed.
    """
    try:
        import pythoncom
        import win32com.client
    except ImportError as exc:
        raise WmiError(what="WMI недоступен", reason=f"нет pywin32: {exc}") from exc

    pythoncom.CoInitialize()
    try:
        # In a separate frame so every COM reference is released when it
        # returns — before CoUninitialize, not after it.
        return _run(win32com.client, queries)
    except Exception as exc:  # noqa: BLE001 - pywintypes.com_error and friends
        raise WmiError(what="Запрос WMI не выполнен", reason=str(exc)) from exc
    finally:
        pythoncom.CoUninitialize()


def _run(client: Any, queries: dict[str, tuple[str, str]]) -> dict[str, list[dict[str, Any]]]:
    locator = client.Dispatch("WbemScripting.SWbemLocator")
    services: dict[str, Any] = {}
    results: dict[str, list[dict[str, Any]]] = {}
    for key, (namespace, wql) in queries.items():
        service = services.get(namespace)
        if service is None:
            service = services[namespace] = locator.ConnectServer(".", namespace)
        results[key] = [
            {prop.Name: prop.Value for prop in row.Properties_}
            for row in service.ExecQuery(wql)
        ]
    return results


__all__ = ["WmiError", "query"]
