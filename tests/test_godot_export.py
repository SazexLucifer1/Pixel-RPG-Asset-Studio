import json

import pytest
from PIL import Image

from pixel_rpg_studio.core.errors import ExportConflictError, StudioError
from pixel_rpg_studio.export.godot import is_godot_project, plan_asset_export, spriteframes_tres, tileset_tres
from pixel_rpg_studio.spritesheet.builder import AnimationSpec, SheetLayout, build_sheet


def sheet_meta():
    anims = [AnimationSpec("idle", [Image.new("RGBA", (8, 8))] * 2, 6, True, "s"), AnimationSpec("attack", [Image.new("RGBA", (8, 8))] * 3, 12, False, "s")]
    return build_sheet(anims, SheetLayout(8, 8))


def test_spriteframes_resource_format():
    _, meta = sheet_meta()
    text = spriteframes_tres("res://a/sheet.png", meta)
    assert text.startswith('[gd_resource type="SpriteFrames" load_steps=7 format=3]')
    assert '[ext_resource type="Texture2D" path="res://a/sheet.png" id="1_sheet"]' in text
    assert text.count('[sub_resource type="AtlasTexture"') == 5
    assert '"name": &"attack_s"' in text and '"loop": false' in text and '"speed": 12.0' in text
    assert "region = Rect2(8, 0, 8, 8)" in text


def test_tileset_resource_with_terrain():
    meta = [{"atlas": [0, 0], "terrain": 0, "corners": {"top_left": 0, "top_right": 0, "bottom_left": 1, "bottom_right": 1}}]
    text = tileset_tres("res://t.png", 16, 4, 4, meta, ("Grass", "Dirt"))
    assert "tile_size = Vector2i(16, 16)" in text
    assert "terrain_set_0/mode = 1" in text
    assert "0:0/0/terrains_peering_bit/bottom_right_corner = 1" in text
    assert 'terrain_set_0/terrain_1/name = "Dirt"' in text
    plain = tileset_tres("res://t.png", 16, 2, 2)
    assert "3:" not in plain and "1:1/0 = 0" in plain


def test_export_plan_and_conflicts(project, godot_project):
    asset = project.create_asset("character", "Hero")
    sheet, meta = sheet_meta()
    png = asset.path("exports", "hero_sheet.png")
    png.parent.mkdir(parents=True)
    sheet.save(png)
    js = png.with_suffix(".json")
    js.write_text(json.dumps(meta.to_dict()))
    asset.set_output("sprite_sheet", png)
    asset.set_output("sprite_sheet_meta", js)
    asset.save()
    assert is_godot_project(godot_project)
    plan = plan_asset_export(asset, godot_project)
    targets = [f.target for f in plan.files]
    assert "assets/generated/characters/hero_001/hero_001_sheet.png" in targets
    assert "assets/generated/characters/hero_001/hero_001_frames.tres" in targets
    assert "assets/generated/scripts/pixel_rpg_directional_sprite.gd" in targets
    plan.execute()
    tres = (godot_project / "assets/generated/characters/hero_001/hero_001_frames.tres").read_text()
    assert 'path="res://assets/generated/characters/hero_001/hero_001_sheet.png"' in tres
    # second export must not silently overwrite
    with pytest.raises(ExportConflictError) as e:
        plan_asset_export(asset, godot_project).execute()
    assert len(e.value.conflicts) == 4
    marker = godot_project / "assets/generated/characters/hero_001/hero_001_sheet.png"
    before = marker.stat().st_mtime_ns
    plan_asset_export(asset, godot_project).execute(overwrite=True)
    assert marker.exists() and marker.stat().st_mtime_ns >= before


def test_export_without_output_fails(project, godot_project):
    asset = project.create_asset("item", "Empty")
    with pytest.raises(StudioError):
        plan_asset_export(asset, godot_project)
