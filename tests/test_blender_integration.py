"""Real Blender integration (skipped when Blender is not installed).

Set PIXEL_RPG_TEST_BLENDER to a blender executable to force a specific one.
"""

import os

import numpy as np
import pytest
from PIL import Image, ImageDraw

from pixel_rpg_studio.blender.detect import find_blender

BLENDER = find_blender(os.environ.get("PIXEL_RPG_TEST_BLENDER", ""))
pytestmark = [pytest.mark.blender, pytest.mark.skipif(BLENDER is None, reason="Blender not installed")]


@pytest.fixture
def blender_ctx(project, mock_settings):
    from pixel_rpg_studio.blender.runner import BlenderRunner
    from pixel_rpg_studio.pipeline.common import PipelineContext
    from pixel_rpg_studio.providers.blender_provider import BlenderRenderer
    from pixel_rpg_studio.providers.registry import build_providers

    providers = build_providers(mock_settings, gpus=[])
    providers.renderer = BlenderRenderer(BlenderRunner(str(BLENDER.executable)))
    project.style.render_scale = 4
    return PipelineContext(project, providers)


def test_character_through_real_blender(blender_ctx, run_job, tmp_path):
    from pixel_rpg_studio.pipeline import three_d
    from pixel_rpg_studio.pipeline.character import CharacterRunOptions, run_character
    from pixel_rpg_studio.project.asset import Asset

    concept = tmp_path / "concept.png"
    img = Image.new("RGB", (200, 200), "white")
    d = ImageDraw.Draw(img)
    d.rectangle((90, 10, 110, 40), fill=(240, 200, 160))
    d.rectangle((60, 40, 140, 110), fill=(40, 80, 200))
    d.rectangle((80, 110, 120, 190), fill=(90, 60, 40))
    img.save(concept)
    asset = blender_ctx.project.create_asset("character", "Blocky")
    job = run_job(lambda c: run_character(blender_ctx, asset, c, CharacterRunOptions(concept_image=concept, animations=["walk"])), timeout=600)
    assert job.error is None, job.error.full_text()
    asset = Asset.load(asset.root)
    rep = asset.meta.settings["prepare_report"]
    assert rep["rig"]["mode"] == "auto_humanoid" and rep["rig"]["bones"] == 17
    assert rep["rig"]["unweighted_vertices"] == 0
    assert (asset.root / asset.meta.settings["blend"]).exists()
    frames = asset.meta.animations["walk"].final_frames["w"]
    assert len(frames) == 6
    arrays = [np.array(Image.open(asset.root / f)) for f in frames]
    assert all(a.shape == (48, 48, 4) and a[..., 3].any() for a in arrays)
    assert any((arrays[0] != a).any() for a in arrays[1:]), "walk frames should differ (legs move)"
    # feet stay on the same pixel row across frames (fixed camera, stable framing)
    bottoms = {int(np.nonzero(a[..., 3].any(axis=1))[0].max()) for a in arrays}
    assert max(bottoms) - min(bottoms) <= 2
    # single frame regeneration uses the stored framing
    framing = dict(asset.meta.settings["camera_framing"])
    job = run_job(lambda c: three_d.regenerate_frame(blender_ctx, asset, c, "walk", "s", 2), timeout=300)
    assert job.error is None, job.error.full_text()
    assert Asset.load(asset.root).meta.settings["camera_framing"]["ortho_scale"] == pytest.approx(framing["ortho_scale"])


def test_static_object_views(blender_ctx, run_job, tmp_path):
    from pixel_rpg_studio.pipeline.objects import ObjectRunOptions, run_object
    from pixel_rpg_studio.project.asset import Asset

    asset = blender_ctx.project.create_asset("prop", "Crate")
    job = run_job(lambda c: run_object(blender_ctx, asset, c, ObjectRunOptions(seed=1, directions=["s", "e", "n"])), timeout=600)
    assert job.error is None, job.error.full_text()
    asset = Asset.load(asset.root)
    for d in ("s", "e", "n"):
        assert asset.output_path("final_view:" + d) is not None
    assert asset.output_path("model") is not None  # cleaned GLB exported


def test_broken_model_reports_error(blender_ctx, run_job, tmp_path):
    from pixel_rpg_studio.pipeline import three_d

    bad = tmp_path / "bad.obj"
    bad.write_text("this is not an obj file\n")
    asset = blender_ctx.project.create_asset("prop", "Broken")
    three_d.import_model(asset, bad)
    job = run_job(lambda c: three_d.prepare_model(blender_ctx, asset, c), timeout=300)
    assert job.error is not None
    assert "Blender" in job.error.message and job.error.details
