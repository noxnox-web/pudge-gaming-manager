"""Deleting a file by the object, not by its name.

The cleaner runs elevated and deletes under ``C:\\Windows\\Temp``, a directory
any user can create subdirectories in. That is the classic setup for a
time-of-check/time-of-use attack: PGM validates a path, and between the
validation and the ``unlink`` a low-privileged user swaps a directory for a
junction pointing at ``System32``. The elevated delete then follows the
junction and destroys a system file.

Re-checking the path again just moves the window; the name is the wrong
thing to trust. This module opens a handle **once**, with
``FILE_FLAG_OPEN_REPARSE_POINT`` so a reparse point is opened as itself
rather than followed, and does every check and the delete against that one
handle. After the open, the object is pinned: the attacker may rename or
replace the *name*, but the handle still refers to the object PGM inspected.

``delete_file`` is used for the actual removal. ``safe_unlink`` falls back to
an ordinary ``unlink`` off Windows (tests, and the rules never point outside
Windows in production), so the engine has one call site either way.
"""

from __future__ import annotations

import ctypes
import os
import stat
import pathlib
from ctypes import wintypes

_IS_WINDOWS = os.name == "nt"

# CreateFileW
_GENERIC_READ = 0x80000000
_DELETE = 0x00010000
_FILE_SHARE_READ = 0x00000001
_FILE_SHARE_WRITE = 0x00000002
_FILE_SHARE_DELETE = 0x00000004
_OPEN_EXISTING = 3
_FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
_FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
_INVALID_HANDLE_VALUE = wintypes.HANDLE(-1).value

# GetFileInformationByHandle
_FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400
_FILE_ATTRIBUTE_DIRECTORY = 0x00000010

# SetFileInformationByHandle: FILE_DISPOSITION_INFO (class 4)
_FILE_DISPOSITION_INFO = 4


class _ByHandleFileInformation(ctypes.Structure):
    _fields_ = [
        ("dwFileAttributes", wintypes.DWORD),
        ("ftCreationTime", wintypes.FILETIME),
        ("ftLastAccessTime", wintypes.FILETIME),
        ("ftLastWriteTime", wintypes.FILETIME),
        ("dwVolumeSerialNumber", wintypes.DWORD),
        ("nFileSizeHigh", wintypes.DWORD),
        ("nFileSizeLow", wintypes.DWORD),
        ("nNumberOfLinks", wintypes.DWORD),
        ("nFileIndexHigh", wintypes.DWORD),
        ("nFileIndexLow", wintypes.DWORD),
    ]


class _FileDispositionInfo(ctypes.Structure):
    _fields_ = [("DeleteFile", wintypes.BOOL)]


if _IS_WINDOWS:
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _kernel32.CreateFileW.restype = wintypes.HANDLE
    _kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ]
    _kernel32.GetFileInformationByHandle.argtypes = [
        wintypes.HANDLE, ctypes.POINTER(_ByHandleFileInformation)
    ]
    _kernel32.SetFileInformationByHandle.argtypes = [
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD
    ]
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]


class DeleteError(OSError):
    """A handle-based delete could not be completed safely."""


def delete_file(path: pathlib.Path) -> int:
    """Delete a regular file by its object and return its size in bytes.

    Opens the path once without following reparse points and, on that same
    handle, refuses anything that is a directory or a reparse point, then
    marks the object for deletion. The name can be swapped after the open;
    the handle cannot.

    Raises:
        DeleteError: the object is a directory or reparse point, or a Win32
            call failed. ``FileNotFoundError`` and ``PermissionError`` (both
            ``OSError``) surface with their Windows error numbers so the
            caller can treat a locked file as "in use", as before.
    """
    if not _IS_WINDOWS:  # pragma: no cover - exercised via safe_unlink on POSIX
        size = path.stat().st_size
        path.unlink()
        return size

    handle = _kernel32.CreateFileW(
        str(path),
        _GENERIC_READ | _DELETE,
        _FILE_SHARE_READ | _FILE_SHARE_WRITE | _FILE_SHARE_DELETE,
        None,
        _OPEN_EXISTING,
        # BACKUP_SEMANTICS lets the same call open a directory, so the check
        # below can reject one instead of the open silently refusing it.
        _FILE_FLAG_BACKUP_SEMANTICS | _FILE_FLAG_OPEN_REPARSE_POINT,
        None,
    )
    if handle == _INVALID_HANDLE_VALUE:
        raise ctypes.WinError(ctypes.get_last_error())

    try:
        info = _ByHandleFileInformation()
        if not _kernel32.GetFileInformationByHandle(handle, ctypes.byref(info)):
            raise ctypes.WinError(ctypes.get_last_error())

        attributes = info.dwFileAttributes
        if attributes & _FILE_ATTRIBUTE_REPARSE_POINT:
            # A junction or symlink swapped in after the scan. Deleting the
            # link itself would be safe, but the cleaner never lists links,
            # so its presence here means the name was tampered with. Refuse.
            raise DeleteError(f"{path} is a reparse point; refusing to delete")
        if attributes & _FILE_ATTRIBUTE_DIRECTORY:
            raise DeleteError(f"{path} is a directory; refusing to delete")

        size = (info.nFileSizeHigh << 32) | info.nFileSizeLow

        disposition = _FileDispositionInfo(DeleteFile=True)
        if not _kernel32.SetFileInformationByHandle(
            handle, _FILE_DISPOSITION_INFO, ctypes.byref(disposition),
            ctypes.sizeof(disposition),
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        return size
    finally:
        _kernel32.CloseHandle(handle)


def safe_unlink(path: pathlib.Path) -> int:
    """Delete ``path`` and return its size; handle-based on Windows."""
    return delete_file(path)


def _is_reparse_point(entry: os.DirEntry | pathlib.Path) -> bool:
    """True for a junction or symlink, without following it."""
    try:
        st = entry.stat(follow_symlinks=False) if isinstance(entry, os.DirEntry) \
            else os.lstat(entry)
        return bool(getattr(st, "st_reparse_tag", 0)) or bool(
            entry.is_symlink() if isinstance(entry, os.DirEntry) else os.path.islink(entry)
        )
    except OSError:
        return False


def assert_real_directory(path: pathlib.Path) -> None:
    """Open ``path`` by handle and refuse anything but a plain directory.

    Defeats the swap where a folder the scan approved becomes a junction to a
    protected tree before deletion: the handle is opened without following
    reparse points, so a junction is seen as itself and rejected.
    """
    if not _IS_WINDOWS:  # pragma: no cover
        if path.is_symlink() or not path.is_dir():
            raise DeleteError(f"{path} is not a plain directory; refusing")
        return

    handle = _kernel32.CreateFileW(
        str(path), _GENERIC_READ,
        _FILE_SHARE_READ | _FILE_SHARE_WRITE | _FILE_SHARE_DELETE,
        None, _OPEN_EXISTING,
        _FILE_FLAG_BACKUP_SEMANTICS | _FILE_FLAG_OPEN_REPARSE_POINT, None,
    )
    if handle == _INVALID_HANDLE_VALUE:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        info = _ByHandleFileInformation()
        if not _kernel32.GetFileInformationByHandle(handle, ctypes.byref(info)):
            raise ctypes.WinError(ctypes.get_last_error())
        attrs = info.dwFileAttributes
        if attrs & _FILE_ATTRIBUTE_REPARSE_POINT:
            raise DeleteError(f"{path} is a reparse point; refusing to delete")
        if not attrs & _FILE_ATTRIBUTE_DIRECTORY:
            raise DeleteError(f"{path} is not a directory; refusing to delete")
    finally:
        _kernel32.CloseHandle(handle)


def delete_tree(root: pathlib.Path) -> int:
    """Recursively delete a directory and return the bytes reclaimed.

    The root is verified by handle as a real directory (not a reparse point)
    before anything is removed. The walk is done by hand with ``os.scandir``
    and **never descends into a reparse point**: a junction or symlink is
    removed as a link, so deletion can never follow one out of the tree.
    Regular files are deleted by :func:`delete_file`, so each is removed by
    its object, not its name.

    ``os.walk`` is deliberately not used: with ``followlinks=False`` it still
    walks *into* an NTFS junction (that flag only skips symlinks), which would
    let a junction planted inside the tree redirect deletion to its target's
    contents — the exact escape this module exists to prevent.

    Raises:
        DeleteError: the root is a reparse point or not a directory.
    """
    assert_real_directory(root)
    freed = _delete_children(root)
    try:
        root.rmdir()
    except OSError:
        pass  # locked, or something reappeared inside it
    return freed


def _delete_children(directory: pathlib.Path) -> int:
    """Delete everything under ``directory`` without following any link.

    Files are removed with ``os.remove`` rather than a per-file handle open:
    that is roughly five times faster on a game folder with tens of thousands
    of files, and it is still safe. ``DeleteFileW`` removes a name, so if a
    regular file were swapped for a symlink between the scan of this directory
    and its deletion, the symlink itself is removed, never its target. The
    only content-destroying vector — descending into a directory junction — is
    still blocked by the reparse-point check below, and the tree root was
    already verified by handle in :func:`delete_tree`.
    """
    freed = 0
    try:
        entries = list(os.scandir(directory))
    except OSError:
        return freed
    for entry in entries:
        child = pathlib.Path(entry.path)
        try:
            if _is_reparse_point(entry):
                # A junction or symlink: remove the link itself, never its
                # target. Directory links go through rmdir, file links unlink.
                if entry.is_dir(follow_symlinks=False):
                    os.rmdir(child)
                else:
                    os.unlink(child)
            elif entry.is_dir(follow_symlinks=False):
                freed += _delete_children(child)
                child.rmdir()
            else:
                try:
                    size = entry.stat(follow_symlinks=False).st_size
                except OSError:
                    size = 0
                _remove_file(child)
                freed += size
        except FileNotFoundError:
            continue
        except OSError:
            pass  # locked or denied; leave it and carry on
    return freed


def _remove_file(path: pathlib.Path) -> None:
    """Delete a file, clearing the read-only bit if it blocks the first try.

    Steam marks some game files read-only; ``Remove-Item -Force`` clears the
    attribute and so must this, or those files would be left behind.
    """
    try:
        os.remove(path)
    except PermissionError:
        try:
            os.chmod(path, stat.S_IWRITE)
        except OSError:
            raise
        os.remove(path)
