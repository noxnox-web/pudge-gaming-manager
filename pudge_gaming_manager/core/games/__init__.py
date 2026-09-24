"""Core-level facade for the game subsystems.

The GUI depends on ``core`` (never on ``games`` or ``windows`` directly), so
the Steam reset engine and its value types are re-exported here for the app
layer to import.
"""

from __future__ import annotations

from ...games.steam.wipe import CacheTarget, SteamWiper, WipePlan, WipeResult

__all__ = ["CacheTarget", "SteamWiper", "WipePlan", "WipeResult"]
