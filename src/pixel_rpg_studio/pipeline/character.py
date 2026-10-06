"""Character Studio pipeline: the first complete end-to-end vertical slice.

concept → master reference → 3D model → Blender prepare (cleanup, colours,
auto rig) → animate + render all directions → pixel processing → sprite
sheet → (Godot export via export.godot).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from pixel_rpg_studio.pipeline import three_d
from pixel_rpg_studio.pipeline.common import PipelineContext, Progress, build_asset_sheet, generate_concept, import_image
from pixel_rpg_studio.project.asset import ROLE_GENERATED, Asset


@dataclass
class CharacterRunOptions:
    seed: int | None = None
    model_seed: int | None = None
    reference_image: Path | None = None
    concept_image: Path | None = None  # skip AI concept: use this image
    model_file: Path | None = None  # skip AI 3D: import this model
    reuse_concept: bool = False
    reuse_model: bool = False
    animations: list[str] = field(default_factory=lambda: ["idle", "walk"])
    extra_prompt: str = ""


def run_character(ctx: PipelineContext, asset: Asset, progress: Progress, opts: CharacterRunOptions) -> dict:
    """Run every stage. Stages already done are reused when requested."""
    three_d.settings(asset, ctx.style)
    # 1. concept
    p = progress.sub(0.0, 0.25)
    if opts.concept_image:
        import_image(asset, opts.concept_image)
    elif not (opts.reuse_concept and asset.output_path(ROLE_GENERATED)):
        prompt = None
        if opts.extra_prompt:
            from pixel_rpg_studio.pipeline.common import build_prompt

            prompt, _ = build_prompt(ctx.project, asset.type, asset.meta.description, asset.meta.subtype, opts.extra_prompt)
        generate_concept(ctx, asset, p, seed=opts.seed, reference=opts.reference_image, prompt=prompt)
    progress.check_cancelled()
    three_d.make_master_reference(ctx, asset)
    _free(ctx)
    # 2. 3D model
    p = progress.sub(0.25, 0.55)
    if opts.model_file:
        three_d.import_model(asset, opts.model_file)
    elif not (opts.reuse_model and asset.meta.outputs.get("model_raw")):
        three_d.generate_model(ctx, asset, p, seed=opts.model_seed)
    _free(ctx)
    progress.check_cancelled()
    # 3. Blender prepare (cleanup, colours, rig)
    report = three_d.prepare_model(ctx, asset, progress.sub(0.55, 0.65))
    progress.check_cancelled()
    # 4. animation + render + pixel processing
    for name in opts.animations:
        three_d.set_animation(asset, name)
    three_d.render_frames(ctx, asset, progress.sub(0.65, 0.97), animations=opts.animations)
    # 5. sprite sheet
    s = asset.meta.settings
    build_asset_sheet(asset, (int(s["sprite_width"]), int(s["sprite_height"])))
    progress.progress(1.0, "Character finished")
    return report


def _free(ctx: PipelineContext) -> None:
    """Unload models between stages (keeps VRAM free for the next model)."""
    try:
        ctx.providers.image.free_memory()
    except Exception:  # noqa: BLE001 - best effort
        pass
