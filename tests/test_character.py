"""2D character pipeline: reference → identity → poses → frames → sheets → Godot (mock AI)."""

import json

import numpy as np
import pytest
from PIL import Image

from pixel_rpg_studio.core.errors import StudioError
from pixel_rpg_studio.core.jobs import JobState
from pixel_rpg_studio.export.godot import plan_asset_export
from pixel_rpg_studio.pipeline import character as ch
from pixel_rpg_studio.project.asset import Asset
from pixel_rpg_studio.providers.mock import mock_reference


def ok(job):
    assert job.state == JobState.DONE, job.error.full_text() if job.error else job.state
    return job.result


@pytest.fixture
def knight(mock_ctx, tmp_path):
    ref = tmp_path / "knight.png"
    mock_reference(400).save(ref)
    asset = mock_ctx.project.create_asset("character", "Knight")
    ch.set_reference(asset, ref)
    return asset


def test_identity_from_reference(mock_ctx, knight, run_job):
    ident = ok(run_job(lambda c: ch.create_identity(mock_ctx, knight, c, description="knight", seed=11)))
    assert ident["seed"] == 11 and ident["sprite_size"] == 48 and ident["workflow"] == "character_frame"
    assert (knight.root / "identity" / "identity.json").exists()
    assert Image.open(knight.root / ident["reference_clean"]).size == (1024, 1024)
    preview = Image.open(knight.root / ident["pixel_preview"])
    assert preview.size == (48, 48)
    # palette locked from the reference: steel grey and blue are in it
    pal = [tuple(int(h[i:i + 2], 16) for i in (1, 3, 5)) for h in ident["palette"]]
    assert any(abs(r - 170) < 30 and abs(g - 175) < 30 and abs(b - 190) < 30 for r, g, b in pal)
    assert any(b > r + 60 for r, g, b in pal)
    assert ident["generation_size"] == ch.generation_size(mock_ctx.providers.profile)


def test_generation_requires_identity(mock_ctx, knight, run_job):
    job = run_job(lambda c: ch.generate_animation(mock_ctx, knight, c, "walk", ["s"]))
    assert job.state == JobState.FAILED and "identity" in job.error.message


def test_walk_animation_frames_records_and_export(mock_ctx, knight, run_job, godot_project):
    ok(run_job(lambda c: ch.create_identity(mock_ctx, knight, c, seed=5, reference_strength=0.7, pose_strength=0.95)))
    ok(run_job(lambda c: ch.generate_animation(mock_ctx, knight, c, "walk", ["s", "w"], frames=6)))
    asset = Asset.load(knight.root)
    info = asset.meta.animations["walk"]
    assert info.frames == 6 and info.directions == ["s", "w"]
    frames = [np.array(Image.open(asset.root / r)) for r in info.final_frames["w"]]
    assert all(f.shape == (48, 48, 4) for f in frames)
    assert all(f[0, 0, 3] == 0 for f in frames)  # transparent background
    assert all(set(np.unique(f[..., 3]).tolist()) <= {0, 255} for f in frames)  # no soft edges
    assert any((frames[0] != f).any() for f in frames[1:])  # the pose changes
    # colours come only from the identity palette
    ident = ch.load_identity(asset)
    pal = {tuple(int(h[i:i + 2], 16) for i in (1, 3, 5)) for h in ident["palette"]}
    for f in frames:
        cols = {tuple(c) for c in f[f[..., 3] > 0][:, :3]}
        assert cols <= pal
    # one generation record per frame with everything needed to reproduce it
    recs = [g for g in asset.meta.generations if g.stage == "frame"]
    assert len(recs) == 12
    r = asset.last_generation("frame", "walk/w/3")
    assert r.seed == 5 and r.prompt and r.negative_prompt and r.workflow == "character_frame" and r.model
    for key in ("reference_strength", "pose_strength", "resolution", "palette", "direction", "animation", "frame", "sprite_size"):
        assert key in r.params
    assert r.params["reference_strength"] == 0.7 and r.params["pose_strength"] == 0.95 and r.params["frame"] == 3
    assert "side view" in r.prompt and "walking" in r.prompt
    assert any("pose" in ref["path"] for ref in r.references) and any("reference_clean" in ref["path"] for ref in r.references)
    # sheets: knight_walk.png, one row per direction
    sheet = asset.output_path("anim_sheet:walk")
    assert sheet.name == "knight_walk.png" and Image.open(sheet).size == (6 * 48, 2 * 48)
    meta = json.loads(sheet.with_suffix(".json").read_text())
    assert [a["name"] for a in meta["animations"]] == ["walk_s", "walk_w"]
    # Godot
    written = plan_asset_export(asset, godot_project).execute()
    names = {p.name for p in written}
    assert {"knight_walk.png", "knight_walk.json"} <= names and any(n.endswith("_frames.tres") for n in names)
    tres = next(p for p in written if p.name.endswith("_frames.tres")).read_text()
    assert '&"walk_w"' in tres


def test_regenerate_single_frame_only_changes_seed(mock_ctx, knight, run_job):
    ok(run_job(lambda c: ch.create_identity(mock_ctx, knight, c, seed=1)))
    ok(run_job(lambda c: ch.generate_animation(mock_ctx, knight, c, "attack", ["e"], frames=4)))
    asset = Asset.load(knight.root)
    final_dir = asset.path("animations", "attack", "e", "final")
    before = {p.name: p.read_bytes() for p in final_dir.glob("*.png")}
    mtimes = {p.name: p.stat().st_mtime_ns for p in final_dir.glob("*.png")}
    old = asset.last_generation("frame", "attack/e/2")
    ch.update_identity(asset, reference_strength=0.3)  # later setting changes must not leak into the regeneration
    ok(run_job(lambda c: ch.regenerate_frame(mock_ctx, asset, c, "attack", "e", 2, seed=999)))
    asset = Asset.load(asset.root)
    changed = [n for n in mtimes if (final_dir / n).stat().st_mtime_ns != mtimes[n]]
    assert changed == ["frame_002.png"]
    new = asset.last_generation("frame", "attack/e/2")
    assert new.seed == 999 != old.seed
    for key in ("reference_strength", "pose_strength", "direction", "animation", "frame", "resolution"):
        assert new.params[key] == old.params[key]
    assert new.prompt == old.prompt and new.negative_prompt == old.negative_prompt
    assert ch.frame_seed(asset, "attack", "e", 2) == 999 and ch.frame_seed(asset, "attack", "e", 1) == 1
    assert before.keys() == {p.name for p in final_dir.glob("*.png")}
    assert len(list(asset.path("generated").glob("attack_e_002_*.png"))) == 2  # old output kept


def test_pose_edit_drives_generation(mock_ctx, knight, run_job):
    ok(run_job(lambda c: ch.create_identity(mock_ctx, knight, c, seed=2)))
    ch.ensure_animation(knight, "idle", frames=2, directions=["s"])
    pose = ch.get_pose(knight, "idle", "s", 0)
    pose.move("r_wrist", 0.15, 0.2)
    ch.set_pose(knight, "idle", "s", 0, pose)
    ok(run_job(lambda c: ch.generate_frame(mock_ctx, knight, c, "idle", "s", 0)))
    raw = np.array(Image.open(knight.path("animations", "idle", "s", "raw", "frame_000.png")).convert("RGB"))
    h = raw.shape[0]
    assert (raw[int(0.2 * h) - 3:int(0.2 * h) + 3, int(0.15 * h) - 3:int(0.15 * h) + 3] < 235).any()  # arm raised where the pose says
    ch.apply_preset(knight, "idle", "s", 1, "Death")
    assert ch.get_pose(knight, "idle", "s", 1).source == "preset:Death"


def test_custom_animation_and_sizes(mock_ctx, knight, run_job):
    ok(run_job(lambda c: ch.create_identity(mock_ctx, knight, c, seed=3, sprite_size=32)))
    info = ch.ensure_animation(knight, "cast", frames=3, directions=["n"])
    assert info.frames == 3 and len(ch._read_poses(knight, "cast", "n")["frames"]) == 3
    ok(run_job(lambda c: ch.generate_animation(mock_ctx, knight, c, "cast", ["n"])))
    a = Asset.load(knight.root)
    assert Image.open(a.root / a.meta.animations["cast"].final_frames["n"][0]).size == (32, 32)
    ch.update_identity(a, sprite_size=64)
    assert ok(run_job(lambda c: ch.reprocess_frames(mock_ctx, a, c))) == 3
    a = Asset.load(a.root)
    assert Image.open(a.root / a.meta.animations["cast"].final_frames["n"][0]).size == (64, 64)
    with pytest.raises(StudioError):
        ch.update_identity(a, sprite_size=50)
    files = ch.export_png_frames(a, a.path("pngs"))
    assert [f.name for f in files] == ["knight_cast_n_00.png", "knight_cast_n_01.png", "knight_cast_n_02.png"]


def test_low_vram_canvas():
    assert ch.generation_size("low") == 768 and ch.generation_size("high") == 1024
