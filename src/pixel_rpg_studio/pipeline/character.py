"""Character pipeline (2D, reference-driven).

    Reference image → Character identity → Pose (OpenPose) → AI frame
    (IP-Adapter identity + ControlNet pose) → pixel processing → sprite
    sheets → Godot export

The reference image IS the character: it is cleaned once and fed to the
image model as real image conditioning (IP-Adapter via CLIP Vision) for
every single frame; the pose comes from an OpenPose skeleton (ControlNet).
Frames are generated one at a time (no batches - fits 8 GB GPUs) on a fixed
canvas with a fixed skeleton scale, then pixel-processed with the palette
locked from the reference, so all frames of all animations line up.

Folder layout of a character asset::

    characters/knight_001/
      asset.json
      reference/   reference.png (as imported)  reference_clean.png (AI input)  equipment.png
      identity/    identity.json  identity_pixel.png  palette.json
      animations/<animation>/<direction>/
                   poses.json  pose/frame_000.png  raw/frame_000.png  final/frame_000.png
      generated/   every AI output ever produced (never overwritten)
      export/      knight_idle.png + .json, knight_walk.png + .json, combined sheet
"""

from __future__ import annotations

import hashlib
import shutil
import time
from pathlib import Path
from typing import Any

from PIL import Image

from pixel_rpg_studio.comfyui.workflows import random_seed
from pixel_rpg_studio.core.config import read_json, write_json_atomic
from pixel_rpg_studio.core.errors import GenerationError, StudioError
from pixel_rpg_studio.core.paths import safe_filename
from pixel_rpg_studio.export.godot import ANIM_SHEET_PREFIX, ROLE_SHEET_META
from pixel_rpg_studio.imaging import pixel
from pixel_rpg_studio.imaging.openpose import draw_pose
from pixel_rpg_studio.pipeline.common import PipelineContext, Progress
from pixel_rpg_studio.poses import presets
from pixel_rpg_studio.poses.skeleton import Pose2D, PoseView
from pixel_rpg_studio.project.asset import (
    ROLE_FINAL,
    ROLE_GENERATED,
    ROLE_MASTER,
    ROLE_ORIGINAL,
    ROLE_PROCESSED,
    ROLE_SHEET,
    AnimationInfo,
    Asset,
    GenerationRecord,
)
from pixel_rpg_studio.project.style import Palette
from pixel_rpg_studio.providers.base import ImageRequest
from pixel_rpg_studio.spritesheet.builder import AnimationSpec as SheetAnim
from pixel_rpg_studio.spritesheet.builder import SheetLayout, build_sheet, save_sheet

WORKFLOW = "character_frame"
IDENTITY_VERSION = 1
SPRITE_SIZES = (16, 32, 48, 64, 128)
DEFAULT_SPRITE_SIZE = 48
DEFAULT_REFERENCE_STRENGTH = 0.8
DEFAULT_POSE_STRENGTH = 0.85
DEFAULT_EQUIPMENT_STRENGTH = 0.35
# Front, Back, Left, Right. Diagonals (sw, se, nw, ne) work the same way -
# the skeleton projects to any yaw - and can be enabled here later.
DIRECTIONS = ("s", "n", "w", "e")
DIRECTION_LABELS = {"s": "Front", "n": "Back", "w": "Left", "e": "Right",
                    "sw": "Front-left", "se": "Front-right", "nw": "Back-left", "ne": "Back-right"}
VIEW_PROMPTS = {
    "s": "front view, facing the viewer",
    "n": "back view, seen from behind, facing away",
    "w": "side view, profile, facing left",
    "e": "side view, profile, facing right",
    "sw": "three-quarter front view, facing left",
    "se": "three-quarter front view, facing right",
    "nw": "three-quarter back view, facing left",
    "ne": "three-quarter back view, facing right",
}
CHARACTER_NEGATIVE = (
    "multiple characters, crowd, sprite sheet, character sheet, multiple views, turnaround, reference sheet, "
    "background scenery, landscape, floor, ground shadow, cast shadow, drop shadow, text, letters, watermark, signature, "
    "logo, frame, border, blurry, anti-aliasing, soft edges, gradient, photorealistic, photo, 3d render, "
    "cropped, out of frame, cut off feet, extra limbs, extra arms, deformed"
)


# ================================================================= identity
def identity_path(asset: Asset) -> Path:
    return asset.path("identity", "identity.json")


def load_identity(asset: Asset) -> dict[str, Any] | None:
    data = read_json(identity_path(asset))
    return data if isinstance(data, dict) else None


def require_identity(asset: Asset) -> dict[str, Any]:
    ident = load_identity(asset)
    if ident is None:
        raise StudioError("This character has no identity yet.",
                          hint="Choose a reference image and press 'Create Character' first.", code="no_identity")
    return ident


def save_identity(asset: Asset, identity: dict[str, Any]) -> None:
    write_json_atomic(identity_path(asset), identity)


def _sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def set_reference(asset: Asset, source: Path) -> Path:
    """Store the reference image (the character's identity source)."""
    source = Path(source)
    try:
        img = Image.open(source)
        img.load()
    except (OSError, ValueError) as exc:
        raise StudioError(f"'{source.name}' could not be opened as an image.", hint="Use PNG, JPG or WEBP.") from exc
    dest = asset.path("reference", "reference.png")
    dest.parent.mkdir(parents=True, exist_ok=True)
    img.convert("RGBA").save(dest)
    asset.add_generation(GenerationRecord(stage="reference", provider="import", references=[{"path": str(source), "sha256": _sha256(source)}],
                                          outputs=[asset.rel(dest)], notes=f"Reference image {source.name}"))
    asset.set_output(ROLE_ORIGINAL, dest)
    asset.save()
    return dest


def set_equipment_reference(asset: Asset, source: Path | None) -> Path | None:
    """Optional extra reference (weapon/shield close-up) for equipment consistency."""
    dest = asset.path("reference", "equipment.png")
    if source is None:
        dest.unlink(missing_ok=True)
        return None
    dest.parent.mkdir(parents=True, exist_ok=True)
    clean_reference_image(Path(source), dest)
    return dest


def clean_reference_image(source: Path, dest: Path, size: int = 1024, margin: float = 0.08) -> Path:
    """Background removed, cropped to the character and centred on a white
    square - the input the CLIP Vision encoder sees (it centre-crops squares)."""
    img = pixel.crop_to_content(pixel.alpha_threshold(pixel.remove_background(Image.open(source).convert("RGBA"))))
    side = int(max(img.width, img.height) / (1 - 2 * margin))
    canvas = Image.new("RGBA", (side, side), (255, 255, 255, 255))
    canvas.alpha_composite(img, ((side - img.width) // 2, (side - img.height) // 2))
    canvas = canvas.convert("RGB").resize((size, size), Image.LANCZOS)
    dest.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(dest)
    return dest


def generation_size(profile: str) -> int:
    """SDXL canvas per frame: 768 px on <=8 GB GPUs, 1024 px otherwise."""
    return 768 if profile in ("low", "cpu") else 1024


def create_identity(ctx: PipelineContext, asset: Asset, progress: Progress | None = None, *, description: str = "",
                    sprite_size: int = DEFAULT_SPRITE_SIZE, reference_strength: float = DEFAULT_REFERENCE_STRENGTH,
                    pose_strength: float = DEFAULT_POSE_STRENGTH, seed: int | None = None,
                    equipment_strength: float = DEFAULT_EQUIPMENT_STRENGTH) -> dict[str, Any]:
    """Reference → identity: cleaned reference, pixel preview, locked palette, identity.json."""
    ref = asset.output_path(ROLE_ORIGINAL)
    if ref is None or not ref.exists():
        raise StudioError("Choose a reference image first.", hint="Press 'Reference Image' and select your character.")
    if sprite_size not in SPRITE_SIZES:
        raise StudioError(f"Unsupported sprite size {sprite_size}.", hint=f"Use one of {', '.join(map(str, SPRITE_SIZES))}.")
    if progress is not None:
        progress.progress(0.1, "Cleaning the reference image")
    clean = clean_reference_image(ref, asset.path("reference", "reference_clean.png"))
    style = ctx.style
    # Pixel version of the reference = identity preview and palette source.
    preview = asset.path("identity", "identity_pixel.png")
    preview.parent.mkdir(parents=True, exist_ok=True)
    cut = pixel.crop_to_content(pixel.alpha_threshold(pixel.remove_background(Image.open(ref).convert("RGBA"))))
    pal_src = pixel.fit_canvas(cut, sprite_size * 2, sprite_size * 2, anchor="bottom", margin=0, method=style.downscale_method)
    palette = pixel.extract_palette([pal_src], max(4, int(style.max_colors)))
    hexes = ["#%02x%02x%02x" % c for c in palette]
    write_json_atomic(asset.path("identity", "palette.json"), Palette.from_rgb(f"{asset.name} (from reference)", palette).to_dict())
    steps = [
        {"op": "remove_background", "tolerance": 32},
        {"op": "alpha_threshold", "threshold": 128},
        {"op": "crop_to_content"},
        {"op": "fit_canvas", "width": sprite_size, "height": sprite_size, "anchor": "bottom", "margin": 1, "method": style.downscale_method},
        {"op": "quantize", "palette": hexes},
        {"op": "remove_orphans"},
    ]
    if style.outline != "none":
        steps.append({"op": "outline", "style": style.outline, "palette": hexes})
    ctx.providers.processing.process(ref, steps, preview)
    old = load_identity(asset) or {}
    identity = {
        "version": IDENTITY_VERSION,
        "name": asset.name,
        "description": description.strip(),
        "reference": {"path": asset.rel(ref), "sha256": _sha256(ref)},
        "reference_clean": asset.rel(clean),
        "equipment_reference": asset.rel(asset.path("reference", "equipment.png")) if asset.path("reference", "equipment.png").exists() else "",
        "pixel_preview": asset.rel(preview),
        "palette": hexes,
        "sprite_size": int(sprite_size),
        "reference_strength": float(reference_strength),
        "pose_strength": float(pose_strength),
        "equipment_strength": float(equipment_strength),
        "seed": int(seed) if seed is not None and seed >= 0 else int(old.get("seed", random_seed())),
        "generation_size": generation_size(ctx.providers.profile),
        "pose_view": PoseView(proportions=style.proportions).to_dict(),
        "workflow": WORKFLOW,
        "background_tolerance": int(old.get("background_tolerance", 32)),
    }
    save_identity(asset, identity)
    asset.meta.description = description.strip()
    asset.set_output(ROLE_MASTER, preview)
    asset.set_output(ROLE_PROCESSED, preview)
    asset.add_generation(GenerationRecord(stage="identity", provider=ctx.providers.processing.id, params={"steps": steps, "identity": identity},
                                          references=[asset.reference_entry(ref)], outputs=[asset.rel(clean), asset.rel(preview)]))
    asset.save()
    if progress is not None:
        progress.progress(1.0, "Character identity created")
    return identity


def update_identity(asset: Asset, **changes: Any) -> dict[str, Any]:
    """Change identity settings (strengths, size, description, seed)."""
    identity = require_identity(asset)
    for key, value in changes.items():
        if value is None:
            continue
        if key == "sprite_size" and int(value) not in SPRITE_SIZES:
            raise StudioError(f"Unsupported sprite size {value}.")
        identity[key] = value
    save_identity(asset, identity)
    if "description" in changes and changes["description"] is not None:
        asset.meta.description = str(changes["description"])
        asset.save()
    return identity


# =================================================================== poses
def pose_view(identity: dict[str, Any]) -> PoseView:
    return PoseView.from_dict(identity.get("pose_view"))


def animation_settings(asset: Asset) -> dict[str, dict[str, Any]]:
    return asset.meta.settings.setdefault("animations", {})


def _poses_file(asset: Asset, animation: str, direction: str) -> Path:
    return asset.path("animations", animation, direction, "poses.json")


def ensure_animation(asset: Asset, animation: str, frames: int | None = None, fps: int | None = None, loop: bool | None = None,
                     directions: list[str] | None = None, reset_poses: bool = False) -> AnimationInfo:
    """Create/resize an animation; template poses are filled in for every direction.

    Changing the frame count rebuilds the poses from the template (edited
    poses of that animation are replaced, because frame phases change).
    """
    identity = require_identity(asset)
    tpl = presets.template(animation)
    cfg = animation_settings(asset).setdefault(animation, {"frames": tpl.frames, "fps": tpl.fps, "loop": tpl.loop})
    resized = frames is not None and int(frames) != int(cfg["frames"])
    if frames is not None:
        cfg["frames"] = max(1, min(32, int(frames)))
    if fps is not None:
        cfg["fps"] = max(1, min(60, int(fps)))
    if loop is not None:
        cfg["loop"] = bool(loop)
    info = asset.meta.animations.get(animation) or AnimationInfo(animation, cfg["frames"], cfg["fps"], cfg["loop"], directions=[])
    info.frames, info.fps, info.loop = cfg["frames"], cfg["fps"], cfg["loop"]
    for d in directions or []:
        if d not in info.directions:
            info.directions.append(d)
    view = pose_view(identity)
    for d in info.directions:
        path = _poses_file(asset, animation, d)
        data = read_json(path) if path.exists() else None
        if data is None or reset_poses or resized or len(data.get("frames", [])) != info.frames:
            poses = presets.animation_poses(animation, info.frames, d, view, loop=info.loop)
            old = (data or {}).get("frames", [])
            entries = [{"pose": p.to_dict(), "seed": (old[i].get("seed") if i < len(old) else None)} for i, p in enumerate(poses)]
            write_json_atomic(path, {"animation": animation, "direction": d, "frames": entries})
    asset.meta.animations[animation] = info
    _refresh_frames(asset, animation)
    asset.save()
    return info


def _read_poses(asset: Asset, animation: str, direction: str) -> dict[str, Any]:
    data = read_json(_poses_file(asset, animation, direction))
    if not data:
        raise StudioError(f"Animation '{animation}' has no poses for direction '{direction}'.",
                          hint="Select the animation and direction, then press 'Generate Animation'.")
    return data


def get_pose(asset: Asset, animation: str, direction: str, frame: int) -> Pose2D:
    frames = _read_poses(asset, animation, direction)["frames"]
    if not 0 <= frame < len(frames):
        raise StudioError(f"Frame {frame + 1} does not exist in '{animation}'.")
    return Pose2D.from_dict(frames[frame]["pose"])


def set_pose(asset: Asset, animation: str, direction: str, frame: int, pose: Pose2D) -> None:
    data = _read_poses(asset, animation, direction)
    if not 0 <= frame < len(data["frames"]):
        raise StudioError(f"Frame {frame + 1} does not exist in '{animation}'.")
    pose.direction = direction
    data["frames"][frame]["pose"] = pose.to_dict()
    write_json_atomic(_poses_file(asset, animation, direction), data)


def apply_preset(asset: Asset, animation: str, direction: str, frame: int, preset: str, user_dir: Path | None = None) -> Pose2D:
    identity = require_identity(asset)
    pose = presets.preset_pose(preset, direction, pose_view(identity), user_dir)
    set_pose(asset, animation, direction, frame, pose)
    return pose


def pose_image(asset: Asset, animation: str, direction: str, frame: int, size: int) -> Path:
    """Draw the OpenPose control image for one frame."""
    pose = get_pose(asset, animation, direction, frame)
    path = asset.path("animations", animation, direction, "pose", f"frame_{frame:03d}.png")
    path.parent.mkdir(parents=True, exist_ok=True)
    draw_pose(pose.openpose_joints(), size, size).save(path)
    return path


# ============================================================== generation
def frame_prompt(ctx: PipelineContext, identity: dict[str, Any], animation: str, direction: str) -> tuple[str, str]:
    style = ctx.style
    tpl = presets.template(animation)
    action = tpl.prompt or animation.replace("_", " ")
    parts = [style.positive_prompt, "single character, full body", identity.get("description") or asset_name(identity),
             action, VIEW_PROMPTS.get(direction, ""), "plain white background, centered, flat colors, crisp edges"]
    prompt = ", ".join(p.strip(" ,") for p in parts if p and p.strip(" ,"))
    negative = ", ".join(p for p in (style.negative_prompt, CHARACTER_NEGATIVE) if p)
    return prompt, negative


def asset_name(identity: dict[str, Any]) -> str:
    return str(identity.get("name") or "character")


def frame_steps(identity: dict[str, Any], style) -> list[dict[str, Any]]:
    """Deterministic pixel processing of one frame on the fixed canvas.

    The whole canvas is downscaled (no cropping), so every frame keeps the
    same scale and ground line; colours snap to the reference palette."""
    size = int(identity["sprite_size"])
    steps: list[dict[str, Any]] = [
        {"op": "remove_background", "tolerance": int(identity.get("background_tolerance", 32)), "force": True},
        {"op": "alpha_threshold", "threshold": 128},
        {"op": "downscale", "width": size, "height": size, "method": style.downscale_method},
        {"op": "quantize", "palette": list(identity["palette"])},
        {"op": "remove_orphans"},
    ]
    if style.outline != "none":
        steps.append({"op": "outline", "style": style.outline, "palette": list(identity["palette"])})
    return steps


def generate_frame(ctx: PipelineContext, asset: Asset, progress: Progress, animation: str, direction: str, frame: int,
                   seed: int | None = None, overrides: dict[str, Any] | None = None) -> Path:
    """Generate exactly one frame (one image request, no batch)."""
    identity = require_identity(asset)
    size = int(identity.get("generation_size", 1024))
    clean = asset.root / identity["reference_clean"]
    if not clean.exists():
        raise StudioError("The cleaned reference image is missing.", hint="Press 'Create Character' again.")
    equipment = asset.root / identity["equipment_reference"] if identity.get("equipment_reference") else None
    if equipment is not None and not equipment.exists():
        equipment = None
    pose_png = pose_image(asset, animation, direction, frame, size)
    prompt, negative = frame_prompt(ctx, identity, animation, direction)
    seed = int(identity.get("seed", 0)) if seed is None else int(seed)
    params: dict[str, Any] = {
        "reference_strength": float(identity["reference_strength"]),
        "pose_strength": float(identity["pose_strength"]),
        "equipment_strength": float(identity.get("equipment_strength", 0.0)) if equipment is not None else 0.0,
    }
    if overrides:
        prompt = overrides.get("prompt", prompt)
        negative = overrides.get("negative_prompt", negative)
        params.update({k: v for k, v in overrides.items() if k in params})
    req = ImageRequest(workflow=identity.get("workflow", WORKFLOW), prompt=prompt, negative_prompt=negative, seed=seed,
                       width=size, height=size, params=params,
                       images={"reference_image": clean, "equipment_image": equipment or clean, "pose_image": pose_png})
    target = f"{animation}/{direction}/{frame}"
    progress.progress(None, f"Generating {animation} {DIRECTION_LABELS.get(direction, direction)} frame {frame + 1} (seed {seed})")
    started = time.time()
    tmp = asset.path("_tmp")
    result = ctx.providers.image.generate(req, tmp, progress=progress.progress, cancelled=lambda: progress.cancelled)
    if not result.images:
        raise GenerationError("The image provider returned no image.")
    generated = asset.path("generated", f"{animation}_{direction}_{frame:03d}_seed{seed}.png")
    generated.parent.mkdir(parents=True, exist_ok=True)
    if generated.exists():
        generated = asset.versioned_path("generated", generated.stem)
    shutil.move(str(result.images[0]), generated)
    shutil.rmtree(tmp, ignore_errors=True)
    raw = asset.path("animations", animation, direction, "raw", f"frame_{frame:03d}.png")
    raw.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(generated, raw)
    final = process_frame(ctx, asset, identity, animation, direction, frame)
    data = _read_poses(asset, animation, direction)
    data["frames"][frame]["seed"] = seed
    write_json_atomic(_poses_file(asset, animation, direction), data)
    asset.add_generation(GenerationRecord(
        stage="frame", provider=result.provider, model=result.model, models=result.models, workflow=result.workflow,
        prompt=prompt, negative_prompt=negative, seed=seed, target=target,
        params={**params, "resolution": [size, size], "sprite_size": identity["sprite_size"], "palette": list(identity["palette"]),
                "direction": direction, "animation": animation, "frame": frame, "provider_params": result.params},
        references=[asset.reference_entry(clean), asset.reference_entry(pose_png)] + ([asset.reference_entry(equipment)] if equipment else []),
        outputs=[asset.rel(generated), asset.rel(raw), asset.rel(final)], duration_s=round(time.time() - started, 2),
    ))
    asset.set_output(ROLE_GENERATED, generated)
    asset.set_output(ROLE_FINAL, final)
    asset.meta.status = "review"
    _refresh_frames(asset, animation)
    asset.save()
    return final


def process_frame(ctx: PipelineContext, asset: Asset, identity: dict[str, Any], animation: str, direction: str, frame: int) -> Path:
    raw = asset.path("animations", animation, direction, "raw", f"frame_{frame:03d}.png")
    final = asset.path("animations", animation, direction, "final", f"frame_{frame:03d}.png")
    final.parent.mkdir(parents=True, exist_ok=True)
    ctx.providers.processing.process(raw, frame_steps(identity, ctx.style), final)
    return final


def generate_animation(ctx: PipelineContext, asset: Asset, progress: Progress, animation: str, directions: list[str],
                       frames: int | None = None, seed: int | None = None) -> list[Path]:
    """All frames of one animation, one after the other (sequential GPU use).

    Every frame uses the same seed (the character seed unless given): the
    same noise plus the same reference keeps details stable between poses."""
    if not directions:
        raise StudioError("Choose at least one direction.")
    info = ensure_animation(asset, animation, frames=frames, directions=directions)
    total = info.frames * len(directions)
    out = []
    n = 0
    for d in directions:
        for i in range(info.frames):
            progress.check_cancelled()
            sub = progress.sub(n / total, (n + 1) / total) if hasattr(progress, "sub") else progress
            out.append(generate_frame(ctx, asset, sub, animation, d, i, seed=seed))
            n += 1
            _free(ctx)
    build_sheets(asset)
    progress.progress(1.0, f"{animation}: {total} frame(s) generated")
    return out


def regenerate_frame(ctx: PipelineContext, asset: Asset, progress: Progress, animation: str, direction: str, frame: int,
                     seed: int | None = None) -> Path:
    """Regenerate ONE frame: identical identity, reference, pose, animation,
    direction, prompt and strengths as its last generation - only the seed changes."""
    last = asset.last_generation("frame", f"{animation}/{direction}/{frame}")
    overrides = None
    if last is not None:
        overrides = {"prompt": last.prompt, "negative_prompt": last.negative_prompt,
                     **{k: last.params[k] for k in ("reference_strength", "pose_strength", "equipment_strength") if k in last.params}}
    new_seed = random_seed() if seed is None else int(seed)
    if last is not None and last.seed == new_seed:
        new_seed = (new_seed + 1) % (2**32)
    final = generate_frame(ctx, asset, progress, animation, direction, frame, seed=new_seed, overrides=overrides)
    build_sheets(asset)
    return final


def reprocess_frames(ctx: PipelineContext, asset: Asset, progress: Progress | None = None) -> int:
    """Re-run pixel processing for every generated frame (e.g. after changing the sprite size)."""
    identity = require_identity(asset)
    n = 0
    for name, info in asset.meta.animations.items():
        for d in info.directions:
            for i in range(info.frames):
                if asset.path("animations", name, d, "raw", f"frame_{i:03d}.png").exists():
                    process_frame(ctx, asset, identity, name, d, i)
                    n += 1
        _refresh_frames(asset, name)
    asset.add_generation(GenerationRecord(stage="pixel_process", provider=ctx.providers.processing.id,
                                          params={"steps": frame_steps(identity, ctx.style)}, target="all_frames", notes=f"{n} frame(s)"))
    asset.save()
    if n:
        build_sheets(asset)
    return n


def _refresh_frames(asset: Asset, animation: str) -> None:
    info = asset.meta.animations.get(animation)
    if info is None:
        return
    for d in info.directions:
        finals = [asset.path("animations", animation, d, "final", f"frame_{i:03d}.png") for i in range(info.frames)]
        raws = [asset.path("animations", animation, d, "raw", f"frame_{i:03d}.png") for i in range(info.frames)]
        info.final_frames[d] = [asset.rel(p) if p.exists() else "" for p in finals]
        info.raw_frames[d] = [asset.rel(p) if p.exists() else "" for p in raws]


def frame_seed(asset: Asset, animation: str, direction: str, frame: int) -> int | None:
    try:
        return _read_poses(asset, animation, direction)["frames"][frame].get("seed")
    except (StudioError, IndexError, KeyError):
        return None


def _free(ctx: PipelineContext) -> None:
    try:
        ctx.providers.image.free_memory()
    except Exception:  # noqa: BLE001 - best effort
        pass


# ================================================================== export
def file_base(asset: Asset) -> str:
    return safe_filename(asset.name, fallback=asset.id).lower()


def complete_animations(asset: Asset) -> dict[str, dict[str, list[Path]]]:
    """{animation: {direction: [final frame paths]}} for directions whose frames are all generated."""
    out: dict[str, dict[str, list[Path]]] = {}
    for name, info in asset.meta.animations.items():
        for d in info.directions:
            rels = info.final_frames.get(d) or []
            if len(rels) == info.frames and all(rels) and all((asset.root / r).exists() for r in rels):
                out.setdefault(name, {})[d] = [asset.root / r for r in rels]
    return out


def build_sheets(asset: Asset) -> list[Path]:
    """export/<name>_<animation>.png (+ .json): one row per direction; plus the
    combined sheet used for the Godot SpriteFrames resource."""
    identity = require_identity(asset)
    size = int(identity["sprite_size"])
    layout = SheetLayout(size, size)
    complete = complete_animations(asset)
    for role in [r for r in asset.meta.outputs if r.startswith(ANIM_SHEET_PREFIX)]:
        del asset.meta.outputs[role]
    if not complete:
        asset.save()
        return []
    written: list[Path] = []
    all_specs: list[SheetAnim] = []
    base = file_base(asset)
    for name, by_dir in complete.items():
        info = asset.meta.animations[name]
        specs = [SheetAnim(name, [Image.open(p).convert("RGBA") for p in by_dir[d]], info.fps, info.loop, d)
                 for d in DIRECTIONS + tuple(k for k in by_dir if k not in DIRECTIONS) if d in by_dir]
        sheet, meta = build_sheet(specs, layout, image_name=f"{base}_{name}.png")
        png, _ = save_sheet(sheet, meta, asset.path("export", f"{base}_{name}.png"))
        asset.set_output(ANIM_SHEET_PREFIX + name, png)
        info.sheet = asset.rel(png)
        written.append(png)
        all_specs.extend(specs)
    sheet, meta = build_sheet(all_specs, layout, image_name=f"{base}_sheet.png")
    png, js = save_sheet(sheet, meta, asset.path("export", f"{base}_sheet.png"))
    asset.set_output(ROLE_SHEET, png)
    asset.set_output(ROLE_SHEET_META, js)
    asset.save()
    return written


def export_png_frames(asset: Asset, dest_dir: Path) -> list[Path]:
    """Copy every finished frame as <name>_<animation>_<direction>_<nn>.png."""
    complete = complete_animations(asset)
    if not complete:
        raise StudioError("There are no finished frames to export yet.", hint="Generate an animation first.")
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    base = file_base(asset)
    out = []
    for name, by_dir in complete.items():
        for d, paths in by_dir.items():
            for i, p in enumerate(paths):
                target = dest_dir / f"{base}_{name}_{d}_{i:02d}.png"
                shutil.copyfile(p, target)
                out.append(target)
    return out
