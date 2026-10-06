"""Detect and start local ComfyUI installations.

Supported layouts (no fixed install path is assumed):

* **Portable** (``ComfyUI_windows_portable``): ``python_embeded/python.exe`` +
  ``ComfyUI/main.py``. Started headless with ``--disable-auto-launch``.
* **Desktop** (ComfyUI Desktop / Electron app): ``ComfyUI.exe``; its data
  folder (models, custom nodes) is read from ``%APPDATA%/ComfyUI/config.json``.
  The desktop app manages its own server (default port 8000) - we can launch
  it but cannot pass server flags.
* **Manual** (git clone): ``main.py`` + a ``venv``/``.venv`` python.
* **Remote**: an already running server (nothing to start).
"""

from __future__ import annotations

import json
import logging
import os
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from pixel_rpg_studio.core import paths
from pixel_rpg_studio.core.config import AppSettings
from pixel_rpg_studio.core.errors import BackendUnavailableError, StudioError

log = logging.getLogger(__name__)

DESKTOP_DEFAULT_PORT = 8000
PORTABLE_DEFAULT_PORT = 8188


@dataclass
class ComfyInstall:
    kind: str  # portable | desktop | manual
    root: Path
    python: Path | None = None
    main_py: Path | None = None
    executable: Path | None = None  # desktop app exe
    data_dir: Path | None = None  # folder containing models/ and custom_nodes/
    default_port: int = PORTABLE_DEFAULT_PORT
    notes: list[str] = field(default_factory=list)

    @property
    def models_dir(self) -> Path | None:
        return self.data_dir / "models" if self.data_dir else None

    @property
    def custom_nodes_dir(self) -> Path | None:
        return self.data_dir / "custom_nodes" if self.data_dir else None

    @property
    def can_autostart(self) -> bool:
        if self.kind == "desktop":
            return bool(self.executable and self.executable.exists())
        return bool(self.python and self.python.exists() and self.main_py and self.main_py.exists())

    def describe(self) -> str:
        return f"{self.kind} installation at {self.root}"


def _exe(name: str) -> str:
    return f"{name}.exe" if sys.platform == "win32" else name


def _venv_python(root: Path) -> Path | None:
    for venv in ("venv", ".venv", "env"):
        for candidate in (root / venv / "Scripts" / "python.exe", root / venv / "bin" / "python"):
            if candidate.exists():
                return candidate
    return None


def desktop_config_base_path() -> Path | None:
    """Read the ComfyUI Desktop data folder from its config.json (Windows/macOS)."""
    candidates = []
    if sys.platform == "win32":
        candidates.append(Path(os.environ.get("APPDATA", "")) / "ComfyUI" / "config.json")
    elif sys.platform == "darwin":
        candidates.append(Path.home() / "Library" / "Application Support" / "ComfyUI" / "config.json")
    else:
        candidates.append(Path.home() / ".config" / "ComfyUI" / "config.json")
    for cfg in candidates:
        try:
            data = json.loads(cfg.read_text(encoding="utf-8"))
            base = data.get("basePath")
            if base:
                return Path(base)
        except (OSError, ValueError):
            continue
    return None


def detect_install(path: Path | str) -> ComfyInstall | None:
    """Identify the ComfyUI layout in (or around) ``path``."""
    if not path:
        return None
    root = Path(path).expanduser()
    if root.is_file():
        if root.name.lower() in ("comfyui.exe", "comfyui"):
            return _desktop_from_exe(root)
        root = root.parent
    if not root.exists():
        return None

    # Portable: root/python_embeded + root/ComfyUI/main.py (user may select either folder)
    for portable_root in (root, root.parent):
        embedded = portable_root / "python_embeded" / "python.exe"
        main_py = portable_root / "ComfyUI" / "main.py"
        if embedded.exists() and main_py.exists():
            return ComfyInstall("portable", portable_root, python=embedded, main_py=main_py, data_dir=portable_root / "ComfyUI")

    # Desktop app folder (contains ComfyUI.exe)
    exe = root / "ComfyUI.exe"
    if exe.exists():
        return _desktop_from_exe(exe)

    # Desktop data folder (basePath) selected directly: has models/ but no main.py
    if (root / "models").is_dir() and not (root / "main.py").exists() and (root / ".venv").exists():
        inst = ComfyInstall("desktop", root, data_dir=root, default_port=DESKTOP_DEFAULT_PORT)
        exe_path = find_desktop_executable()
        inst.executable = exe_path
        if exe_path is None:
            inst.notes.append("ComfyUI Desktop data folder found, but ComfyUI.exe was not found; start it manually.")
        return inst

    # Manual git install
    main_py = root / "main.py"
    if main_py.exists() and (root / "comfy").is_dir():
        python = _venv_python(root) or _venv_python(root.parent)
        inst = ComfyInstall("manual", root, python=python, main_py=main_py, data_dir=root)
        if python is None:
            inst.notes.append("No virtual environment (venv/.venv) found next to main.py; configure a Python or start ComfyUI manually.")
        return inst
    return None


def _desktop_from_exe(exe: Path) -> ComfyInstall:
    base = desktop_config_base_path()
    inst = ComfyInstall("desktop", exe.parent, executable=exe, data_dir=base, default_port=DESKTOP_DEFAULT_PORT)
    if base is None:
        inst.notes.append("Could not read the ComfyUI Desktop data folder from its config.json.")
    return inst


def find_desktop_executable() -> Path | None:
    if sys.platform != "win32":
        return None
    local = Path(os.environ.get("LOCALAPPDATA", ""))
    for candidate in (
        local / "Programs" / "@comfyorgcomfyui-electron" / "ComfyUI.exe",
        local / "Programs" / "ComfyUI" / "ComfyUI.exe",
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "ComfyUI" / "ComfyUI.exe",
    ):
        if candidate.exists():
            return candidate
    return None


def find_installations() -> list[ComfyInstall]:
    """Search common locations. Never assumes a single fixed path."""
    found: list[ComfyInstall] = []
    seen: set[str] = set()

    def add(inst: ComfyInstall | None) -> None:
        if inst and str(inst.root.resolve()) not in seen:
            seen.add(str(inst.root.resolve()))
            found.append(inst)

    exe = find_desktop_executable()
    if exe:
        add(_desktop_from_exe(exe))
    home = Path.home()
    roots = [home, home / "Desktop", home / "Documents", home / "Downloads", home / "AI"]
    if sys.platform == "win32":
        for drive in "CDEFG":
            d = Path(f"{drive}:/")
            if d.exists():
                roots += [d, d / "AI", d / "Tools", d / "ComfyUI"]
    names = ("ComfyUI_windows_portable", "ComfyUI_windows_portable_nvidia", "ComfyUI", "comfyui")
    for r in roots:
        for n in names:
            candidate = r / n
            if candidate.exists():
                add(detect_install(candidate))
    base = desktop_config_base_path()
    if base and base.exists() and exe is None:
        add(detect_install(base))
    return found


def build_launch_command(install: ComfyInstall, settings: AppSettings, low_vram: bool) -> tuple[list[str], Path]:
    if install.kind == "desktop":
        if not install.executable:
            raise StudioError("ComfyUI Desktop executable not found.", hint="Start ComfyUI Desktop manually.")
        return [str(install.executable)], install.executable.parent
    if not install.can_autostart:
        raise StudioError(
            f"Cannot start the {install.kind} ComfyUI installation automatically.",
            hint="; ".join(install.notes) or "Start ComfyUI manually, then press 'Check again'.",
        )
    cmd = [str(install.python), "-s", str(install.main_py)]
    if install.kind == "portable":
        cmd.append("--windows-standalone-build")
    cmd += ["--listen", settings.comfyui.host, "--port", str(settings.comfyui.port), "--disable-auto-launch"]
    if low_vram:
        cmd.append("--lowvram")
    if settings.comfyui.extra_launch_args.strip():
        cmd += shlex.split(settings.comfyui.extra_launch_args, posix=(sys.platform != "win32"))
    return cmd, install.root


class ComfyUIProcess:
    """A ComfyUI server process started by this application."""

    def __init__(self) -> None:
        self.process: subprocess.Popen | None = None
        self.log_path = paths.logs_dir() / "comfyui.log"
        self.install: ComfyInstall | None = None
        self._log_handle = None

    @property
    def running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def start(self, install: ComfyInstall, settings: AppSettings, low_vram: bool) -> None:
        if self.running:
            return
        cmd, cwd = build_launch_command(install, settings, low_vram)
        log.info("Starting ComfyUI: %s (cwd=%s)", " ".join(cmd), cwd)
        self._log_handle = open(self.log_path, "a", encoding="utf-8", errors="replace")
        self._log_handle.write(f"\n===== Starting ComfyUI {time.strftime('%Y-%m-%d %H:%M:%S')} =====\n{' '.join(cmd)}\n")
        self._log_handle.flush()
        kwargs: dict = {"cwd": str(cwd), "stdout": self._log_handle, "stderr": subprocess.STDOUT, "stdin": subprocess.DEVNULL}
        if sys.platform == "win32":
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        kwargs["env"] = env
        try:
            self.process = subprocess.Popen(cmd, **kwargs)
        except OSError as exc:
            raise StudioError(
                "ComfyUI could not be started.",
                hint="Check the ComfyUI folder in Settings, or start ComfyUI manually.",
                details=f"{' '.join(cmd)}\n{exc}",
                code="comfyui_start_failed",
            ) from exc
        self.install = install

    def log_tail(self, lines: int = 60) -> str:
        try:
            return "\n".join(self.log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:])
        except OSError:
            return ""

    def wait_until_ready(self, is_alive: Callable[[], bool], timeout: float, progress: Callable[[str], None] | None = None,
                         cancelled: Callable[[], bool] | None = None) -> None:
        start = time.time()
        while time.time() - start < timeout:
            if cancelled and cancelled():
                return
            if is_alive():
                return
            if self.process is not None and self.process.poll() is not None and (self.install and self.install.kind != "desktop"):
                raise BackendUnavailableError(
                    f"ComfyUI exited during startup (exit code {self.process.returncode}).",
                    hint="Open the ComfyUI log for the reason (often a missing dependency or a broken custom node).",
                    details=self.log_tail(),
                    code="comfyui_crashed",
                )
            if progress:
                progress(f"Waiting for ComfyUI to start... {int(time.time() - start)} s")
            time.sleep(1.0)
        raise BackendUnavailableError(
            f"ComfyUI did not become ready within {int(timeout)} s.",
            hint="The first start can take several minutes. Increase the start timeout in Settings or check the ComfyUI log.",
            details=self.log_tail(),
            code="comfyui_start_timeout",
        )

    def stop(self, timeout: float = 10.0) -> None:
        if self.process is None:
            return
        if self.process.poll() is None:
            log.info("Stopping ComfyUI (pid %s)", self.process.pid)
            self.process.terminate()
            try:
                self.process.wait(timeout)
            except subprocess.TimeoutExpired:
                self.process.kill()
        self.process = None
        if self._log_handle:
            self._log_handle.close()
            self._log_handle = None


def resolve_install(settings: AppSettings) -> ComfyInstall | None:
    """The configured install, or the first auto-detected one."""
    if settings.comfyui.install_type == "remote":
        return None
    if settings.comfyui.install_dir:
        return detect_install(settings.comfyui.install_dir)
    installs = find_installations()
    return installs[0] if installs else None


def models_dir(settings: AppSettings) -> Path | None:
    if settings.paths.comfyui_models_dir:
        return Path(settings.paths.comfyui_models_dir)
    inst = detect_install(settings.comfyui.install_dir) if settings.comfyui.install_dir else None
    return inst.models_dir if inst else None


def apply_install_to_settings(install: ComfyInstall, settings: AppSettings) -> None:
    """Store a detected installation in the settings (incl. the right default port)."""
    settings.comfyui.install_dir = str(install.executable if install.kind == "desktop" and install.executable else install.root)
    settings.comfyui.install_type = install.kind
    if install.kind == "desktop" and settings.comfyui.port == PORTABLE_DEFAULT_PORT:
        settings.comfyui.port = DESKTOP_DEFAULT_PORT
    elif install.kind != "desktop" and settings.comfyui.port == DESKTOP_DEFAULT_PORT:
        settings.comfyui.port = PORTABLE_DEFAULT_PORT
