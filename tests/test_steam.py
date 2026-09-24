"""Tests for the Steam subsystem: VDF parsing, enumeration, and the wiper.

Every deletion test runs against a throwaway Steam-shaped tree the fixture
builds (rule #48). Nothing here touches a real Steam install, and the wiper's
process-stopping step is stubbed so no test kills anything.
"""

from __future__ import annotations

import os
import pathlib
import subprocess

import pytest

from pudge_gaming_manager.games.steam import library, vdf
from pudge_gaming_manager.games.steam.models import SteamInstall
from pudge_gaming_manager.games.steam.wipe import SteamWiper

windows_only = pytest.mark.skipif(os.name != "nt", reason="Windows-only behaviour")


# -- VDF / ACF parsing --------------------------------------------------------


def test_vdf_parses_nested_objects_and_escapes() -> None:
    text = r'''
    "AppState"
    {
        "appid"   "730"
        "name"    "Counter-Strike 2"
        "installdir" "Counter-Strike Global Offensive"
        // a comment Steam sometimes writes
        "path"    "C:\\Games\\CS"
    }
    '''
    parsed = vdf.loads(text)
    assert parsed["AppState"]["appid"] == "730"
    assert parsed["AppState"]["name"] == "Counter-Strike 2"
    assert parsed["AppState"]["path"] == r"C:\Games\CS"


def test_vdf_rejects_unterminated_input() -> None:
    with pytest.raises(vdf.VdfError):
        vdf.loads('"AppState" { "appid" "730"')


def test_vdf_rejects_a_bare_word() -> None:
    with pytest.raises(vdf.VdfError):
        vdf.loads("AppState { }")


# -- a fake Steam tree --------------------------------------------------------


def _manifest(app_id: int, name: str, install_dir: str, size: int) -> str:
    return (
        '"AppState"\n{\n'
        f'    "appid"   "{app_id}"\n'
        f'    "name"    "{name}"\n'
        f'    "installdir"  "{install_dir}"\n'
        f'    "SizeOnDisk"  "{size}"\n'
        "}\n"
    )


def _make_steam(root: pathlib.Path, games: dict[int, tuple[str, str, int]]) -> SteamInstall:
    """Build a Steam install with one library and the given games installed."""
    steamapps = root / "steamapps"
    common = steamapps / "common"
    common.mkdir(parents=True)
    (steamapps / "libraryfolders.vdf").write_text(
        '"libraryfolders"\n{\n  "0"\n  {\n'
        f'    "path"  "{str(root).replace(chr(92), chr(92) * 2)}"\n'
        "  }\n}\n",
        encoding="utf-8",
    )
    for app_id, (name, install_dir, size) in games.items():
        (steamapps / f"appmanifest_{app_id}.acf").write_text(
            _manifest(app_id, name, install_dir, size), encoding="utf-8"
        )
        game_dir = common / install_dir
        game_dir.mkdir()
        (game_dir / "game.bin").write_bytes(b"x" * 1000)
    return SteamInstall(path=root)


def _wiper() -> SteamWiper:
    wiper = SteamWiper()
    wiper._stop_steam = lambda: True  # never kill a real process in a test
    return wiper


# -- enumeration --------------------------------------------------------------


def test_installed_games_reads_every_manifest(tmp_path: pathlib.Path) -> None:
    install = _make_steam(tmp_path, {
        730: ("Counter-Strike 2", "cs2", 34_000_000_000),
        570: ("Dota 2", "dota2", 24_000_000_000),
    })
    libs = library.discover_libraries(install)
    games = {g.app_id: g for g in library.installed_games(libs)}
    assert set(games) == {730, 570}
    assert games[730].name == "Counter-Strike 2"
    assert games[730].size_bytes == 34_000_000_000
    assert games[730].install_path == install.steamapps / "common" / "cs2"


def test_a_corrupt_manifest_is_skipped_not_fatal(tmp_path: pathlib.Path) -> None:
    install = _make_steam(tmp_path, {730: ("CS2", "cs2", 100)})
    (install.steamapps / "appmanifest_999.acf").write_text("garbage {", encoding="utf-8")
    games = library.installed_games(library.discover_libraries(install))
    assert {g.app_id for g in games} == {730}


# -- planning -----------------------------------------------------------------


def test_scan_splits_games_by_the_keep_list(tmp_path: pathlib.Path) -> None:
    _make_steam(tmp_path, {
        730: ("CS2", "cs2", 30), 570: ("Dota 2", "dota2", 20), 4000: ("GMod", "gmod", 10),
    })
    wiper = _wiper()
    monkey_find(tmp_path)
    plan = wiper.scan(keep_app_ids={730, 570})
    assert {g.app_id for g in plan.keep} == {730, 570}
    assert {g.app_id for g in plan.remove} == {4000}


# -- deletion (against the fake tree) -----------------------------------------


def test_wipe_removes_only_non_kept_games(tmp_path: pathlib.Path) -> None:
    install = _make_steam(tmp_path, {
        730: ("CS2", "cs2", 30), 4000: ("GMod", "gmod", 10),
    })
    monkey_find(tmp_path)
    wiper = _wiper()
    plan = wiper.scan(keep_app_ids={730})
    result = wiper.wipe(plan)

    assert result.removed_games == 1
    assert (install.steamapps / "common" / "cs2").is_dir()  # kept
    assert not (install.steamapps / "common" / "gmod").exists()  # removed
    assert not (install.steamapps / "appmanifest_4000.acf").exists()
    assert (install.steamapps / "appmanifest_730.acf").exists()


def test_wipe_re_checks_the_keep_list_at_delete_time(tmp_path: pathlib.Path) -> None:
    install = _make_steam(tmp_path, {4000: ("GMod", "gmod", 10)})
    monkey_find(tmp_path)
    wiper = _wiper()
    plan = wiper.scan(keep_app_ids=set())
    # The operator changed their mind after the preview: move the game to keep.
    plan.keep = plan.remove
    result = wiper.wipe(plan)
    assert result.removed_games == 0
    assert (install.steamapps / "common" / "gmod").is_dir()


def test_dry_run_deletes_nothing(tmp_path: pathlib.Path) -> None:
    install = _make_steam(tmp_path, {4000: ("GMod", "gmod", 10)})
    monkey_find(tmp_path)
    wiper = _wiper()
    plan = wiper.scan(keep_app_ids=set())
    result = wiper.wipe(plan, dry_run=True)
    assert result.removed_games == 1
    assert (install.steamapps / "common" / "gmod").is_dir()  # still there


@windows_only
def test_wipe_refuses_a_game_folder_swapped_for_a_junction(
    tmp_path: pathlib.Path,
) -> None:
    """The core attack: a game folder becomes a junction to a protected tree
    after the scan. The handle-based tree delete refuses it, leaving the
    target untouched."""
    install = _make_steam(tmp_path, {4000: ("GMod", "gmod", 10)})
    protected = tmp_path / "protected"
    (protected).mkdir()
    (protected / "important.dat").write_bytes(b"keep me")

    monkey_find(tmp_path)
    wiper = _wiper()
    plan = wiper.scan(keep_app_ids=set())

    game_dir = install.steamapps / "common" / "gmod"
    import shutil

    shutil.rmtree(game_dir)
    subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(game_dir), str(protected)],
        check=True, capture_output=True,
    )
    result = wiper.wipe(plan)
    assert result.removed_games == 0
    assert result.refused
    assert (protected / "important.dat").exists()


# -- helpers ------------------------------------------------------------------


_ORIGINAL_FIND = library.find_steam


def monkey_find(root: pathlib.Path) -> None:
    """Point wipe.find_steam at the fake tree for the duration of one test."""
    from pudge_gaming_manager.games.steam import wipe

    wipe.find_steam = lambda: SteamInstall(path=root)


@pytest.fixture(autouse=True)
def _restore_find():
    yield
    from pudge_gaming_manager.games.steam import wipe

    wipe.find_steam = _ORIGINAL_FIND


# -- caches, workshop and sign-out, as SteamWiper does ----------------------


def _fake_profile(tmp_path: pathlib.Path, monkeypatch, name: str = "player") -> pathlib.Path:
    """A fake Windows user profile holding Steam's sign-in state."""
    from pudge_gaming_manager.games.steam import signout

    profile = tmp_path / "Users" / name
    steam = profile / "AppData" / "Local" / "Steam"
    (steam / "htmlcache").mkdir(parents=True)
    (steam / "htmlcache" / "Cookies").write_bytes(b"session")
    (steam / "local.vdf").write_text('"MachineUserConfigStore" { }', encoding="utf-8")
    monkeypatch.setattr(signout, "_profile_dirs", lambda: [profile])
    return steam


def _add_steam_state(install_root: pathlib.Path) -> None:
    steamapps = install_root / "steamapps"
    for folder in ("workshop/content/431960/123", "workshop/content/730/9", "sourcemods/mod"):
        (steamapps / folder).mkdir(parents=True)
        (steamapps / folder / "data.bin").write_bytes(b"x" * 500)
    config = install_root / "config"
    config.mkdir()
    (config / "config.vdf").write_text('"InstallConfigStore" { }', encoding="utf-8")
    (config / "libraryfolders.vdf").write_text('"libraryfolders" { }', encoding="utf-8")
    (config / "loginusers.vdf").write_text(
        '"users" { "7656119800000001" { "AccountName" "a" } '
        '"7656119800000002" { "AccountName" "b" } }',
        encoding="utf-8",
    )
    (config / "avatarcache").mkdir()
    (config / "avatarcache" / "a.png").write_bytes(b"png")


def test_reset_clears_workshop_even_for_kept_games(tmp_path, monkeypatch) -> None:
    """As SteamWiper: workshop content piles up on club PCs; clear it whole."""
    install = _make_steam(tmp_path, {431960: ("Wallpaper Engine", "we", 10)})
    _add_steam_state(tmp_path)
    monkey_find(tmp_path)
    wiper = _wiper()
    result = wiper.wipe(wiper.scan(keep_app_ids={431960}))

    assert (install.steamapps / "common" / "we").is_dir()  # the game is kept
    assert not (install.steamapps / "workshop").exists()  # its workshop is not
    assert not (install.steamapps / "sourcemods").exists()
    assert result.removed_games == 0


def test_signout_empties_config_but_keeps_its_settings(tmp_path, monkeypatch) -> None:
    _make_steam(tmp_path, {730: ("CS2", "cs2", 10)})
    _add_steam_state(tmp_path)
    steam_user = _fake_profile(tmp_path, monkeypatch)
    monkey_find(tmp_path)
    wiper = _wiper()
    plan = wiper.scan(keep_app_ids={730})
    assert plan.accounts == 2

    result = wiper.wipe(plan)
    config = tmp_path / "config"
    assert sorted(p.name for p in config.iterdir()) == ["config.vdf", "libraryfolders.vdf"]
    assert not (steam_user / "local.vdf").exists()  # saved tokens gone
    assert not (steam_user / "htmlcache").exists()  # web cookies gone
    assert result.signed_out is True


def test_signout_is_reported_failed_when_a_token_file_is_refused(tmp_path, monkeypatch) -> None:
    from pudge_gaming_manager.games.steam import targets
    from pudge_gaming_manager.utilities.secure_delete import DeleteError

    _make_steam(tmp_path, {730: ("CS2", "cs2", 10)})
    _add_steam_state(tmp_path)
    _fake_profile(tmp_path, monkeypatch)
    monkey_find(tmp_path)

    def refuse(path):
        raise DeleteError(f"{path} is a reparse point; refusing to delete")

    monkeypatch.setattr(targets, "delete_file", refuse)
    wiper = _wiper()
    result = wiper.wipe(wiper.scan(keep_app_ids={730}))
    assert result.signed_out is False
    assert any("sign-in tokens" in line for line in result.refused)


def test_dry_run_signs_nobody_out(tmp_path, monkeypatch) -> None:
    _make_steam(tmp_path, {730: ("CS2", "cs2", 10)})
    _add_steam_state(tmp_path)
    steam_user = _fake_profile(tmp_path, monkeypatch)
    monkey_find(tmp_path)
    wiper = _wiper()
    wiper.wipe(wiper.scan(keep_app_ids={730}), dry_run=True)
    assert (steam_user / "local.vdf").exists()
    assert (tmp_path / "config" / "loginusers.vdf").exists()


@windows_only
def test_config_child_swapped_for_a_junction_is_refused(tmp_path, monkeypatch) -> None:
    """A junction planted inside config must not aim the delete elsewhere."""
    _make_steam(tmp_path, {730: ("CS2", "cs2", 10)})
    _add_steam_state(tmp_path)
    monkey_find(tmp_path)
    wiper = _wiper()
    plan = wiper.scan(keep_app_ids={730})

    victim = tmp_path / "victim"
    victim.mkdir()
    (victim / "important.dat").write_bytes(b"keep me")
    import shutil

    shutil.rmtree(tmp_path / "config" / "avatarcache")
    subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(tmp_path / "config" / "avatarcache"), str(victim)],
        check=True, capture_output=True,
    )
    result = wiper.wipe(plan)
    assert (victim / "important.dat").exists()
    assert result.signed_out is False
    assert result.refused


@windows_only
def test_a_redirected_profile_steam_folder_is_skipped(tmp_path, monkeypatch) -> None:
    r"""If a player's AppData\Local\Steam is a junction, it is not a target."""
    from pudge_gaming_manager.games.steam import signout

    victim = tmp_path / "victim"
    victim.mkdir()
    (victim / "local.vdf").write_text("not steam", encoding="utf-8")
    profile = tmp_path / "Users" / "player"
    (profile / "AppData" / "Local").mkdir(parents=True)
    subprocess.run(
        ["cmd", "/c", "mklink", "/J",
         str(profile / "AppData" / "Local" / "Steam"), str(victim)],
        check=True, capture_output=True,
    )
    monkeypatch.setattr(signout, "_profile_dirs", lambda: [profile])
    install = _make_steam(tmp_path / "steam", {730: ("CS2", "cs2", 10)})
    labels = [t.label for t in signout.signout_targets(install)]
    assert not any("player" in label for label in labels)


# -- built-in keep-list and preview override (no profile needed) -------------


def test_default_keep_list_covers_popular_titles_and_the_runtime() -> None:
    from pudge_gaming_manager.games.steam.default_keep import (
        DEFAULT_KEEP,
        DEFAULT_KEEP_IDS,
    )

    assert 730 in DEFAULT_KEEP_IDS  # CS2
    assert 570 in DEFAULT_KEEP_IDS  # Dota 2
    assert 228980 in DEFAULT_KEEP_IDS  # Steamworks Common Redistributables
    assert set(DEFAULT_KEEP) == set(DEFAULT_KEEP_IDS)
    assert all(isinstance(i, int) and i > 0 for i in DEFAULT_KEEP_IDS)


def test_with_kept_back_moves_a_game_from_remove_to_keep(tmp_path) -> None:
    _make_steam(tmp_path, {
        730: ("CS2", "cs2", 30), 4000: ("GMod", "gmod", 10), 999: ("Indie", "indie", 5),
    })
    monkey_find(tmp_path)
    plan = _wiper().scan(keep_app_ids={730})  # CS2 kept; GMod + Indie to remove
    assert {g.app_id for g in plan.remove} == {4000, 999}

    narrowed = plan.with_kept_back({999})  # operator rescues the indie game
    assert {g.app_id for g in narrowed.remove} == {4000}
    assert {g.app_id for g in narrowed.keep} == {730, 999}
    # caches and sign-out are unchanged by a rescue
    assert narrowed.caches == plan.caches


def test_rescued_game_is_not_deleted(tmp_path) -> None:
    install = _make_steam(tmp_path, {4000: ("GMod", "gmod", 10)})
    monkey_find(tmp_path)
    wiper = _wiper()
    plan = wiper.scan(keep_app_ids=set())  # nothing kept by default
    wiper.wipe(plan.with_kept_back({4000}))  # but the operator unticks it
    assert (install.steamapps / "common" / "gmod").is_dir()
