"""Backgrounds: a primarily 2D pipeline (no 3D step).

Full image or parallax layers (sky / far / mid / near). Each layer can be
generated, imported, re-generated with its own seed, and is pixel-processed
deterministically to the target resolution.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pixel_rpg_studio.export.godot import LAYER_PREFIX
from pixel_rpg_studio.imaging import pixel
from pixel_rpg_studio.pipeline.common import PipelineContext, Progress, build_prompt, generate_concept, import_image, palette_for, process_image
from pixel_rpg_studio.project.asset import ROLE_FINAL, ROLE_GENERATED, Asset

LAYERS = ("sky", "far", "mid", "near")
LAYER_PROMPTS = {
    "sky": "sky only, clouds, no ground",
    "far": "distant mountains silhouette, plain sky background",
    "mid": "midground hills and trees, plain white background",
    "near": "foreground grass and bushes, plain white background",
}


@dataclass
class BackgroundOptions:
    width: int = 480
    height: int = 270
    layered: bool = False
    layers: tuple[str, ...] = LAYERS
    seed: int | None = None
    reference_image: Path | None = None
    import_image: Path | None = None


def background_steps(ctx: PipelineContext, asset: Asset, width: int, height: int, transparent: bool) -> list[dict]:
    style = ctx.style
    steps: list[dict] = []
    if transparent:
        steps += [{"op": "remove_background", "tolerance": 28, "force": True}, {"op": "alpha_threshold"}]
    steps.append({"op": "downscale", "width": width, "height": height, "method": style.downscale_method, "alpha_coverage": 0.5})
    if abs(style.contrast - 1) > 1e-6 or abs(style.saturation - 1) > 1e-6:
        steps.append({"op": "adjust", "contrast": style.contrast, "saturation": style.saturation})
    pal = palette_for(ctx.project, asset)
    steps.append({"op": "quantize", "palette": pal, "dither": style.dithering} if pal else {"op": "quantize", "max_colors": style.max_colors})
    return steps


def run_background(ctx: PipelineContext, asset: Asset, progress: Progress, opts: BackgroundOptions) -> list[Path]:
    asset.meta.settings.update({"width": opts.width, "height": opts.height, "layered": opts.layered})
    outputs = []
    if opts.import_image:
        src = import_image(asset, opts.import_image)
        out = process_image(ctx, asset, src, width=opts.width, height=opts.height,
                            steps=background_steps(ctx, asset, opts.width, opts.height, False))
        asset.set_output(ROLE_FINAL, out)
        asset.save()
        return [out]
    if not opts.layered:
        src = generate_concept(ctx, asset, progress, seed=opts.seed, reference=opts.reference_image)
        out = process_image(ctx, asset, src, width=opts.width, height=opts.height,
                            steps=background_steps(ctx, asset, opts.width, opts.height, False))
        asset.set_output(ROLE_FINAL, out)
        asset.save()
        return [out]
    for n, layer in enumerate(opts.layers):
        outputs.append(generate_layer(ctx, asset, progress.sub(n / len(opts.layers), (n + 1) / len(opts.layers)), layer, opts.width, opts.height,
                                      None if opts.seed is None else opts.seed + n))
    composite_layers(asset)
    return outputs


def generate_layer(ctx: PipelineContext, asset: Asset, progress: Progress, layer: str, width: int, height: int, seed: int | None = None) -> Path:
    prompt, negative = build_prompt(ctx.project, asset.type, asset.meta.description, extra=LAYER_PROMPTS.get(layer, layer))
    src = generate_concept(ctx, asset, progress, prompt=prompt, negative=negative, seed=seed, stage=f"layer_{layer}", folder="layers")
    out = process_image(ctx, asset, src, width=width, height=height, target=f"layer:{layer}", role=None,
                        out_path=asset.versioned_path("layers", f"{layer}_final"),
                        steps=background_steps(ctx, asset, width, height, transparent=(layer != "sky")))
    asset.meta.outputs[LAYER_PREFIX + layer] = asset.rel(out)
    asset.save()
    return out


def composite_layers(asset: Asset) -> Path | None:
    from PIL import Image

    layers = [asset.output_path(LAYER_PREFIX + l) for l in LAYERS]
    layers = [p for p in layers if p]
    if not layers:
        return None
    base = Image.open(layers[0]).convert("RGBA")
    for p in layers[1:]:
        base.alpha_composite(Image.open(p).convert("RGBA").resize(base.size, Image.NEAREST))
    out = asset.versioned_path("processed", "composite")
    base.save(out)
    asset.set_output(ROLE_FINAL, out)
    if not asset.output_path(ROLE_GENERATED):
        asset.set_output(ROLE_GENERATED, layers[-1])
    asset.save()
    return out


__all__ = ["BackgroundOptions", "run_background", "generate_layer", "composite_layers", "LAYERS", "pixel"]
