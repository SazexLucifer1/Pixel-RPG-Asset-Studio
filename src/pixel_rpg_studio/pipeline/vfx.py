"""VFX: deterministic procedural frame effects (+ import of external frames)."""

from __future__ import annotations

from pathlib import Path

from PIL import Image

from pixel_rpg_studio.imaging.pixel import quantize_to_palette
from pixel_rpg_studio.imaging.vfx import VFXParams, render_vfx
from pixel_rpg_studio.pipeline.common import PipelineContext, build_asset_sheet, palette_for, palette_rgb
from pixel_rpg_studio.project.asset import ROLE_FINAL, AnimationInfo, Asset, GenerationRecord
from pixel_rpg_studio.spritesheet.builder import slice_sheet


def run_vfx(ctx: PipelineContext, asset: Asset, params: VFXParams, snap_to_palette: bool = False) -> list[Path]:
    frames = render_vfx(params)
    return _store_frames(ctx, asset, frames, params.fps, params.loop, snap_to_palette,
                         GenerationRecord(stage="vfx", provider="procedural", seed=params.seed, params=params.to_dict(), workflow=params.preset))


def import_vfx_frames(ctx: PipelineContext, asset: Asset, files: list[Path], fps: int = 12, loop: bool = True,
                      sheet_frame: tuple[int, int] | None = None) -> list[Path]:
    """Import frames (several PNGs, or one sheet sliced by ``sheet_frame``)."""
    frames: list[Image.Image] = []
    if sheet_frame and len(files) == 1:
        frames = slice_sheet(Image.open(files[0]), *sheet_frame)
    else:
        frames = [Image.open(f).convert("RGBA") for f in files]
    size = frames[0].size
    frames = [f if f.size == size else f.resize(size, Image.NEAREST) for f in frames]
    return _store_frames(ctx, asset, frames, fps, loop, False,
                         GenerationRecord(stage="vfx_import", provider="import", references=[{"path": str(f), "sha256": ""} for f in files]))


def _store_frames(ctx, asset, frames, fps, loop, snap, record) -> list[Path]:
    pal = palette_rgb(palette_for(ctx.project, asset)) if snap else None
    out_dir = asset.path("animations", "effect", "s", "final")
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("frame_*.png"):
        old.unlink()
    paths = []
    for i, f in enumerate(frames):
        if pal:
            f = quantize_to_palette(f, pal)
        p = out_dir / f"frame_{i:03d}.png"
        f.save(p)
        paths.append(p)
    record.outputs = [asset.rel(p) for p in paths]
    asset.add_generation(record)
    asset.meta.animations["effect"] = AnimationInfo("effect", len(paths), int(fps), bool(loop), directions=["s"],
                                                    final_frames={"s": [asset.rel(p) for p in paths]})
    asset.set_output(ROLE_FINAL, paths[0])
    build_asset_sheet(asset, frames[0].size)
    asset.meta.status = "review"
    asset.save()
    return paths
