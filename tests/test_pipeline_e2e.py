"""End-to-end pipelines with mock providers (no GPU / ComfyUI / Blender needed)."""

import json

from PIL import Image

from pixel_rpg_studio.core.jobs import JobState
from pixel_rpg_studio.export.godot import plan_asset_export
from pixel_rpg_studio.imaging.vfx import VFXParams
from pixel_rpg_studio.pipeline import backgrounds, sheets, three_d, tiles, vfx
from pixel_rpg_studio.pipeline.objects import ObjectRunOptions, run_object
from pixel_rpg_studio.project.asset import Asset


def ok(job):
    assert job.state == JobState.DONE, job.error.full_text() if job.error else job.state
    return job.result


def test_objects_and_buildings(mock_ctx, run_job, godot_project):
    sword = mock_ctx.project.create_asset("weapon", "Sword", "iron sword", "sword")
    ok(run_job(lambda c: run_object(mock_ctx, sword, c, ObjectRunOptions(seed=2, directions=["s", "e"]))))
    sword = Asset.load(sword.root)
    assert "final_view:s" in sword.meta.outputs and "final_view:e" in sword.meta.outputs
    house = mock_ctx.project.create_asset("building", "House")
    ok(run_job(lambda c: run_object(mock_ctx, house, c, ObjectRunOptions(seed=3))))
    house = Asset.load(house.root)
    assert Image.open(house.output_path("final")).size == (96, 96)
    potion = mock_ctx.project.create_asset("item", "Potion")
    ok(run_job(lambda c: run_object(mock_ctx, potion, c, ObjectRunOptions(seed=4, use_3d=False))))
    potion = Asset.load(potion.root)
    assert potion.meta.status == "accepted" and "model_raw" not in potion.meta.outputs
    files = plan_asset_export(sword, godot_project).execute()
    assert any(p.name == "sword_001_e.png" for p in files)


def test_tileset(mock_ctx, run_job, godot_project):
    asset = mock_ctx.project.create_asset("tileset", "Grass Dirt")

    def job(c):
        tiles.make_tile(mock_ctx, asset, c, tiles.TileOptions("grass", seed=1), "base")
        tiles.make_tile(mock_ctx, asset, c, tiles.TileOptions("dirt", seed=2), "overlay")
        return tiles.build_tileset(mock_ctx, asset, seed=3, variants=4)

    atlas = ok(run_job(job))
    assert Image.open(atlas).size == (64, 96)  # 4x4 transitions + 2 rows of variants
    asset = Asset.load(asset.root)
    meta = json.loads(asset.output_path("tileset_meta").read_text())
    assert len(meta["terrain_tiles"]) == 24 and meta["terrain_names"][0].startswith("Grass")
    files = plan_asset_export(asset, godot_project).execute()
    assert any(p.suffix == ".tres" for p in files)


def test_background_layers(mock_ctx, run_job):
    asset = mock_ctx.project.create_asset("background", "Forest", "misty forest")
    ok(run_job(lambda c: backgrounds.run_background(mock_ctx, asset, c, backgrounds.BackgroundOptions(160, 90, layered=True, seed=5))))
    asset = Asset.load(asset.root)
    for layer in backgrounds.LAYERS:
        assert Image.open(asset.output_path("layer:" + layer)).size == (160, 90)
    assert Image.open(asset.output_path("final")).size == (160, 90)
    flat = mock_ctx.project.create_asset("background", "Plains")
    ok(run_job(lambda c: backgrounds.run_background(mock_ctx, flat, c, backgrounds.BackgroundOptions(120, 60, seed=1))))


def test_vfx_and_sheets(mock_ctx, run_job, tmp_path):
    fx = mock_ctx.project.create_asset("vfx", "Fireball")
    paths = vfx.run_vfx(mock_ctx, fx, VFXParams("fire", frames=8, width=32, height=32, seed=4), snap_to_palette=True)
    assert len(paths) == 8
    fx = Asset.load(fx.root)
    assert fx.output_path("sprite_sheet") is not None
    files = []
    for i in range(3):
        p = tmp_path / f"f{i}.png"
        Image.new("RGBA", (20, 24), (i * 50, 0, 0, 255)).save(p)
        files.append(p)
    sheet_asset = mock_ctx.project.create_asset("spritesheet", "Custom")
    png, js = sheets.build_from_files(sheet_asset, {"blink": files}, (24, 24), fps=5, loop=False)
    meta = json.loads(js.read_text())
    assert meta["animations"][0]["fps"] == 5 and len(meta["animations"][0]["frames"]) == 3
    imported = mock_ctx.project.create_asset("vfx", "Imported")
    vfx.import_vfx_frames(mock_ctx, imported, files, fps=8)


def test_failures_are_explained(mock_ctx, run_job):
    asset = mock_ctx.project.create_asset("prop", "Nobody")
    job = run_job(lambda c: three_d.prepare_model(mock_ctx, asset, c))
    assert job.state == JobState.FAILED
    assert "3D model" in job.error.message and job.error.hint
