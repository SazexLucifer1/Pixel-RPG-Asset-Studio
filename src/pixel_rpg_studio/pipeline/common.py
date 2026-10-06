"""Shared pipeline building blocks used by every asset type.

Asset-type pipelines are thin compositions of these functions, so there is no
duplicated generation/processing/metadata code between characters, props,
buildings, backgrounds, tiles and VFX.
"""

from __future__ import annotations

import logging
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from pixel_rpg_studio.comfyui.workflows import random_seed
from pixel_rpg_studio.core.config import write_json_atomic
from pixel_rpg_studio.core.errors import GenerationError
from pixel_rpg_studio.core.jobs import JobContext, SubProgress
from pixel_rpg_studio.export.godot import ROLE_SHEET_META
from pixel_rpg_studio.imaging import pixel
from pixel_rpg_studio.project.asset import (
    ROLE_FINAL,
    ROLE_GENERATED,
    ROLE_ORIGINAL,
    ROLE_PROCESSED,
    ROLE_SHEET,
    Asset,
    GenerationRecord,
)
from pixel_rpg_studio.project.asset_types import get_asset_type
from pixel_rpg_studio.project.project import Project
from pixel_rpg_studio.project.style import PROPORTIONS, Palette, hex_to_rgb
from pixel_rpg_studio.providers.base import ImageRequest
from pixel_rpg_studio.providers.registry import ProviderSet
from pixel_rpg_studio.spritesheet.builder import AnimationSpec as SheetAnim
from pixel_rpg_studio.spritesheet.builder import SheetLayout, build_sheet, save_sheet

log = logging.getLogger(__name__)

Progress = JobContext | SubProgress


@dataclass
class PipelineContext:
    project: Project
    providers: ProviderSet

    @property
    def style(self):
        return self.project.style


# ----------------------------------------------------------------- prompts
def build_prompt(project: Project, type_key: str, description: str, subtype: str = "", extra: str = "") -> tuple[str, str]:
    """Combine the style definition with the asset description."""
    style = project.style
    asset_type = get_asset_type(type_key)
    body = asset_type.prompt_template.format(description=description.strip() or asset_type.label.lower(), subtype=subtype or "")
    parts = [style.positive_prompt, body, style.perspective_prompt() if type_key not in ("tile", "tileset", "character") else ""]
    if type_key == "character":
        parts.append(PROPORTIONS.get(style.proportions, ""))
    custom = project.style_dir / "prompts" / f"{type_key}.txt"
    if custom.is_file():
        parts.append(custom.read_text(encoding="utf-8").strip())
    if extra:
        parts.append(extra)
    prompt = ", ".join(p.strip(" ,") for p in parts if p and p.strip(" ,"))
    return prompt, style.negative_prompt


def resolve_seed(seed: int | None) -> int:
    return random_seed() if seed is None or seed < 0 else int(seed)


# ---------------------------------------------------------------- concept
def generate_concept(
    ctx: PipelineContext,
    asset: Asset,
    progress: Progress,
    *,
    prompt: str | None = None,
    negative: str | None = None,
    seed: int | None = None,
    workflow: str | None = None,
    reference: Path | None = None,
    width: int | None = None,
    height: int | None = None,
    params: dict[str, Any] | None = None,
    stage: str = "concept",
    folder: str = "concept",
) -> Path:
    """Generate an AI image for the asset and record it. Returns the image path."""
    asset_type = get_asset_type(asset.type)
    if prompt is None:
        prompt, default_negative = build_prompt(ctx.project, asset.type, asset.meta.description, asset.meta.subtype)
        negative = default_negative if negative is None else negative
    seed = resolve_seed(seed)
    wf = workflow or asset_type.concept_workflow
    if reference is not None and not wf.endswith("_reference"):
        candidate = f"{wf}_reference"
        try:
            from pixel_rpg_studio.comfyui.workflows import WorkflowLibrary

            WorkflowLibrary().find_path(candidate)
            wf = candidate
        except Exception:  # noqa: BLE001 - no reference variant; plain generation
            log.info("No reference workflow for %s; generating without reference", wf)
            reference = None
    if reference is not None and not _inside(reference, asset.root):
        copied = asset.path("references", Path(reference).name)
        copied.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(reference, copied)
        reference = copied
    req = ImageRequest(workflow=wf, prompt=prompt, negative_prompt=negative or "", seed=seed, width=width, height=height,
                       reference_image=reference, params=params or {})
    progress.progress(None, f"Generating {stage} image (seed {seed})")
    tmp_dir = asset.path("_tmp")
    started = time.time()
    result = ctx.providers.image.generate(req, tmp_dir, progress=progress.progress, cancelled=lambda: progress.cancelled)
    if not result.images:
        raise GenerationError("The image provider returned no image.")
    dest = asset.versioned_path(folder, stage)
    shutil.move(str(result.images[0]), dest)
    shutil.rmtree(tmp_dir, ignore_errors=True)
    refs = [asset.reference_entry(reference)] if reference else []
    asset.add_generation(
        GenerationRecord(
            stage=stage, provider=result.provider, model=result.model, models=result.models, workflow=result.workflow,
            prompt=prompt, negative_prompt=negative or "", seed=seed, params={**result.params, "width": width, "height": height},
            references=refs, outputs=[asset.rel(dest)], duration_s=round(time.time() - started, 2),
        )
    )
    asset.set_output(ROLE_GENERATED, dest)
    if reference is not None:
        asset.set_output(ROLE_ORIGINAL, reference)
    asset.meta.status = "review"
    asset.save()
    return dest


def import_image(asset: Asset, source: Path, role: str = ROLE_GENERATED, folder: str = "concept", stage: str = "import") -> Path:
    """Use a user-provided image instead of AI generation (always available)."""
    source = Path(source)
    img = Image.open(source)
    img.load()
    dest = asset.versioned_path(folder, stage)
    img.convert("RGBA").save(dest)
    asset.add_generation(GenerationRecord(stage=stage, provider="import", references=[{"path": str(source), "sha256": ""}],
                                          outputs=[asset.rel(dest)], notes=f"Imported from {source.name}"))
    asset.set_output(ROLE_ORIGINAL, dest)
    asset.set_output(role, dest)
    asset.save()
    return dest


def _inside(path: Path, root: Path) -> bool:
    try:
        Path(path).resolve().relative_to(Path(root).resolve())
        return True
    except ValueError:
        return False


# -------------------------------------------------------------- processing
def palette_for(project: Project, asset: Asset | None = None) -> list[str] | None:
    """The palette used for quantisation: asset palette > project palette (if enabled)."""
    if asset is not None:
        p = asset.path("palette.json")
        if p.is_file():
            from pixel_rpg_studio.core.config import read_json

            return Palette.from_dict(read_json(p)).colors
    return project.palette.colors if project.style.use_palette else None


def process_image(
    ctx: PipelineContext,
    asset: Asset,
    source: Path,
    *,
    width: int,
    height: int,
    source_kind: str = "concept",
    anchor: str = "bottom",
    steps: list[dict[str, Any]] | None = None,
    role: str | None = ROLE_PROCESSED,
    out_path: Path | None = None,
    target: str = "",
    record: bool = True,
) -> Path:
    """Run the deterministic pixel pipeline and record the exact steps."""
    if steps is None:
        steps = pixel.steps_from_style(ctx.style, palette_for(ctx.project, asset), width, height, source=source_kind, anchor=anchor)
    out_path = out_path or asset.versioned_path("processed", "processed")
    started = time.time()
    ctx.providers.processing.process(Path(source), steps, out_path)
    if record:
        asset.add_generation(GenerationRecord(stage="pixel_process", provider=ctx.providers.processing.id,
                                              params={"steps": steps}, references=[asset.reference_entry(source)],
                                              outputs=[asset.rel(out_path)], target=target, duration_s=round(time.time() - started, 3)))
    if role:
        asset.set_output(role, out_path)
    return out_path


def accept_final(asset: Asset, path: Path, role: str = ROLE_FINAL) -> None:
    asset.set_output(role, path)
    asset.meta.status = "accepted"
    asset.save()


def projection_texture(source: Path, dest: Path, bleed: int = 4) -> Path:
    """Concept image -> background removed, cropped to the subject, colours bled
    into transparent pixels (prevents dark seams when projected onto a mesh)."""
    img = pixel.crop_to_content(pixel.alpha_threshold(pixel.remove_background(Image.open(source).convert("RGBA"))))
    arr = np.array(img).astype(np.int32)
    alpha = arr[..., 3] > 0
    for _ in range(bleed):
        if alpha.all():
            break
        pad = np.pad(arr, ((1, 1), (1, 1), (0, 0)))
        pad_a = np.pad(alpha, 1)
        acc = np.zeros(arr.shape[:2] + (3,), dtype=np.int64)
        cnt = np.zeros(arr.shape[:2], dtype=np.int64)
        for dy, dx in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            sl = (slice(1 + dy, 1 + dy + arr.shape[0]), slice(1 + dx, 1 + dx + arr.shape[1]))
            m = pad_a[sl]
            acc += pad[sl][..., :3] * m[..., None]
            cnt += m
        grow = (~alpha) & (cnt > 0)
        arr[grow, :3] = acc[grow] // cnt[grow][:, None]
        alpha = alpha | grow
    arr[..., 3] = 255
    out = Image.fromarray(arr.astype(np.uint8), "RGBA")
    dest.parent.mkdir(parents=True, exist_ok=True)
    out.save(dest)
    return dest


def threed_input_image(source: Path, dest: Path, size: int = 1024, margin: float = 0.1) -> Path:
    """Subject on a white square canvas with margin - what image-to-3D models expect."""
    img = pixel.crop_to_content(pixel.remove_background(Image.open(source).convert("RGBA")))
    side = int(max(img.width, img.height) / (1 - 2 * margin))
    canvas = Image.new("RGBA", (side, side), (255, 255, 255, 255))
    canvas.alpha_composite(img, ((side - img.width) // 2, (side - img.height) // 2))
    canvas = canvas.convert("RGB").resize((size, size), Image.LANCZOS)
    dest.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(dest)
    return dest


# ------------------------------------------------------------ sprite sheets
def build_asset_sheet(asset: Asset, frame_size: tuple[int, int], padding: int = 0, columns: int = 0,
                      animations: list[str] | None = None) -> tuple[Path, Path]:
    """Combined atlas of all (accepted or selected) animations of an asset."""
    anims: list[SheetAnim] = []
    for name, info in asset.meta.animations.items():
        if animations is not None and name not in animations:
            continue
        for direction in info.directions:
            rels = info.final_frames.get(direction) or []
            if not rels:
                continue
            frames = [Image.open(asset.root / r).convert("RGBA") for r in rels]
            anims.append(SheetAnim(name, frames, info.fps, info.loop, direction))
    if not anims:
        raise GenerationError("There are no finished animation frames to put on a sprite sheet yet.")
    layout = SheetLayout(frame_size[0], frame_size[1], columns=columns, padding=padding)
    sheet, meta = build_sheet(anims, layout)
    png, js = save_sheet(sheet, meta, asset.path("exports", f"{asset.id}_sheet.png"))
    asset.set_output(ROLE_SHEET, png)
    asset.set_output(ROLE_SHEET_META, js)
    # per-animation sheets as well (idle.png, walk.png...)
    for a in anims:
        single, smeta = build_sheet([a], layout)
        p, _ = save_sheet(single, smeta, asset.path("exports", "animations", f"{a.full_name}.png"))
        info = asset.meta.animations.get(a.name)
        if info is not None and (a.direction in ("", "s") or not info.sheet):
            info.sheet = asset.rel(p)
    asset.save()
    return png, js


def save_palette(asset: Asset, colors: list[tuple[int, int, int]], name: str) -> Path:
    path = asset.path("palette.json")
    write_json_atomic(path, Palette.from_rgb(name, colors).to_dict())
    return path


def palette_rgb(colors: list[str] | None) -> list[tuple[int, int, int]] | None:
    return [hex_to_rgb(c) for c in colors] if colors else None
