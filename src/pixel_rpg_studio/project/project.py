"""Projects: one folder per game, containing style + assets + exports.

Layout::

    MyGame/
      project.json
      style/  style.json  palette.json  references/  prompts/  rules.md
      characters/ weapons/ items/ props/ environment/ buildings/
      tiles/ tilesets/ backgrounds/ vfx/ spritesheets/
      exports/
"""

from __future__ import annotations

import shutil
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any, Iterator

from pixel_rpg_studio import __version__
from pixel_rpg_studio.core.config import read_json, write_json_atomic
from pixel_rpg_studio.core.errors import ProjectError
from pixel_rpg_studio.core.paths import safe_filename
from pixel_rpg_studio.project.asset import ASSET_FILE, Asset, AssetMeta, now_iso
from pixel_rpg_studio.project.asset_types import all_asset_types, get_asset_type
from pixel_rpg_studio.project.style import Palette, StyleDefinition, load_style, save_style

PROJECT_FILE = "project.json"
PROJECT_FORMAT_VERSION = 1
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}

RULES_TEMPLATE = """# Art rules for {name}

Write down the rules every asset must follow. These notes are for humans
(and for future you); the machine-readable settings live in style.json.

- Camera: {perspective}
- Light comes from: {light}
- Outline: {outline}
- Sprite size: {w}x{h} px, tiles {tile}x{tile} px
"""


@dataclass
class GodotSettings:
    project_dir: str = ""
    export_subdir: str = "assets/generated"
    write_resources: bool = True
    write_helper_scripts: bool = True


@dataclass
class ProjectInfo:
    name: str
    id: str
    format_version: int = PROJECT_FORMAT_VERSION
    created: str = field(default_factory=now_iso)
    updated: str = field(default_factory=now_iso)
    app_version: str = __version__
    description: str = ""
    godot: GodotSettings = field(default_factory=GodotSettings)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ProjectInfo":
        if not isinstance(data, dict):
            raise ValueError("project.json must contain a JSON object")
        names = {f.name for f in fields(cls)}
        clean = {k: v for k, v in data.items() if k in names}
        godot = data.get("godot") if isinstance(data.get("godot"), dict) else {}
        gnames = {f.name for f in fields(GodotSettings)}
        clean["godot"] = GodotSettings(**{k: v for k, v in godot.items() if k in gnames})
        if "name" not in clean or "id" not in clean:
            raise ValueError("project.json requires 'name' and 'id'")
        return cls(**clean)


class Project:
    def __init__(self, root: Path, info: ProjectInfo, style: StyleDefinition, palette: Palette) -> None:
        self.root = Path(root)
        self.info = info
        self.style = style
        self.palette = palette

    # ------------------------------------------------------------ lifecycle
    @classmethod
    def create(cls, parent_dir: Path, name: str, description: str = "") -> "Project":
        if not name.strip():
            raise ProjectError("Project name must not be empty.")
        folder_name = safe_filename(name, fallback="project")
        root = Path(parent_dir) / folder_name
        if (root / PROJECT_FILE).exists():
            raise ProjectError(
                f"A project already exists at {root}.",
                hint="Open the existing project or choose another name.",
            )
        if root.exists() and any(root.iterdir()):
            raise ProjectError(
                f"The folder {root} already exists and is not empty.",
                hint="Choose another project name or folder.",
            )
        root.mkdir(parents=True, exist_ok=True)
        info = ProjectInfo(name=name.strip(), id=folder_name, description=description)
        project = cls(root, info, StyleDefinition(), Palette())
        project._ensure_structure()
        project.save()
        return project

    @classmethod
    def open(cls, root: Path) -> "Project":
        root = Path(root)
        data = read_json(root / PROJECT_FILE)
        if data is None:
            raise ProjectError(
                f"No project found at {root}.",
                hint=f"Select a folder that contains a {PROJECT_FILE} file.",
            )
        try:
            info = ProjectInfo.from_dict(data)
        except (TypeError, ValueError) as exc:
            raise ProjectError(f"The project file in {root} is damaged.", details=str(exc)) from exc
        if info.format_version > PROJECT_FORMAT_VERSION:
            raise ProjectError(
                "This project was created with a newer version of the application.",
                hint="Update Pixel RPG Asset Studio to open it.",
            )
        style, palette = load_style(root / "style")
        project = cls(root, info, style, palette)
        project._ensure_structure()
        return project

    @staticmethod
    def is_project_dir(path: Path) -> bool:
        return (Path(path) / PROJECT_FILE).is_file()

    def save(self) -> None:
        self.info.updated = now_iso()
        write_json_atomic(self.root / PROJECT_FILE, self.info.to_dict())
        self.save_style()

    def save_style(self) -> None:
        problems = self.style.validate()
        if problems:
            raise ProjectError("The style definition is invalid.", hint="Fix the style settings.", details="\n".join(problems))
        save_style(self.style_dir, self.style, self.palette)

    def _ensure_structure(self) -> None:
        for sub in ("style/references", "style/prompts", "exports"):
            (self.root / sub).mkdir(parents=True, exist_ok=True)
        for asset_type in all_asset_types():
            (self.root / asset_type.folder).mkdir(parents=True, exist_ok=True)
        rules = self.style_dir / "rules.md"
        if not rules.exists():
            s = self.style
            rules.write_text(
                RULES_TEMPLATE.format(
                    name=self.info.name, perspective=s.perspective, light=s.lighting_direction,
                    outline=s.outline, w=s.sprite_width, h=s.sprite_height, tile=s.tile_size,
                ),
                encoding="utf-8",
            )

    # ----------------------------------------------------------------- style
    @property
    def style_dir(self) -> Path:
        return self.root / "style"

    @property
    def exports_dir(self) -> Path:
        return self.root / "exports"

    def style_references(self) -> list[Path]:
        ref_dir = self.style_dir / "references"
        return sorted(p for p in ref_dir.iterdir() if p.suffix.lower() in IMAGE_EXTENSIONS) if ref_dir.exists() else []

    def add_style_reference(self, source: Path) -> Path:
        source = Path(source)
        if source.suffix.lower() not in IMAGE_EXTENSIONS:
            raise ProjectError(f"'{source.name}' is not a supported image type.", hint="Use PNG, JPG or WEBP.")
        target = _unique_path(self.style_dir / "references" / f"{safe_filename(source.stem)}{source.suffix.lower()}")
        shutil.copy2(source, target)
        return target

    def remove_style_reference(self, path: Path) -> None:
        path = Path(path)
        if path.parent.resolve() != (self.style_dir / "references").resolve():
            raise ProjectError("Refusing to delete a file outside the style references folder.")
        path.unlink(missing_ok=True)

    # ---------------------------------------------------------------- assets
    def asset_dir(self, type_key: str) -> Path:
        return self.root / get_asset_type(type_key).folder

    def create_asset(self, type_key: str, name: str, description: str = "", subtype: str = "") -> Asset:
        asset_type = get_asset_type(type_key)
        if not name.strip():
            raise ProjectError("Asset name must not be empty.")
        base = safe_filename(name, fallback=asset_type.key)
        folder = self.asset_dir(type_key)
        n = 1
        while (folder / f"{base}_{n:03d}").exists():
            n += 1
        asset_id = f"{base}_{n:03d}"
        root = folder / asset_id
        root.mkdir(parents=True)
        meta = AssetMeta(id=asset_id, type=type_key, name=name.strip(), description=description, subtype=subtype)
        asset = Asset(root, meta)
        asset.save()
        return asset

    def list_assets(self, type_key: str | None = None) -> list[Asset]:
        types = [get_asset_type(type_key)] if type_key else all_asset_types()
        assets: list[Asset] = []
        for t in types:
            folder = self.root / t.folder
            if not folder.exists():
                continue
            for child in sorted(folder.iterdir()):
                if (child / ASSET_FILE).is_file():
                    try:
                        assets.append(Asset.load(child))
                    except ProjectError:
                        continue
        return assets

    def iter_assets(self) -> Iterator[Asset]:
        yield from self.list_assets()

    def get_asset(self, type_key: str, asset_id: str) -> Asset:
        root = self.asset_dir(type_key) / asset_id
        return Asset.load(root)

    def delete_asset(self, asset: Asset) -> None:
        if not asset.root.resolve().is_relative_to(self.root.resolve()):
            raise ProjectError("Refusing to delete a folder outside the project.")
        shutil.rmtree(asset.root)

    def summary(self) -> dict[str, int]:
        return {t.key: len(self.list_assets(t.key)) for t in all_asset_types()}


def _unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    n = 2
    while True:
        candidate = path.with_name(f"{path.stem}_{n}{path.suffix}")
        if not candidate.exists():
            return candidate
        n += 1
