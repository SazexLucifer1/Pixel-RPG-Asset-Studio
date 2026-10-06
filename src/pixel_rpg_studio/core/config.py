"""User settings, stored as JSON outside the repository.

Location: ``<app_data_dir>/settings.json`` (``%APPDATA%/PixelRPGAssetStudio`` on
Windows). Settings are dataclasses so they are typed, documented and have
defaults; unknown keys from newer versions are preserved on save so a
downgrade never destroys configuration.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import asdict, dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any

from pixel_rpg_studio.core import paths
from pixel_rpg_studio.core.errors import ConfigError

log = logging.getLogger(__name__)

SETTINGS_FILE = "settings.json"
SETTINGS_VERSION = 1

COMFYUI_INSTALL_TYPES = ("auto", "portable", "desktop", "manual", "remote")
VRAM_PROFILES = ("auto", "low", "medium", "high")
LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")


@dataclass
class ComfyUISettings:
    host: str = "127.0.0.1"
    port: int = 8188
    install_dir: str = ""  # empty = not configured / try auto-detect
    install_type: str = "auto"  # one of COMFYUI_INSTALL_TYPES
    auto_start: bool = True
    extra_launch_args: str = ""
    start_timeout_s: int = 180
    request_timeout_s: int = 30
    generation_timeout_s: int = 1800

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"


@dataclass
class BlenderSettings:
    executable: str = ""  # empty = auto-detect
    render_timeout_s: int = 1800


@dataclass
class PathSettings:
    projects_dir: str = ""  # empty = Documents/PixelRPGAssetStudio/Projects
    default_export_dir: str = ""
    godot_project_dir: str = ""
    comfyui_models_dir: str = ""  # override; empty = derived from ComfyUI install


@dataclass
class GPUSettings:
    vram_profile: str = "auto"  # one of VRAM_PROFILES
    unload_models_between_stages: bool = True
    sequential_stages: bool = True


@dataclass
class ProviderSettings:
    """Which provider implementation is used for each capability."""

    image: str = "comfyui"
    threed: str = "comfyui_hunyuan3d"
    renderer: str = "blender"
    image_processing: str = "builtin"


@dataclass
class ModelRoleSettings:
    """Maps logical model roles to model file names installed in ComfyUI.

    Workflows reference roles such as ``image.checkpoint``; the actual file is
    configurable here so the app is never hard-wired to one model.
    """

    roles: dict[str, str] = field(default_factory=dict)


@dataclass
class UISettings:
    last_project: str = ""
    recent_projects: list[str] = field(default_factory=list)
    window_geometry: str = ""


@dataclass
class AppSettings:
    version: int = SETTINGS_VERSION
    setup_completed: bool = False
    use_mock_providers: bool = False
    log_level: str = "INFO"
    comfyui: ComfyUISettings = field(default_factory=ComfyUISettings)
    blender: BlenderSettings = field(default_factory=BlenderSettings)
    paths: PathSettings = field(default_factory=PathSettings)
    gpu: GPUSettings = field(default_factory=GPUSettings)
    providers: ProviderSettings = field(default_factory=ProviderSettings)
    models: ModelRoleSettings = field(default_factory=ModelRoleSettings)
    ui: UISettings = field(default_factory=UISettings)
    _unknown: dict[str, Any] = field(default_factory=dict, repr=False)

    # ------------------------------------------------------------------ helpers
    def projects_dir(self) -> Path:
        return Path(self.paths.projects_dir) if self.paths.projects_dir else paths.default_projects_dir()

    def add_recent_project(self, project_dir: str | Path, limit: int = 10) -> None:
        p = str(Path(project_dir))
        recent = [r for r in self.ui.recent_projects if r != p]
        self.ui.recent_projects = [p, *recent][:limit]
        self.ui.last_project = p

    def validate(self) -> list[str]:
        """Return a list of human-readable problems (empty = valid)."""
        problems: list[str] = []
        if not (1 <= int(self.comfyui.port) <= 65535):
            problems.append(f"ComfyUI port {self.comfyui.port} is not a valid port number (1-65535).")
        if self.comfyui.install_type not in COMFYUI_INSTALL_TYPES:
            problems.append(f"Unknown ComfyUI install type '{self.comfyui.install_type}'.")
        if self.gpu.vram_profile not in VRAM_PROFILES:
            problems.append(f"Unknown VRAM profile '{self.gpu.vram_profile}'.")
        if self.log_level.upper() not in LOG_LEVELS:
            problems.append(f"Unknown log level '{self.log_level}'.")
        if not self.comfyui.host.strip():
            problems.append("ComfyUI host must not be empty.")
        return problems

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        unknown = data.pop("_unknown", {})
        merged = dict(unknown)
        merged.update(data)
        return merged


def _from_dict(cls: type, data: dict[str, Any]) -> Any:
    """Build a dataclass from a dict, ignoring wrong types and unknown keys."""
    if not isinstance(data, dict):
        return cls()
    instance = cls()
    known = {f.name: f for f in fields(cls)}
    for key, value in data.items():
        if key not in known or key == "_unknown":
            if hasattr(instance, "_unknown"):
                instance._unknown[key] = value
            continue
        current = getattr(instance, key)
        if is_dataclass(current):
            setattr(instance, key, _from_dict(type(current), value))
        elif isinstance(current, bool):
            if isinstance(value, bool):
                setattr(instance, key, value)
        elif isinstance(current, int):
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                setattr(instance, key, int(value))
            elif isinstance(value, str) and value.strip().isdigit():
                setattr(instance, key, int(value))
        elif isinstance(current, str):
            if isinstance(value, str):
                setattr(instance, key, value)
        elif isinstance(current, list):
            if isinstance(value, list):
                setattr(instance, key, [str(v) for v in value])
        elif isinstance(current, dict):
            if isinstance(value, dict):
                setattr(instance, key, {str(k): str(v) for k, v in value.items()})
        else:  # pragma: no cover - no other field types today
            setattr(instance, key, value)
    return instance


def settings_path() -> Path:
    return paths.app_data_dir() / SETTINGS_FILE


def load_settings(path: Path | None = None) -> AppSettings:
    """Load settings; returns defaults if the file does not exist.

    A corrupt settings file is backed up (``settings.json.corrupt``) and
    defaults are used, so the application can always start.
    """
    path = path or settings_path()
    if not path.exists():
        return AppSettings()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        backup = path.with_suffix(path.suffix + ".corrupt")
        try:
            path.replace(backup)
        except OSError:
            pass
        log.error("Settings file %s is unreadable (%s); using defaults. Backup: %s", path, exc, backup)
        return AppSettings()
    settings = _from_dict(AppSettings, data)
    return settings


def save_settings(settings: AppSettings, path: Path | None = None) -> Path:
    """Atomically write settings to disk."""
    path = path or settings_path()
    problems = settings.validate()
    if problems:
        raise ConfigError("Settings are invalid and were not saved.", hint="Fix the listed problems.", details="\n".join(problems))
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(path, settings.to_dict())
    return path


def write_json_atomic(path: Path, data: Any) -> None:
    """Write JSON via a temp file + rename so a crash never leaves a half-written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name, suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default
