"""The built-in keep-list: Steam games a reset never removes by default.

SteamWiper reads these app IDs from a ``config.ini``; PGM bakes them into the
program instead, so a reset works with one click and no configuration file to
lose or mis-edit. The list is the *default* only — the reset preview shows
every game it would remove and lets the operator untick any of them, so a
club game missing from this list is never deleted without being seen first.

To change the defaults, edit this file and rebuild. IDs are Steam application
IDs (the number in a game's store URL, ``store.steampowered.com/app/<id>``).
"""

from __future__ import annotations

#: app_id -> display name. Popular competitive and club titles, plus the
#: shared runtime every Steam PC needs. IDs are verified against the Steam
#: store (store.steampowered.com/app/<id>).
#:
#: Escape from Tarkov is deliberately absent: it is not on Steam (it installs
#: through Battlestate Games' own launcher), so a Steam reset never sees or
#: touches it — there is nothing to keep here.
DEFAULT_KEEP: dict[int, str] = {
    228980: "Steamworks Common Redistributables",  # shared runtime — never a game
    # -- explicitly requested for club PCs --
    570: "Dota 2",
    730: "Counter-Strike 2",
    1172470: "Apex Legends",
    578080: "PUBG: BATTLEGROUNDS",
    1422450: "Deadlock",
    252490: "Rust",
    3240220: "Grand Theft Auto V Enhanced",
    271590: "Grand Theft Auto V",  # legacy edition, kept if still installed
    381210: "Dead by Daylight",
    # -- other common club titles --
    440: "Team Fortress 2",
    550: "Left 4 Dead 2",
    4000: "Garry's Mod",
    359550: "Rainbow Six Siege",
    252950: "Rocket League",
    230410: "Warframe",
    236390: "War Thunder",
    105600: "Terraria",
    892970: "Valheim",
    1085660: "Destiny 2",
    1203220: "Naraka: Bladepoint",
    945360: "Among Us",
    620: "Portal 2",
    291550: "Brawlhalla",
    238960: "Path of Exile",
}

#: Just the IDs, for the reset's keep-set.
DEFAULT_KEEP_IDS: frozenset[int] = frozenset(DEFAULT_KEEP)


__all__ = ["DEFAULT_KEEP", "DEFAULT_KEEP_IDS"]
