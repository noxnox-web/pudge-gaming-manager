"""A block of host entries PGM owns, inside a file it does not.

The hosts file belongs to whoever is editing it, and that is rarely one
program: ad blockers, antivirus cleanup tools, corporate agents and people
with Notepad all write here. So this module never parses, rewrites or
reformats the file. It finds its own delimited block, replaces exactly that,
and leaves every other byte where it was::

    # === Pudge Cleaner: начало ===
    0.0.0.0 dota2.com
    0.0.0.0 www.dota2.com
    # === Pudge Cleaner: конец ===

Removing the block removes only those lines. An entry somebody else added
for the same host is theirs and stays, which also means removing our block
does not guarantee the host resolves again — and the verification says so
rather than assuming.

Bytes, not text
---------------
The file is read and written with ``surrogateescape``, so whatever encoding
someone else's comment line is in survives the round trip. Decoding it as
UTF-8 and writing it back as UTF-8 would silently mangle a Cyrillic comment
written in ANSI. Line endings are CRLF, which is what Windows ships and what
every other editor of this file uses.

No wildcards
------------
A hosts entry matches one name exactly. ``dota2.com`` does not cover
``www.dota2.com``; both have to be listed, and forgetting the second is why
a blocklist copied from a forum post often does nothing.
"""

from __future__ import annotations

import os
import pathlib

from ..utilities.command_runner import CommandRunner
from ..utilities.exceptions import PgmError
from ..utilities.logging_setup import audit_event, get_logger

_log = get_logger(__name__)

#: Where Windows keeps it. The path has not moved since NT.
HOSTS_PATH = "%SystemRoot%\\System32\\drivers\\etc\\hosts"

BEGIN_MARKER = "# === Pudge Cleaner: начало ==="
END_MARKER = "# === Pudge Cleaner: конец ==="

#: Not 127.0.0.1: a connection to localhost waits for a refusal, while
#: 0.0.0.0 is unroutable and fails at once.
BLACKHOLE = "0.0.0.0"

_NEWLINE = "\r\n"


def path() -> pathlib.Path:
    return pathlib.Path(os.path.expandvars(HOSTS_PATH))


def _read() -> str | None:
    try:
        with open(path(), "r", encoding="utf-8", errors="surrogateescape",
                  newline="") as handle:
            return handle.read()
    except OSError as exc:
        _log.warning("cannot read hosts file: %s", exc)
        return None


def _write(text: str) -> bool:
    try:
        with open(path(), "w", encoding="utf-8", errors="surrogateescape",
                  newline="") as handle:
            handle.write(text)
        return True
    except OSError as exc:
        _log.warning("cannot write hosts file: %s", exc)
        return False


def _split(text: str) -> tuple[str, list[str], str]:
    """Return ``(before, block_lines, after)`` around our markers.

    A file without the markers yields the whole text as ``before`` and an
    empty block, so adding and replacing are the same operation.
    """
    start = text.find(BEGIN_MARKER)
    if start == -1:
        return text, [], ""
    end = text.find(END_MARKER, start)
    if end == -1:
        # A begin without an end: somebody edited inside our block. Treat
        # everything from the marker as ours rather than guessing where it
        # was meant to stop — we wrote the marker, so the tail is ours.
        return text[:start], text[start:].splitlines(), ""
    after_end = end + len(END_MARKER)
    body = text[start + len(BEGIN_MARKER):end]
    return text[:start], body.splitlines(), text[after_end:]


def blocked_hosts() -> list[str] | None:
    """Host names currently in *our* block, or ``None`` if unreadable."""
    text = _read()
    if text is None:
        return None
    _before, lines, _after = _split(text)
    found: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = stripped.split()
        if len(parts) >= 2:
            found.extend(parts[1:])
    return found


def other_entry_exists(host: str) -> bool:
    """True when something outside our block already maps ``host``.

    Checked before claiming a removal restored the name: an entry another
    tool added is not ours to delete, and saying "unblocked" while it is
    still blocked would be wrong.
    """
    text = _read()
    if text is None:
        return False
    before, _ours, after = _split(text)
    wanted = host.lower()
    for line in (before + after).splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = stripped.lower().split()
        if len(parts) >= 2 and wanted in parts[1:]:
            return True
    return False


def set_block(hosts: list[str]) -> bool:
    """Replace our block with one entry per name. An empty list removes it."""
    text = _read()
    if text is None:
        return False

    before, _ours, after = _split(text)
    # Leave exactly one blank line's worth of separation, without piling up
    # newlines every time the block is rewritten.
    before = before.rstrip("\r\n")
    after = after.lstrip("\r\n")

    if not hosts:
        rebuilt = before + (_NEWLINE if before else "")
        if after:
            rebuilt += after if after.endswith(_NEWLINE) else after + _NEWLINE
    else:
        lines = [BEGIN_MARKER] + [f"{BLACKHOLE} {name}" for name in hosts]
        lines.append(END_MARKER)
        block = _NEWLINE.join(lines)
        rebuilt = (
            (before + _NEWLINE + _NEWLINE if before else "")
            + block
            + _NEWLINE
            + (after + _NEWLINE if after and not after.endswith(_NEWLINE) else after)
        )

    if not _write(rebuilt):
        return False

    audit_event(
        "hosts",
        "set_block",
        target=str(path()),
        new_state={"hosts": hosts},
        result="SUCCESS",
    )
    return True


def flush_dns(runner: CommandRunner | None = None) -> None:
    """Drop the resolver cache so the change takes effect now.

    Without this a name already resolved stays cached until its TTL runs
    out, and the operator sees a change that verified as applied and did
    nothing yet.
    """
    try:
        (runner or CommandRunner()).run(
            ["ipconfig", "/flushdns"], check=False, timeout_s=30
        )
    except PgmError as exc:
        _log.debug("could not flush the DNS cache: %s", exc.what)


__all__ = [
    "BEGIN_MARKER",
    "BLACKHOLE",
    "END_MARKER",
    "HOSTS_PATH",
    "blocked_hosts",
    "flush_dns",
    "other_entry_exists",
    "path",
    "set_block",
]
