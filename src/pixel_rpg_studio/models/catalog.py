"""Model Manager: which AI models are installed, missing, where, and under which license.

Truth source order:
1. a running ComfyUI's ``/object_info`` (lists every model file it can load,
   including ``extra_model_paths.yaml`` locations)
2. scanning the ComfyUI models folder on disk
Nothing is ever downloaded automatically.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from pixel_rpg_studio.core import paths
from pixel_rpg_studio.core.config import AppSettings

MODEL_FILE_EXTENSIONS = (".safetensors", ".ckpt", ".pt", ".pth", ".bin", ".gguf")


@dataclass
class ModelEntry:
    id: str
    name: str
    role: str
    folder: str
    filename: str
    download_page: str
    license: str
    license_url: str = ""
    license_warning: str = ""
    size_gb: float = 0.0
    min_vram_gb: float = 0.0
    notes: str = ""
    providers: list[str] = field(default_factory=list)


@dataclass
class ModelStatus:
    entry: ModelEntry
    configured_filename: str
    installed: bool | None  # None = unknown (no ComfyUI and no models folder)
    location: str = ""
    source: str = ""  # "comfyui" | "disk" | ""
    available_alternatives: list[str] = field(default_factory=list)

    @property
    def state(self) -> str:
        return {True: "Installed", False: "Missing", None: "Unknown"}[self.installed]


def load_catalog(path: Path | None = None) -> tuple[list[ModelEntry], list[dict]]:
    path = path or (paths.bundled_config_dir() / "models_catalog.json")
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    fields = set(ModelEntry.__dataclass_fields__)
    entries = [ModelEntry(**{k: v for k, v in m.items() if k in fields}) for m in data.get("models", [])]
    return entries, data.get("tools", [])


def configured_filename(entry: ModelEntry, settings: AppSettings) -> str:
    return settings.models.roles.get(entry.role) or entry.filename


def scan_folder(models_dir: Path | None, folder: str) -> list[str]:
    if not models_dir:
        return []
    root = Path(models_dir) / folder
    if not root.is_dir():
        return []
    return sorted(str(p.relative_to(root)).replace("\\", "/") for p in root.rglob("*") if p.suffix.lower() in MODEL_FILE_EXTENSIONS)


def check_models(settings: AppSettings, entries: list[ModelEntry], models_dir: Path | None = None, client=None) -> list[ModelStatus]:
    statuses = []
    for e in entries:
        wanted = configured_filename(e, settings)
        available: list[str] | None = None
        source = ""
        if client is not None:
            try:
                if e.role.startswith("threed"):
                    available = client.input_options("ImageOnlyCheckpointLoader", "ckpt_name")
                if available is None:
                    available = client.list_models(e.folder) or None
                source = "comfyui" if available is not None else ""
            except Exception:  # noqa: BLE001 - offline is fine
                available = None
        if available is None and models_dir:
            available = scan_folder(models_dir, e.folder)
            source = "disk"
        if available is None:
            statuses.append(ModelStatus(e, wanted, None))
            continue
        norm = {a.replace("\\", "/") for a in available}
        installed = wanted.replace("\\", "/") in norm
        location = str(Path(models_dir) / e.folder / wanted) if (models_dir and installed) else (f"ComfyUI: {e.folder}/{wanted}" if installed else "")
        statuses.append(ModelStatus(e, wanted, installed, location, source, sorted(norm)[:200]))
    return statuses
