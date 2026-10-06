"""Sprite Sheet Builder asset type: arrange arbitrary frames into a sheet."""

from __future__ import annotations

from pathlib import Path

from PIL import Image

from pixel_rpg_studio.export.godot import ROLE_SHEET_META
from pixel_rpg_studio.project.asset import ROLE_SHEET, AnimationInfo, Asset, GenerationRecord
from pixel_rpg_studio.spritesheet.builder import AnimationSpec, SheetLayout, build_sheet, save_sheet


def build_from_files(asset: Asset, groups: dict[str, list[Path]], frame_size: tuple[int, int], fps: float = 10, loop: bool = True,
                     columns: int = 0, padding: int = 0, spacing: int = 0, fit: str = "pad") -> tuple[Path, Path]:
    """``groups``: animation name → list of image files (in order)."""
    anims = []
    fw, fh = frame_size
    for name, files in groups.items():
        frames = []
        for f in files:
            img = Image.open(f).convert("RGBA")
            if img.size != (fw, fh):
                if fit == "scale":
                    img = img.resize((fw, fh), Image.NEAREST)
                else:  # pad/crop centred, never resample pixel art
                    canvas = Image.new("RGBA", (fw, fh), (0, 0, 0, 0))
                    canvas.paste(img, ((fw - img.width) // 2, fh - img.height if img.height <= fh else (fh - img.height) // 2))
                    img = canvas
            frames.append(img)
        anims.append(AnimationSpec(name, frames, fps, loop))
        stored = []
        frame_dir = asset.path("animations", name, "s", "final")
        frame_dir.mkdir(parents=True, exist_ok=True)
        for i, fr in enumerate(frames):
            p = frame_dir / f"frame_{i:03d}.png"
            fr.save(p)
            stored.append(asset.rel(p))
        asset.meta.animations[name] = AnimationInfo(name, len(frames), int(fps), loop, ["s"], final_frames={"s": stored})
    sheet, meta = build_sheet(anims, SheetLayout(fw, fh, columns=columns, padding=padding, spacing=spacing))
    png, js = save_sheet(sheet, meta, asset.path("exports", f"{asset.id}_sheet.png"))
    asset.set_output(ROLE_SHEET, png)
    asset.set_output(ROLE_SHEET_META, js)
    asset.add_generation(GenerationRecord(stage="spritesheet", provider="builtin",
                                          params={"frame_size": list(frame_size), "columns": columns, "padding": padding, "spacing": spacing,
                                                  "fps": fps, "loop": loop}, outputs=[asset.rel(png), asset.rel(js)]))
    asset.save()
    return png, js
