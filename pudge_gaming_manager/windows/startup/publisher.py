"""Who made the program an autostart entry launches, and whether it is ours to touch.

The Startup tab of Task Manager shows a publisher next to every entry,
because a name like ``RtkAudUService`` means nothing until it says
"Realtek". That column comes from the executable's version resource
(``CompanyName``), which is what this module reads.

Getting from an entry to its executable
---------------------------------------
* A ``Run`` value is a command line: a quoted path, or an unquoted one that
  runs to the first ``.exe``, with arguments after it.
* ``rundll32.exe x.dll,Entry`` is attributed to the DLL, not to rundll32 —
  otherwise every such entry would read "Microsoft".
* A Startup-folder item is a shortcut; its target comes from the shell.

Protected entries
-----------------
Some entries must not be switched off from a club tool, whatever an
operator clicks: the Windows Security tray icon (without it, a disabled
antivirus raises no visible warning) and the club's own management client,
which is how the club bills and controls the PC. The fragments match the
cleaner's protected list for the same software.
"""

from __future__ import annotations

import os
import re

from ...utilities.logging_setup import get_logger

_log = get_logger(__name__)

_UNQUOTED = re.compile(r"^(.+?\.(?:exe|com|bat|cmd|lnk))(?=\s|$)", re.IGNORECASE)

#: (fragment in name or command, why it may not be disabled)
PROTECTED: tuple[tuple[str, str], ...] = (
    (
        "securityhealth",
        "значок «Безопасность Windows»: без него выключенная защита не видна",
    ),
    ("smartshell", "клиент управления клубом"),
    ("senet", "клиент управления клубом"),
    ("langame", "клиент управления клубом"),
    ("gizmo", "клиент управления клубом"),
)


def protected_reason(name: str, command: str) -> str:
    """Why an entry may not be disabled, or ``""``."""
    haystack = f"{name} {command}".lower()
    for fragment, reason in PROTECTED:
        if fragment in haystack:
            return reason
    return ""


def executable(command: str) -> str | None:
    """The file a command line runs, with ``rundll32`` resolved to its DLL."""
    text = os.path.expandvars(command.strip())
    if not text:
        return None
    if text.startswith('"'):
        end = text.find('"', 1)
        path = text[1:end] if end > 0 else text[1:]
        rest = text[end + 1:] if end > 0 else ""
    else:
        match = _UNQUOTED.match(text)
        path = match.group(1) if match else text.split()[0]
        rest = text[len(path):]
    if os.path.basename(path).lower() == "rundll32.exe":
        target = rest.strip().strip('"').split(",")[0].strip()
        return target or path
    return path


def _shortcut_target(path: str) -> str | None:
    try:
        import pythoncom
        import win32com.client
    except ImportError:
        return None
    pythoncom.CoInitialize()
    try:
        shell = win32com.client.Dispatch("WScript.Shell")
        return str(shell.CreateShortcut(path).TargetPath) or None
    except Exception as exc:  # noqa: BLE001 - a broken shortcut has no publisher
        _log.debug("cannot resolve %s: %s", path, exc)
        return None
    finally:
        pythoncom.CoUninitialize()


def company(path: str | None) -> str:
    """``CompanyName`` from the file's version resource, or ``""``."""
    if not path:
        return ""
    if path.lower().endswith(".lnk"):
        path = _shortcut_target(path) or ""
        if not path:
            return ""
    try:
        import win32api
    except ImportError:
        return ""
    try:
        translations = win32api.GetFileVersionInfo(path, "\\VarFileInfo\\Translation")
        if not translations:
            return ""
        language, codepage = translations[0]
        key = f"\\StringFileInfo\\{language:04x}{codepage:04x}\\CompanyName"
        return str(win32api.GetFileVersionInfo(path, key) or "").strip()
    except Exception:  # noqa: BLE001 - no version resource is common, not an error
        return ""


def publisher(command: str) -> str:
    return company(executable(command))


__all__ = ["PROTECTED", "company", "executable", "protected_reason", "publisher"]
