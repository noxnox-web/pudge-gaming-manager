"""Core-level facade for the startup manager.

The GUI depends on ``core`` and never on ``windows``, so the autostart
types are re-exported here. There is no service object: listing and
toggling are already the whole domain, and wrapping a two-method manager in
another two-method class would add a layer that owns no decision.
"""

from __future__ import annotations

from ...windows.startup.manager import StartupEntry, StartupManager, StartupSource

__all__ = ["StartupEntry", "StartupManager", "StartupSource"]
