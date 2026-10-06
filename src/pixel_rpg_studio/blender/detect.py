"""Locate a Blender executable (never assumes a fixed path)."""

from __future__ import annotations

import glob
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

MIN_VERSION = (3, 6)


@dataclass
class BlenderInfo:
    executable: Path
    version: tuple[int, int, int]
    version_text: str

    @property
    def version_str(self) -> str:
        return ".".join(str(v) for v in self.version)

    @property
    def supported(self) -> bool:
        return self.version[:2] >= MIN_VERSION


def _version_key(path: str) -> tuple[int, ...]:
    m = re.search(r"(\d+)\.(\d+)", path)
    return tuple(int(x) for x in m.groups()) if m else (0, 0)


def candidate_paths() -> list[Path]:
    found: list[Path] = []
    which = shutil.which("blender")
    if which:
        found.append(Path(which))
    if sys.platform == "win32":
        pf = [os.environ.get("ProgramFiles", r"C:\Program Files"), os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")]
        patterns = []
        for base in pf:
            patterns.append(os.path.join(base, "Blender Foundation", "Blender*", "blender.exe"))
            patterns.append(os.path.join(base, "Steam", "steamapps", "common", "Blender", "blender.exe"))
        patterns.append(os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Blender Foundation", "Blender*", "blender.exe"))
        # Microsoft Store installs
        patterns.append(os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "WindowsApps", "BlenderFoundation.Blender*", "Blender", "blender.exe"))
        for pattern in patterns:
            for p in sorted(glob.glob(pattern), key=_version_key, reverse=True):
                found.append(Path(p))
    elif sys.platform == "darwin":
        found.append(Path("/Applications/Blender.app/Contents/MacOS/Blender"))
    else:
        found += [Path("/usr/bin/blender"), Path("/snap/bin/blender"), Path("/usr/local/bin/blender")]
        found += [Path(p) for p in sorted(glob.glob(str(Path.home() / "blender*" / "blender")), key=_version_key, reverse=True)]
    unique: list[Path] = []
    for p in found:
        if p.exists() and p not in unique:
            unique.append(p)
    return unique


def parse_version(text: str) -> tuple[int, int, int] | None:
    m = re.search(r"Blender\s+(\d+)\.(\d+)(?:\.(\d+))?", text)
    if not m:
        return None
    return int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)


def probe(executable: Path | str, timeout: float = 60.0) -> BlenderInfo | None:
    exe = Path(executable)
    if not exe.exists():
        return None
    kwargs: dict = {"capture_output": True, "text": True, "timeout": timeout}
    if sys.platform == "win32":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    try:
        result = subprocess.run([str(exe), "--background", "--factory-startup", "--version"], **kwargs)
    except (OSError, subprocess.TimeoutExpired):
        return None
    text = (result.stdout or "") + (result.stderr or "")
    version = parse_version(text)
    if version is None:
        return None
    return BlenderInfo(exe, version, text.strip().splitlines()[0] if text.strip() else "")


def find_blender(configured: str = "") -> BlenderInfo | None:
    """Configured path first, then auto-detection (newest supported version wins)."""
    if configured:
        info = probe(configured)
        if info:
            return info
    for path in candidate_paths():
        info = probe(path)
        if info and info.supported:
            return info
    return None
