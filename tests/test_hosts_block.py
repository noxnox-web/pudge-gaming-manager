"""The hosts file: a file PGM writes to but does not own.

Ad blockers, antivirus tools, corporate agents and people with Notepad all
edit this file. Every test here is a variation on one question: does PGM
leave their lines exactly as it found them?
"""

from __future__ import annotations

import pathlib

import pytest

from pudge_gaming_manager.core.optimization.tweak import (
    BackupRecord,
    BackupScope,
    Outcome,
    RiskLevel,
    TweakContext,
)
from pudge_gaming_manager.core.optimization.tweaks.dota_hosts import (
    DOTA_HOSTS,
    BlockDotaWebTweak,
)
from pudge_gaming_manager.windows import hosts

STOCK = (
    "# Copyright (c) 1993-2009 Microsoft Corp.\r\n"
    "#\r\n"
    "# This is a sample HOSTS file.\r\n"
    "#\r\n"
    "#\t127.0.0.1       localhost\r\n"
)

SOMEBODY_ELSE = (
    "# Added by an ad blocker\r\n"
    "0.0.0.0 ads.example.com\r\n"
    "127.0.0.1 tracker.example.net\r\n"
)


@pytest.fixture()
def hosts_file(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch):
    """Point the module at a throwaway file."""
    target = tmp_path / "hosts"
    target.write_bytes(STOCK.encode("utf-8"))
    monkeypatch.setattr(hosts, "path", lambda: target)
    monkeypatch.setattr(hosts, "flush_dns", lambda *_a, **_k: None)
    return target


def _text(path: pathlib.Path) -> str:
    with open(path, "r", encoding="utf-8", errors="surrogateescape", newline="") as h:
        return h.read()


# -- other people's lines ---------------------------------------------------


def test_existing_lines_survive_being_blocked(hosts_file: pathlib.Path) -> None:
    hosts_file.write_bytes((STOCK + SOMEBODY_ELSE).encode("utf-8"))

    hosts.set_block(list(DOTA_HOSTS))

    text = _text(hosts_file)
    assert "0.0.0.0 ads.example.com" in text
    assert "127.0.0.1 tracker.example.net" in text
    assert "# Added by an ad blocker" in text


def test_removing_our_block_restores_the_file_byte_for_byte(
    hosts_file: pathlib.Path,
) -> None:
    """The property the live round trip on the real file also demonstrated."""
    original = (STOCK + SOMEBODY_ELSE).encode("utf-8")
    hosts_file.write_bytes(original)

    hosts.set_block(list(DOTA_HOSTS))
    assert hosts_file.read_bytes() != original

    hosts.set_block([])

    assert hosts_file.read_bytes() == original


def test_repeated_writes_do_not_pile_up_blank_lines(
    hosts_file: pathlib.Path,
) -> None:
    for _ in range(5):
        hosts.set_block(list(DOTA_HOSTS))

    text = _text(hosts_file)
    assert text.count(hosts.BEGIN_MARKER) == 1
    assert "\r\n\r\n\r\n" not in text


def test_a_non_utf8_comment_survives_the_round_trip(
    hosts_file: pathlib.Path,
) -> None:
    """Somebody's ANSI Cyrillic comment must not be mangled.

    Decoding as UTF-8 and writing UTF-8 back would rewrite these bytes.
    """
    original = STOCK.encode("utf-8") + "# Ð·Ð°Ð¼ÐµÑ‚ÐºÐ°\r\n".encode("cp1251", "replace")
    hosts_file.write_bytes(original)

    hosts.set_block(list(DOTA_HOSTS))
    hosts.set_block([])

    assert hosts_file.read_bytes() == original


# -- reading our own block --------------------------------------------------


def test_only_our_block_is_reported_as_ours(hosts_file: pathlib.Path) -> None:
    hosts_file.write_bytes((STOCK + SOMEBODY_ELSE).encode("utf-8"))
    hosts.set_block(["dota2.com"])

    assert hosts.blocked_hosts() == ["dota2.com"]


def test_an_entry_outside_our_block_is_seen_as_somebody_else_s(
    hosts_file: pathlib.Path,
) -> None:
    hosts_file.write_bytes(
        (STOCK + "0.0.0.0 dota2.com\r\n").encode("utf-8")
    )

    assert hosts.blocked_hosts() == []
    assert hosts.other_entry_exists("dota2.com")
    assert not hosts.other_entry_exists("example.com")


def test_a_begin_marker_without_an_end_is_treated_as_ours(
    hosts_file: pathlib.Path,
) -> None:
    """Somebody deleted our end marker. Guessing where the block stopped
    would risk adopting their lines; everything after the marker we wrote
    is ours, and rewriting replaces it."""
    hosts_file.write_bytes(
        (STOCK + hosts.BEGIN_MARKER + "\r\n0.0.0.0 old.example\r\n").encode("utf-8")
    )

    hosts.set_block(list(DOTA_HOSTS))

    text = _text(hosts_file)
    assert "old.example" not in text
    assert text.count(hosts.BEGIN_MARKER) == 1
    assert text.count(hosts.END_MARKER) == 1


def test_an_unreadable_file_reports_unknown_not_empty(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Empty would mean "nothing is blocked", which is a different claim."""
    monkeypatch.setattr(hosts, "path", lambda: tmp_path / "missing")

    assert hosts.blocked_hosts() is None


# -- both spellings ---------------------------------------------------------


def test_both_names_are_blocked_because_hosts_has_no_wildcards() -> None:
    """dota2.com does not cover www.dota2.com; this is why forum copies fail."""
    assert "dota2.com" in DOTA_HOSTS
    assert "www.dota2.com" in DOTA_HOSTS


def test_the_blackhole_address_is_unroutable() -> None:
    """127.0.0.1 waits for a refusal; 0.0.0.0 fails at once."""
    assert hosts.BLACKHOLE == "0.0.0.0"


# -- the tweak --------------------------------------------------------------


def test_the_tweak_is_experimental_enough_to_need_the_opt_in() -> None:
    """One unreplicated report is not grounds for applying it by default."""
    assert BlockDotaWebTweak.risk is RiskLevel.MEDIUM
    assert not BlockDotaWebTweak.risk.auto_applicable


def test_its_rationale_states_the_evidence_and_the_cost() -> None:
    text = BlockDotaWebTweak.rationale + BlockDotaWebTweak.description
    assert "35438" in text, "the source issue must be citable"
    assert "один" in text.lower() or "единственн" in text.lower()
    assert "новост" in text, "the side effect must be stated, not implied"


def test_it_does_not_promise_frames() -> None:
    """Rule #65 — and here the frame rate is the whole of the claim."""
    assert "не обещается" in BlockDotaWebTweak.rationale


def test_a_full_cycle_leaves_the_file_as_it_was(hosts_file: pathlib.Path) -> None:
    original = (STOCK + SOMEBODY_ELSE).encode("utf-8")
    hosts_file.write_bytes(original)
    tweak = BlockDotaWebTweak()
    ctx = TweakContext()

    state = tweak.scan(ctx)
    assert state.needs_change
    backup = tweak.backup(ctx, state)
    assert tweak.apply(ctx, state).outcome is Outcome.SUCCESS
    assert tweak.verify(ctx, state).confirmed

    assert tweak.rollback(ctx, backup).outcome is Outcome.SUCCESS
    assert hosts_file.read_bytes() == original


def test_a_dry_run_changes_nothing(hosts_file: pathlib.Path) -> None:
    original = hosts_file.read_bytes()
    tweak = BlockDotaWebTweak()
    ctx = TweakContext(dry_run=True)

    tweak.apply(ctx, tweak.scan(TweakContext()))

    assert hosts_file.read_bytes() == original


def test_rollback_says_so_when_somebody_else_still_blocks_the_host(
    hosts_file: pathlib.Path,
) -> None:
    """Their line is not ours to delete, and claiming it resolves would be false."""
    hosts_file.write_bytes((STOCK + "0.0.0.0 dota2.com\r\n").encode("utf-8"))
    tweak = BlockDotaWebTweak()
    ctx = TweakContext()

    state = tweak.scan(ctx)
    tweak.apply(ctx, state)
    backup = BackupRecord(
        backup_id="x", tweak_id=tweak.id, tweak_version=1,
        scope=BackupScope.FILE, target="hosts", old_value="",
        old_value_absent=True, old_value_kind="HOSTS_BLOCK", new_value="",
    )

    result = tweak.rollback(ctx, backup)

    assert result.outcome is Outcome.SUCCESS
    assert "другая программа" in result.detail
    assert "0.0.0.0 dota2.com" in _text(hosts_file)


def test_applying_keeps_hosts_we_blocked_earlier(hosts_file: pathlib.Path) -> None:
    """Our block may hold entries from another tweak; applying adds, not replaces."""
    hosts.set_block(["something.else"])
    tweak = BlockDotaWebTweak()
    ctx = TweakContext()

    tweak.apply(ctx, tweak.scan(ctx))

    blocked = hosts.blocked_hosts() or []
    assert "something.else" in blocked
    assert set(DOTA_HOSTS) <= set(blocked)
