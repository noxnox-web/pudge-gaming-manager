"""Who owns a file or folder, and whether PGM may trust it.

``C:\\ProgramData`` lets any user create a subfolder, and whoever creates a
folder owns it. A player who creates ``PudgeGamingManager`` before PGM first
runs would therefore own the folder holding the backups (the values rollback
restores) and the audit trail — and could rewrite both.

The defence is to check the owner before trusting the location. A path is
trusted when it is owned by SYSTEM, the Administrators group, TrustedInstaller,
or the account PGM is running as right now. Anything else was planted by
another user.
"""

from __future__ import annotations

import ctypes
import os
import pathlib
from ctypes import wintypes

#: SIDs that only the system or an administrator can act as.
TRUSTED_OWNER_SIDS = frozenset(
    {
        "S-1-5-18",  # LocalSystem
        "S-1-5-32-544",  # BUILTIN\\Administrators
        # NT SERVICE\\TrustedInstaller
        "S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464",
    }
)

_SE_FILE_OBJECT = 1
_OWNER_SECURITY_INFORMATION = 0x00000001
_TOKEN_QUERY = 0x0008
_TOKEN_USER = 1


def _sid_to_string(sid: ctypes.c_void_p) -> str | None:
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi32.ConvertSidToStringSidW.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)
    ]
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    text = wintypes.LPWSTR()
    if not advapi32.ConvertSidToStringSidW(sid, ctypes.byref(text)):
        return None
    try:
        return text.value
    finally:
        kernel32.LocalFree(ctypes.cast(text, ctypes.c_void_p))


def owner_sid(path: pathlib.Path | str) -> str | None:
    """The owner's SID as a string, or ``None`` if it cannot be read."""
    if os.name != "nt":
        return None
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi32.GetNamedSecurityInfoW.argtypes = [
        wintypes.LPCWSTR, ctypes.c_int, wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
    ]
    advapi32.GetNamedSecurityInfoW.restype = wintypes.DWORD
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]

    owner = ctypes.c_void_p()
    descriptor = ctypes.c_void_p()
    status = advapi32.GetNamedSecurityInfoW(
        str(path), _SE_FILE_OBJECT, _OWNER_SECURITY_INFORMATION,
        ctypes.byref(owner), None, None, None, ctypes.byref(descriptor),
    )
    if status != 0:
        return None
    try:
        return _sid_to_string(owner)
    finally:
        kernel32.LocalFree(descriptor)


def current_user_sid() -> str | None:
    """The SID of the account this process runs as."""
    if os.name != "nt":
        return None
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    advapi32.OpenProcessToken.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)
    ]
    advapi32.GetTokenInformation.argtypes = [
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]

    token = wintypes.HANDLE()
    if not advapi32.OpenProcessToken(
        kernel32.GetCurrentProcess(), _TOKEN_QUERY, ctypes.byref(token)
    ):
        return None
    try:
        needed = wintypes.DWORD()
        advapi32.GetTokenInformation(token, _TOKEN_USER, None, 0, ctypes.byref(needed))
        buffer = ctypes.create_string_buffer(needed.value)
        if not advapi32.GetTokenInformation(
            token, _TOKEN_USER, buffer, needed, ctypes.byref(needed)
        ):
            return None
        # TOKEN_USER starts with a SID_AND_ATTRIBUTES whose first field is PSID.
        sid = ctypes.c_void_p.from_buffer(buffer).value
        return _sid_to_string(ctypes.c_void_p(sid))
    finally:
        kernel32.CloseHandle(token)


def untrusted_owner(path: pathlib.Path | str) -> str | None:
    """The SID of an untrusted owner of ``path``, or ``None`` if it is fine.

    Returns ``None`` for a path that does not exist, off Windows, or when
    ownership cannot be read — absence of data is not evidence (rule #64),
    and refusing to start on an unreadable ACL would lock out a legitimate
    install.
    """
    if not os.path.exists(path):
        return None
    owner = owner_sid(path)
    if owner is None or owner in TRUSTED_OWNER_SIDS:
        return None
    if owner == current_user_sid():
        return None
    return owner


__all__ = [
    "TRUSTED_OWNER_SIDS",
    "current_user_sid",
    "owner_sid",
    "untrusted_owner",
]
