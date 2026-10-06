"""3D → pixel-art pipeline for weapons, items, props, environment objects
and buildings (characters use the 2D reference pipeline in ``character.py``).

Stages (each is resumable and recorded in asset.json):

    concept image ─► 3D model (AI image-to-3D, or imported) ─► Blender prepare
          (cleanup, scale, colour projection)
    ─► render views (fixed ortho camera, toon shading) ─► pixel processing
    ─► final views ─► Godot export
"""

from __future__ import annotations

import shutil
import time
from pathlib import Path
from typing import Any

from PIL import Image

from pixel_rpg_studio.core.errors import GenerationError, StudioError
from pixel_rpg_studio.export.godot import VIEW_PREFIX
from pixel_rpg_studio.imaging import pixel
from pixel_rpg_studio.pipeline.common import (
    PipelineContext,
    Progress,
    palette_for,
    projection_texture,
    resolve_seed,
    save_palette,
    threed_input_image,
)
from pixel_rpg_studio.project.asset import ROLE_FINAL, ROLE_GENERATED, ROLE_MODEL, ROLE_MODEL_RAW, ROLE_ORIGINAL, Asset, GenerationRecord
from pixel_rpg_studio.project.asset_types import DIRECTIONS, get_asset_type
from pixel_rpg_studio.providers.base import AnimationSpec, PrepareRequest, RenderRequest, ThreeDRequest

MODEL_EXTENSIONS = (".glb", ".gltf", ".obj", ".fbx", ".stl", ".ply")
STATIC = "static"


def default_settings(asset: Asset, style) -> dict[str, Any]:
    t = get_asset_type(asset.type)
    s: dict[str, Any] = {
        "directions": list(t.default_directions),
        "sprite_width": style.sprite_width if t.key != "building" else style.sprite_width * 2,
        "sprite_height": style.sprite_height if t.key != "building" else style.sprite_height * 2,
        "facing_correction_deg": 0.0,
        "texture_mode": "project",
    }
    return s


def settings(asset: Asset, style) -> dict[str, Any]:
    merged = default_settings(asset, style)
    merged.update(asset.meta.settings)
    asset.meta.settings = merged
    return merged


# ------------------------------------------------------------------ model
def generate_model(ctx: PipelineContext, asset: Asset, progress: Progress, seed: int | None = None,
                   params: dict[str, Any] | None = None) -> Path:
    source = asset.output_path(ROLE_GENERATED) or asset.output_path(ROLE_ORIGINAL)
    if source is None:
        raise GenerationError("A concept image is needed before a 3D model can be generated.",
                              hint="Generate a concept or import an image first.")
    seed = resolve_seed(seed)
    model_dir = asset.path("model")
    input_img = threed_input_image(source, model_dir / "threed_input.png")
    started = time.time()
    progress.progress(None, f"Generating 3D model ({ctx.providers.threed.label})")
    result = ctx.providers.threed.generate(
        ThreeDRequest(image=input_img, seed=seed, asset_kind=asset.type, params=params or {}),
        model_dir / "_tmp", progress=progress.progress, cancelled=lambda: progress.cancelled,
    )
    dest = asset.versioned_path("model", "raw", result.mesh_path.suffix)
    shutil.move(str(result.mesh_path), dest)
    shutil.rmtree(model_dir / "_tmp", ignore_errors=True)
    asset.add_generation(GenerationRecord(stage="model_3d", provider=result.provider, model=result.model, workflow=result.workflow,
                                          seed=seed, params=result.params, references=[asset.reference_entry(input_img)],
                                          outputs=[asset.rel(dest)], duration_s=round(time.time() - started, 2)))
    asset.set_output(ROLE_MODEL_RAW, dest)
    _invalidate_scene(asset)
    asset.save()
    return dest


def import_model(asset: Asset, source: Path) -> Path:
    source = Path(source)
    if source.suffix.lower() not in MODEL_EXTENSIONS:
        raise StudioError(f"'{source.name}' is not a supported 3D format.", hint="Use GLB, GLTF, OBJ, FBX, STL or PLY.")
    dest = asset.versioned_path("model", "imported", source.suffix.lower())
    shutil.copy2(source, dest)
    if source.suffix.lower() == ".gltf":  # copy side files (buffers/textures)
        for side in source.parent.iterdir():
            if side.suffix.lower() in (".bin", ".png", ".jpg", ".jpeg"):
                shutil.copy2(side, dest.parent / side.name)
    asset.add_generation(GenerationRecord(stage="model_import", provider="import", outputs=[asset.rel(dest)],
                                          references=[{"path": str(source), "sha256": ""}]))
    asset.set_output(ROLE_MODEL_RAW, dest)
    _invalidate_scene(asset)
    asset.save()
    return dest


def _invalidate_scene(asset: Asset) -> None:
    asset.meta.settings.pop("blend", None)
    asset.meta.settings.pop("camera_framing", None)
    asset.meta.outputs.pop("render_reference", None)


def prepare_model(ctx: PipelineContext, asset: Asset, progress: Progress) -> dict[str, Any]:
    s = settings(asset, ctx.style)
    raw = asset.output_path(ROLE_MODEL_RAW)
    if raw is None:
        raise GenerationError("There is no 3D model yet.", hint="Generate or import a 3D model first.")
    concept = asset.output_path(ROLE_GENERATED) or asset.output_path(ROLE_ORIGINAL)
    texture = projection_texture(concept, asset.path("model", "projection_texture.png")) if (concept and s["texture_mode"] == "project") else None
    blend = asset.path("model", f"{asset.id}.blend")
    clean = asset.path("model", f"{asset.id}_clean.glb")
    style = ctx.style
    req = PrepareRequest(
        model_path=raw, blend_path=blend, export_model_path=clean, texture_image=texture,
        shading_style=style.shading_style, shading_bands=style.shading_bands, light=style.light(),
        facing_correction_deg=float(s.get("facing_correction_deg", 0.0)),
        scale_mode="max", texture_mode=s["texture_mode"], concept_image=concept,
    )
    started = time.time()
    result = ctx.providers.renderer.prepare(req, progress=progress.progress, cancelled=lambda: progress.cancelled)
    s["blend"] = asset.rel(result.blend_path)
    s.pop("camera_framing", None)
    s["prepare_report"] = {k: v for k, v in result.report.items() if k in ("mesh", "warnings", "texture_mode", "height")}
    asset.add_generation(GenerationRecord(stage="prepare", provider=result.provider, params={"request": _jsonable(req.__dict__)},
                                          outputs=[s["blend"]], duration_s=round(time.time() - started, 2),
                                          notes="; ".join(result.report.get("warnings", []))))
    if clean.exists():
        asset.set_output(ROLE_MODEL, clean)
    asset.save()
    return result.report


def _jsonable(d: dict) -> dict:
    return {k: (str(v) if isinstance(v, Path) else v) for k, v in d.items()}


# ----------------------------------------------------------------- render
def render_frames(ctx: PipelineContext, asset: Asset, progress: Progress, directions: list[str] | None = None) -> dict:
    """Render (and pixel-process) one view per direction. Returns {direction: final path}."""
    s = settings(asset, ctx.style)
    blend_rel = s.get("blend")
    if not blend_rel or not (asset.root / blend_rel).exists():
        raise GenerationError("The 3D model has not been prepared yet.", hint="Run 'Prepare model' first.")
    style = ctx.style
    dirs = directions or s["directions"]
    framing = s.get("camera_framing") or {}
    scale = style.render_scale
    W, H = int(s["sprite_width"]), int(s["sprite_height"])
    elevation = float(s["elevation_deg"]) if s.get("elevation_deg") is not None else style.elevation()
    yaw_offset = float(s["camera_yaw_deg"]) if s.get("camera_yaw_deg") is not None else style.perspective_yaw()
    key = {"elevation": elevation, "yaw": yaw_offset, "w": W, "h": H, "scale": scale}
    reuse = framing.get("key") == key
    req = RenderRequest(
        blend_path=asset.root / blend_rel, output_dir=asset.path("render"), width=W * scale, height=H * scale,
        directions=[(d, DIRECTIONS[d].yaw_deg) for d in dirs], animations=[AnimationSpec(STATIC, 1, True)],
        elevation_deg=elevation, yaw_offset_deg=yaw_offset, light=style.light(),
        ortho_scale=framing.get("ortho_scale") if reuse else None, target=framing.get("target") if reuse else None,
    )
    started = time.time()
    render_progress = progress.sub(0.0, 0.8) if hasattr(progress, "sub") else progress
    result = ctx.providers.renderer.render(req, progress=render_progress.progress, cancelled=lambda: progress.cancelled)
    s["camera_framing"] = {"key": key, "ortho_scale": result.ortho_scale, "target": result.target}
    asset.add_generation(GenerationRecord(
        stage="render", provider=result.provider,
        params={"camera": {"elevation": elevation, "yaw_offset": yaw_offset, "ortho_scale": result.ortho_scale,
                           "target": result.target}, "light": list(style.light()), "resolution": [req.width, req.height],
                "directions": dirs},
        outputs=[], duration_s=round(time.time() - started, 2), notes="; ".join(result.report.get("warnings", [])),
    ))

    # Lock a per-asset palette on the first render (when no project palette is enforced)
    if not style.use_palette and not asset.path("palette.json").exists():
        sample = [pixel.downscale(Image.open(p), W, H, style.downscale_method) for frames in result.frames.values() for p in frames.values()]
        save_palette(asset, pixel.extract_palette(sample[:64], style.max_colors), f"{asset.name} palette")

    steps = pixel.steps_from_style(style, palette_for(ctx.project, asset), W, H, source="render")
    finals: dict[str, Path] = {}
    total = sum(len(v) for v in result.frames.values())
    done = 0
    for (_anim, direction), frames in result.frames.items():
        for idx, raw_src in sorted(frames.items()):
            raw = asset.path("views", direction, "raw.png")
            final = asset.path("views", direction, "final.png")
            raw.parent.mkdir(parents=True, exist_ok=True)
            if Path(raw_src).resolve() != raw.resolve():
                shutil.copyfile(raw_src, raw)
            ctx.providers.processing.process(raw, steps, final)
            finals[direction] = final
            asset.meta.outputs[VIEW_PREFIX + direction] = asset.rel(final)
            done += 1
            progress.progress(0.8 + 0.2 * done / max(1, total), f"Pixel processing {done}/{total}")
    if dirs and dirs[0] in finals:
        asset.set_output(ROLE_FINAL, finals[dirs[0]])
    asset.add_generation(GenerationRecord(stage="pixel_process", provider=ctx.providers.processing.id, params={"steps": steps},
                                          target="render_views", outputs=[asset.rel(p) for p in finals.values()]))
    shutil.rmtree(asset.path("render"), ignore_errors=True)
    asset.meta.status = "review"
    asset.save()
    return finals
