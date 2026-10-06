"""Asset folders and their metadata (``asset.json``).

An asset is a folder inside the project (e.g. ``characters/soldier_001``)
containing source files, intermediate results and ``asset.json``. Every
generation step is recorded as a :class:`GenerationRecord` so results are
reproducible and individual frames can be regenerated with identical settings.
"""

from __future__ import annotations

import hashlib
import time
import uuid
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pixel_rpg_studio import __version__
from pixel_rpg_studio.core.config import read_json, write_json_atomic
from pixel_rpg_studio.core.errors import ProjectError

ASSET_FILE = "asset.json"
ASSET_FORMAT_VERSION = 1

# Output roles used for the preview tabs (original -> AI -> processed -> final)
ROLE_ORIGINAL = "original"
ROLE_GENERATED = "generated"
ROLE_PROCESSED = "processed"
ROLE_FINAL = "final"
ROLE_MASTER = "master_reference"
ROLE_MODEL = "model"
ROLE_MODEL_RAW = "model_raw"
ROLE_SHEET = "sprite_sheet"
PREVIEW_ROLES = (ROLE_ORIGINAL, ROLE_GENERATED, ROLE_PROCESSED, ROLE_FINAL)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


@dataclass
class GenerationRecord:
    """Everything needed to reproduce one generation/processing step."""

    stage: str
    provider: str
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    timestamp: str = field(default_factory=now_iso)
    app_version: str = __version__
    model: str = ""
    models: dict[str, str] = field(default_factory=dict)
    workflow: str = ""
    prompt: str = ""
    negative_prompt: str = ""
    seed: int | None = None
    params: dict[str, Any] = field(default_factory=dict)
    references: list[dict[str, str]] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)
    target: str = ""  # e.g. "walk/s/3" for a single frame
    status: str = "ok"
    duration_s: float = 0.0
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "GenerationRecord":
        names = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in names})


@dataclass
class AnimationInfo:
    name: str
    frames: int
    fps: int
    loop: bool
    directions: list[str] = field(default_factory=lambda: ["s"])
    # relative paths of final frames per direction: {"s": ["animations/idle/s/final/frame_000.png", ...]}
    final_frames: dict[str, list[str]] = field(default_factory=dict)
    raw_frames: dict[str, list[str]] = field(default_factory=dict)
    sheet: str = ""
    accepted: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AnimationInfo":
        names = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in names})


@dataclass
class AssetMeta:
    id: str
    type: str
    name: str
    format_version: int = ASSET_FORMAT_VERSION
    created: str = field(default_factory=now_iso)
    updated: str = field(default_factory=now_iso)
    status: str = "draft"  # draft | in_progress | review | accepted
    description: str = ""
    subtype: str = ""
    settings: dict[str, Any] = field(default_factory=dict)
    outputs: dict[str, str] = field(default_factory=dict)  # role -> relative path
    animations: dict[str, AnimationInfo] = field(default_factory=dict)
    generations: list[GenerationRecord] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["animations"] = {k: v.to_dict() for k, v in self.animations.items()}
        data["generations"] = [g.to_dict() for g in self.generations]
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AssetMeta":
        names = {f.name for f in fields(cls)}
        clean = {k: v for k, v in data.items() if k in names}
        clean["animations"] = {k: AnimationInfo.from_dict(v) for k, v in data.get("animations", {}).items()}
        clean["generations"] = [GenerationRecord.from_dict(g) for g in data.get("generations", [])]
        return cls(**clean)


class Asset:
    """A single asset folder."""

    def __init__(self, root: Path, meta: AssetMeta) -> None:
        self.root = Path(root)
        self.meta = meta

    # ----------------------------------------------------------- persistence
    @classmethod
    def load(cls, root: Path) -> "Asset":
        meta_path = Path(root) / ASSET_FILE
        data = read_json(meta_path)
        if data is None:
            raise ProjectError(f"Asset metadata missing: {meta_path}", hint="The folder is not a valid asset.")
        try:
            meta = AssetMeta.from_dict(data)
        except TypeError as exc:
            raise ProjectError(f"Asset metadata is invalid: {meta_path}", details=str(exc)) from exc
        return cls(Path(root), meta)

    def save(self) -> None:
        self.meta.updated = now_iso()
        write_json_atomic(self.root / ASSET_FILE, self.meta.to_dict())

    # --------------------------------------------------------------- helpers
    @property
    def id(self) -> str:
        return self.meta.id

    @property
    def name(self) -> str:
        return self.meta.name

    @property
    def type(self) -> str:
        return self.meta.type

    def path(self, *parts: str) -> Path:
        return self.root.joinpath(*parts)

    def rel(self, path: Path) -> str:
        return Path(path).resolve().relative_to(self.root.resolve()).as_posix()

    def output_path(self, role: str) -> Path | None:
        rel = self.meta.outputs.get(role)
        if not rel:
            return None
        p = self.root / rel
        return p if p.exists() else None

    def set_output(self, role: str, path: Path) -> None:
        self.meta.outputs[role] = self.rel(path)

    def add_generation(self, record: GenerationRecord) -> GenerationRecord:
        self.meta.generations.append(record)
        return record

    def last_generation(self, stage: str, target: str | None = None) -> GenerationRecord | None:
        for record in reversed(self.meta.generations):
            if record.stage == stage and (target is None or record.target == target) and record.status == "ok":
                return record
        return None

    def reference_entry(self, path: Path) -> dict[str, str]:
        path = Path(path)
        try:
            shown = self.rel(path)
        except ValueError:
            shown = str(path)
        return {"path": shown, "sha256": sha256_file(path) if path.exists() else ""}

    def versioned_path(self, folder: str, stem: str, suffix: str = ".png") -> Path:
        """A new, never-overwriting file path: ``folder/stem_<timestamp>.png``.

        Originals are preserved across regenerations; the metadata points at
        the current version.
        """
        directory = self.root / folder
        directory.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d_%H%M%S")
        candidate = directory / f"{stem}_{stamp}{suffix}"
        n = 1
        while candidate.exists():
            candidate = directory / f"{stem}_{stamp}_{n}{suffix}"
            n += 1
        return candidate
