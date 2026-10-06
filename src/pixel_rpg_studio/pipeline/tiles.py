"""Tiles and structured tilesets.

A tileset is built from two terrain tiles (e.g. grass over dirt): both are made
seamless deterministically, then a 16-tile corner-transition set plus random
variants is composed and exported as a Godot TileSet with a terrain set.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from pixel_rpg_studio.core.config import write_json_atomic
from pixel_rpg_studio.export.godot import ROLE_TILESET_ATLAS, ROLE_TILESET_META
from pixel_rpg_studio.imaging import tiles as T
from pixel_rpg_studio.pipeline.common import (
    PipelineContext,
    Progress,
    build_prompt,
    generate_concept,
    import_image,
    palette_for,
    palette_rgb,
    resolve_seed,
)
from pixel_rpg_studio.project.asset import ROLE_FINAL, ROLE_PROCESSED, Asset, GenerationRecord

TERRAIN_PRESETS = ("grass", "dirt", "stone", "road", "water", "sand", "snow", "wall", "floor", "cliff")


@dataclass
class TileOptions:
    terrain: str = "grass"
    seed: int | None = None
    import_image: Path | None = None
    seamless: bool = True


def make_tile(ctx: PipelineContext, asset: Asset, progress: Progress, opts: TileOptions, slot: str = "base") -> Path:
    """One seamless tile. ``slot`` names the terrain role inside a tileset."""
    size = ctx.style.tile_size
    if opts.import_image:
        src = import_image(asset, opts.import_image, folder="sources", stage=f"import_{slot}")
    else:
        prompt, negative = build_prompt(ctx.project, asset.type, opts.terrain)
        src = generate_concept(ctx, asset, progress, prompt=prompt, negative=negative, seed=opts.seed, stage=f"texture_{slot}", folder="sources")
    tile = T.process_tile(Image.open(src), size, palette_rgb(palette_for(ctx.project, asset)), seamless=opts.seamless,
                          method=ctx.style.downscale_method)
    out = asset.path("tiles", f"{slot}.png")
    out.parent.mkdir(parents=True, exist_ok=True)
    tile.save(out)
    score = T.seam_score(tile)
    asset.add_generation(GenerationRecord(stage="tile_process", provider="builtin", target=slot,
                                          params={"tile_size": size, "seamless": opts.seamless, "seam_score": score},
                                          references=[asset.reference_entry(src)], outputs=[asset.rel(out)]))
    asset.meta.settings.setdefault("terrains", {})[slot] = opts.terrain
    if slot == "base":
        asset.set_output(ROLE_PROCESSED, out)
        asset.set_output(ROLE_FINAL, out)
    asset.save()
    return out


def build_tileset(ctx: PipelineContext, asset: Asset, seed: int | None = None, variants: int = 4, roughness: float = 0.35) -> Path:
    base, overlay = asset.path("tiles", "base.png"), asset.path("tiles", "overlay.png")
    if not (base.exists() and overlay.exists()):
        from pixel_rpg_studio.core.errors import GenerationError

        raise GenerationError("A tileset needs a base and an overlay terrain tile.", hint="Generate or import both terrains first.")
    size = ctx.style.tile_size
    seed = resolve_seed(seed)
    base_img, over_img = Image.open(base), Image.open(overlay)
    atlas, meta = T.build_corner_terrain_set(base_img, over_img, size, seed=seed, roughness=roughness)
    # Extra row(s): variants of each pure terrain (Godot picks randomly among matching tiles).
    rows = 4
    extra = []
    for terrain_index, src in ((0, base_img), (1, over_img)):
        for v in T.tile_variations(src, variants, seed + terrain_index):
            extra.append((terrain_index, v))
    cols = 4
    extra_rows = (len(extra) + cols - 1) // cols
    full = Image.new("RGBA", (cols * size, (rows + extra_rows) * size), (0, 0, 0, 0))
    full.paste(atlas, (0, 0))
    for n, (terrain_index, img) in enumerate(extra):
        x, y = n % cols, rows + n // cols
        full.paste(img, (x * size, y * size))
        bit = terrain_index
        meta.append({"atlas": [x, y], "index": 15 if bit else 0, "terrain": bit,
                     "corners": {"top_left": bit, "top_right": bit, "bottom_left": bit, "bottom_right": bit}})
    out = asset.path("exports", f"{asset.id}_tileset.png")
    out.parent.mkdir(parents=True, exist_ok=True)
    full.save(out)
    names = asset.meta.settings.get("terrains", {})
    tmeta = {"tile_size": size, "columns": cols, "rows": rows + extra_rows, "terrain_tiles": meta,
             "terrain_names": [names.get("base", "Base").title(), names.get("overlay", "Overlay").title()], "seed": seed}
    meta_path = asset.path("exports", f"{asset.id}_tileset.json")
    write_json_atomic(meta_path, tmeta)
    preview = T.preview_terrain_map(atlas, meta[:16], size, T.demo_corner_map(seed=seed))
    preview_path = asset.path("exports", "preview_map.png")
    preview.save(preview_path)
    asset.set_output(ROLE_TILESET_ATLAS, out)
    asset.set_output(ROLE_TILESET_META, meta_path)
    asset.set_output("tileset_preview", preview_path)
    asset.add_generation(GenerationRecord(stage="tileset", provider="builtin", seed=seed,
                                          params={"roughness": roughness, "variants": variants, "tile_size": size},
                                          outputs=[asset.rel(out), asset.rel(meta_path)]))
    asset.meta.status = "review"
    asset.save()
    return out
