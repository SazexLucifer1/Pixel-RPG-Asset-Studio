"""Filesystem locations used by the application.

Rules:
* User configuration lives OUTSIDE the source tree (``%APPDATA%/PixelRPGAssetStudio``
  on Windows, ``~/.config/PixelRPGAssetStudio`` elsewhere).
* ``PIXEL_RPG_STUDIO_HOME`` overrides the location (used by tests and for
  "portable" setups where the user keeps everything on one drive).
* Bundled read-only resources (workflows, default config, Blender scripts) are
  found both when running from source and from a PyInstaller build.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from pixel_rpg_studio import APP_ID

ENV_HOME = "PIXEL_RPG_STUDIO_HOME"


def is_frozen() -> bool:
    """True when running from a PyInstaller-built executable."""
    return bool(getattr(sys, "frozen", False))


def resource_root() -> Path:
    """Directory that contains the bundled ``workflows/`` and ``config/`` folders."""
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    # src/pixel_rpg_studio/core/paths.py -> repository root
    return Path(__file__).resolve().parents[3]


def package_root() -> Path:
    """Directory of the ``pixel_rpg_studio`` package."""
    if is_frozen():
        return resource_root() / "pixel_rpg_studio"
    return Path(__file__).resolve().parents[1]


def bundled_workflows_dir() -> Path:
    return resource_root() / "workflows"


def bundled_config_dir() -> Path:
    return resource_root() / "config"


def blender_scripts_dir() -> Path:
    return package_root() / "blender" / "scripts"


def app_data_dir() -> Path:
    """Per-user writable directory for settings, logs and caches."""
    override = os.environ.get(ENV_HOME)
    if override:
        base = Path(override)
    elif sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming") / APP_ID
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / APP_ID
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / APP_ID
    base.mkdir(parents=True, exist_ok=True)
    return base


def logs_dir() -> Path:
    path = app_data_dir() / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def cache_dir() -> Path:
    path = app_data_dir() / "cache"
    path.mkdir(parents=True, exist_ok=True)
    return path


def default_projects_dir() -> Path:
    """Default location for new projects (the user's Documents folder)."""
    docs = Path.home() / "Documents"
    base = docs if docs.exists() else Path.home()
    return base / "PixelRPGAssetStudio" / "Projects"


def safe_filename(name: str, fallback: str = "untitled", max_length: int = 64) -> str:
    """Convert arbitrary user text into a safe, portable file/folder name.

    Windows-reserved names and characters are removed; the result is lowercase
    ``snake_case`` so it also works as a Godot resource path.
    """
    cleaned = []
    for ch in name.strip().lower():
        if ch.isalnum() and ch.isascii():
            cleaned.append(ch)
        elif ch in " -_.":
            cleaned.append("_")
    result = "".join(cleaned)
    while "__" in result:
        result = result.replace("__", "_")
    result = result.strip("_.")[:max_length].strip("_.")
    reserved = {"con", "prn", "aux", "nul"} | {f"com{i}" for i in range(1, 10)} | {f"lpt{i}" for i in range(1, 10)}
    if not result or result in reserved:
        result = fallback if not result else f"{result}_"
    return result


def is_within(path: Path, root: Path) -> bool:
    """True if ``path`` is ``root`` or located inside it (after resolving)."""
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def is_writable_dir(path: Path) -> bool:
    """Check that a directory exists (or can be created) and is writable."""
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".write_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False
