"""Architectural rules, enforced as tests rather than as documentation.

A convention nobody can violate accidentally is worth more than a paragraph in
a style guide. These tests fail the build when the layering or the command
discipline is broken.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

PACKAGE_ROOT = pathlib.Path(__file__).resolve().parents[1] / "pudge_gaming_manager"

#: Layer -> layers it is permitted to import from. Dependencies point downward.
ALLOWED_DEPENDENCIES: dict[str, set[str]] = {
    "app": {"core", "utilities"},
    "core": {"windows", "nvidia", "network", "hardware", "games", "database", "utilities"},
    "windows": {"utilities", "database"},
    "nvidia": {"utilities"},
    "network": {"utilities"},
    "hardware": {"utilities"},
    "games": {"utilities"},
    "database": {"utilities"},
    "utilities": set(),
    "profiles": set(),
    # Package-root entry shims (__main__.py) exist only to launch the GUI.
    "__root__": {"app", "utilities"},
}

#: The only module allowed to import subprocess (rule #49).
SUBPROCESS_OWNER = "utilities/command_runner.py"


def _python_files() -> list[pathlib.Path]:
    return [p for p in PACKAGE_ROOT.rglob("*.py") if p.name != "__init__.py"]


def _layer_of(path: pathlib.Path) -> str:
    parts = path.relative_to(PACKAGE_ROOT).parts
    # A module sitting directly in the package root is an entry shim, not a
    # layer of its own.
    return parts[0] if len(parts) > 1 else "__root__"


def _imported_layers(path: pathlib.Path) -> set[str]:
    """Return the package's own layers that ``path`` imports from."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    own_layer = _layer_of(path)
    found: set[str] = set()
    # The package this module lives in, as path segments below the root.
    package = list(path.relative_to(PACKAGE_ROOT).parts[:-1])

    for node in ast.walk(tree):
        module: str | None = None
        if isinstance(node, ast.ImportFrom):
            if node.level:  # relative import: resolve it against the package
                # Level 1 is this package, each further level one up. Taking
                # the first segment of ``node.module`` instead — as this used
                # to — read ``..profiles`` from core/settings as the top-level
                # ``profiles`` layer rather than core.profiles.
                base = package[: len(package) - (node.level - 1)]
                if node.level - 1 > len(package):
                    continue
                parts = base + (node.module.split(".") if node.module else [])
                if not parts:
                    continue
                layer = parts[0]
                if layer in ALLOWED_DEPENDENCIES:
                    found.add(layer)
                continue
            module = node.module
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("pudge_gaming_manager."):
                    found.add(alias.name.split(".")[1])
            continue

        if not module:
            continue
        if module.startswith("pudge_gaming_manager."):
            found.add(module.split(".")[1])

    found.discard(own_layer)
    return found


@pytest.mark.parametrize("path", _python_files(), ids=lambda p: str(p.name))
def test_layer_dependencies_point_downward(path: pathlib.Path) -> None:
    """No module imports from a layer above or beside its own."""
    layer = _layer_of(path)
    allowed = ALLOWED_DEPENDENCIES.get(layer)
    assert allowed is not None, f"Unknown layer {layer!r} for {path}"

    violations = _imported_layers(path) - allowed
    assert not violations, (
        f"{path.relative_to(PACKAGE_ROOT)} (layer '{layer}') imports from "
        f"{sorted(violations)}, which it may not depend on. "
        f"Allowed: {sorted(allowed) or 'nothing'}."
    )


def test_subprocess_is_confined_to_command_runner() -> None:
    """``subprocess`` is imported in exactly one module (rule #49)."""
    offenders: list[str] = []
    for path in _python_files():
        rel = path.relative_to(PACKAGE_ROOT).as_posix()
        if rel == SUBPROCESS_OWNER:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                if any(a.name == "subprocess" for a in node.names):
                    offenders.append(rel)
            elif isinstance(node, ast.ImportFrom) and node.module == "subprocess":
                offenders.append(rel)

    assert not offenders, (
        "subprocess may only be imported by "
        f"{SUBPROCESS_OWNER}; found in: {sorted(set(offenders))}"
    )


def test_gui_layer_cannot_touch_the_system_directly() -> None:
    """The GUI cannot reach the registry or the OS behind ``core`` (rule #12)."""
    forbidden = {"winreg", "subprocess", "ctypes", "win32api", "win32service"}
    offenders: list[str] = []

    for path in _python_files():
        if _layer_of(path) != "app":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module.split(".")[0]]
            hits = forbidden.intersection(names)
            if hits:
                offenders.append(f"{path.relative_to(PACKAGE_ROOT)}: {sorted(hits)}")

    assert not offenders, (
        "GUI modules must go through core/, never the OS directly:\n  "
        + "\n  ".join(offenders)
    )


def test_no_module_is_oversized() -> None:
    """Keep modules reviewable (rule #61): no file over 500 lines."""
    oversized = [
        f"{p.relative_to(PACKAGE_ROOT)} ({len(p.read_text(encoding='utf-8').splitlines())} lines)"
        for p in _python_files()
        if len(p.read_text(encoding="utf-8").splitlines()) > 500
    ]
    assert not oversized, "Split these modules:\n  " + "\n  ".join(oversized)
