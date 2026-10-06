"""Weapons, items, props, environment objects and buildings.

Same shared 3D pipeline as characters, without rigging: the model is rendered
from several fixed views. A pure 2D path (concept → pixel) is available for
objects where 3D adds nothing.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pixel_rpg_studio.pipeline import three_d
from pixel_rpg_studio.pipeline.common import PipelineContext, Progress, accept_final, generate_concept, import_image, process_image
from pixel_rpg_studio.project.asset import ROLE_GENERATED, Asset


@dataclass
class ObjectRunOptions:
    seed: int | None = None
    model_seed: int | None = None
    reference_image: Path | None = None
    concept_image: Path | None = None
    model_file: Path | None = None
    reuse_concept: bool = False
    reuse_model: bool = False
    use_3d: bool = True  # False = 2D only (concept → pixel)
    directions: list[str] | None = None


def run_object(ctx: PipelineContext, asset: Asset, progress: Progress, opts: ObjectRunOptions) -> dict:
    s = three_d.settings(asset, ctx.style)
    if opts.directions:
        s["directions"] = list(opts.directions)
    s.pop("animations", None)
    p = progress.sub(0.0, 0.3)
    if opts.concept_image:
        import_image(asset, opts.concept_image)
    elif not (opts.reuse_concept and asset.output_path(ROLE_GENERATED)):
        generate_concept(ctx, asset, p, seed=opts.seed, reference=opts.reference_image)
    progress.check_cancelled()
    concept = asset.output_path(ROLE_GENERATED)
    processed = process_image(ctx, asset, concept, width=int(s["sprite_width"]), height=int(s["sprite_height"]),
                              source_kind="concept", anchor="bottom")
    if not opts.use_3d:
        accept_final(asset, processed)
        progress.progress(1.0, "Finished (2D)")
        return {}
    try:
        ctx.providers.image.free_memory()
    except Exception:  # noqa: BLE001
        pass
    if opts.model_file:
        three_d.import_model(asset, opts.model_file)
    elif not (opts.reuse_model and asset.meta.outputs.get("model_raw")):
        three_d.generate_model(ctx, asset, progress.sub(0.3, 0.65), seed=opts.model_seed)
    report = three_d.prepare_model(ctx, asset, progress.sub(0.65, 0.75))
    three_d.render_frames(ctx, asset, progress.sub(0.75, 1.0))
    progress.progress(1.0, "Finished")
    return report
