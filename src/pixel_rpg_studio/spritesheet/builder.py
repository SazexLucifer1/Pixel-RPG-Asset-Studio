"""Sprite sheet construction and metadata.

A sheet is a grid of equally sized frames. Animations occupy consecutive
cells (each animation/direction optionally starting on a new row), and the
metadata records exactly where every frame is, plus fps/loop, so the Godot
exporter (or any engine) can rebuild the animations.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Sequence

from PIL import Image

from pixel_rpg_studio.core.config import write_json_atomic

METADATA_VERSION = 1


@dataclass
class SheetLayout:
    frame_width: int
    frame_height: int
    columns: int = 0  # 0 = automatic
    padding: int = 0  # empty pixels around each frame (inside its cell)
    spacing: int = 0  # pixels between cells
    margin: int = 0  # pixels around the whole sheet
    row_per_animation: bool = True

    @property
    def cell_width(self) -> int:
        return self.frame_width + 2 * self.padding

    @property
    def cell_height(self) -> int:
        return self.frame_height + 2 * self.padding


@dataclass
class AnimationSpec:
    name: str
    frames: list[Image.Image]
    fps: float = 10.0
    loop: bool = True
    direction: str = ""  # optional ("s", "w"...)

    @property
    def full_name(self) -> str:
        return f"{self.name}_{self.direction}" if self.direction else self.name


@dataclass
class FrameRect:
    x: int
    y: int
    w: int
    h: int


@dataclass
class SheetAnimationMeta:
    name: str
    base_name: str
    direction: str
    fps: float
    loop: bool
    frames: list[FrameRect] = field(default_factory=list)


@dataclass
class SheetMetadata:
    image: str
    width: int
    height: int
    frame_width: int
    frame_height: int
    columns: int
    rows: int
    padding: int
    spacing: int
    margin: int
    animations: list[SheetAnimationMeta] = field(default_factory=list)
    version: int = METADATA_VERSION
    generator: str = "Pixel RPG Asset Studio"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "SheetMetadata":
        anims = [
            SheetAnimationMeta(
                name=a["name"], base_name=a.get("base_name", a["name"]), direction=a.get("direction", ""),
                fps=a["fps"], loop=a["loop"], frames=[FrameRect(**f) for f in a["frames"]],
            )
            for a in data.get("animations", [])
        ]
        fields_ = {k: v for k, v in data.items() if k != "animations"}
        return cls(animations=anims, **fields_)


def _check_frames(animations: Sequence[AnimationSpec], layout: SheetLayout) -> None:
    if not animations or not any(a.frames for a in animations):
        raise ValueError("A sprite sheet needs at least one frame.")
    for anim in animations:
        for i, f in enumerate(anim.frames):
            if f.size != (layout.frame_width, layout.frame_height):
                raise ValueError(
                    f"Frame {i} of '{anim.full_name}' is {f.width}x{f.height}, expected "
                    f"{layout.frame_width}x{layout.frame_height}. All frames must have the same size."
                )


def plan_grid(animations: Sequence[AnimationSpec], layout: SheetLayout) -> tuple[int, int, list[list[tuple[int, int]]]]:
    """Return (columns, rows, cells per animation) without drawing anything."""
    counts = [len(a.frames) for a in animations]
    if layout.columns > 0:
        columns = layout.columns
    elif layout.row_per_animation:
        columns = max(counts)
    else:
        columns = max(1, math.ceil(math.sqrt(sum(counts))))
    cells: list[list[tuple[int, int]]] = []
    row, col = 0, 0
    for n in counts:
        if layout.row_per_animation and col != 0:
            row, col = row + 1, 0
        anim_cells = []
        for _ in range(n):
            if col >= columns:
                row, col = row + 1, 0
            anim_cells.append((col, row))
            col += 1
        cells.append(anim_cells)
    rows = row + (1 if col > 0 else 0)
    return columns, max(1, rows), cells


def build_sheet(animations: Sequence[AnimationSpec], layout: SheetLayout, image_name: str = "sheet.png") -> tuple[Image.Image, SheetMetadata]:
    _check_frames(animations, layout)
    columns, rows, cells = plan_grid(animations, layout)
    cw, ch = layout.cell_width, layout.cell_height
    width = 2 * layout.margin + columns * cw + (columns - 1) * layout.spacing
    height = 2 * layout.margin + rows * ch + (rows - 1) * layout.spacing
    sheet = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    meta = SheetMetadata(
        image=image_name, width=width, height=height, frame_width=layout.frame_width, frame_height=layout.frame_height,
        columns=columns, rows=rows, padding=layout.padding, spacing=layout.spacing, margin=layout.margin,
    )
    for anim, anim_cells in zip(animations, cells):
        am = SheetAnimationMeta(anim.full_name, anim.name, anim.direction, anim.fps, anim.loop)
        for frame, (col, row) in zip(anim.frames, anim_cells):
            x = layout.margin + col * (cw + layout.spacing) + layout.padding
            y = layout.margin + row * (ch + layout.spacing) + layout.padding
            sheet.paste(frame.convert("RGBA"), (x, y))
            am.frames.append(FrameRect(x, y, layout.frame_width, layout.frame_height))
        meta.animations.append(am)
    return sheet, meta


def save_sheet(sheet: Image.Image, meta: SheetMetadata, png_path: Path) -> tuple[Path, Path]:
    png_path = Path(png_path)
    png_path.parent.mkdir(parents=True, exist_ok=True)
    meta.image = png_path.name
    sheet.save(png_path, optimize=True)
    json_path = png_path.with_suffix(".json")
    write_json_atomic(json_path, meta.to_dict())
    return png_path, json_path


def load_metadata(json_path: Path) -> SheetMetadata:
    return SheetMetadata.from_dict(json.loads(Path(json_path).read_text(encoding="utf-8")))


def slice_sheet(sheet: Image.Image, frame_width: int, frame_height: int, columns: int = 0, rows: int = 0,
                margin: int = 0, spacing: int = 0, skip_empty: bool = True) -> list[Image.Image]:
    """Cut an imported sprite sheet into frames (row-major)."""
    sheet = sheet.convert("RGBA")
    if frame_width <= 0 or frame_height <= 0:
        raise ValueError("Frame size must be positive.")
    columns = columns or (sheet.width - 2 * margin + spacing) // (frame_width + spacing)
    rows = rows or (sheet.height - 2 * margin + spacing) // (frame_height + spacing)
    frames = []
    for r in range(rows):
        for c in range(columns):
            x = margin + c * (frame_width + spacing)
            y = margin + r * (frame_height + spacing)
            frame = sheet.crop((x, y, x + frame_width, y + frame_height))
            if skip_empty and frame.getbbox() is None:
                continue
            frames.append(frame)
    return frames


def animated_preview(frames: Sequence[Image.Image], path: Path, fps: float, scale: int = 4, loop: bool = True) -> Path:
    """Write an animated GIF preview (nearest-neighbour upscaled)."""
    if not frames:
        raise ValueError("No frames to preview.")
    big = [f.convert("RGBA").resize((f.width * scale, f.height * scale), Image.NEAREST) for f in frames]
    duration = int(1000 / max(1, fps))
    big[0].save(path, save_all=True, append_images=big[1:], duration=duration, loop=0 if loop else 1, disposal=2)
    return Path(path)
