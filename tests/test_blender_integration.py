"""Real Blender integration (skipped when Blender is not installed).

Set PIXEL_RPG_TEST_BLENDER to a blender executable to force a specific one.
"""

import os

import pytest

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
