import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """Every test gets its own app-data dir: tests never touch real user settings."""
    home = tmp_path / "app_home"
    monkeypatch.setenv("PIXEL_RPG_STUDIO_HOME", str(home))
    return home


@pytest.fixture
def project(tmp_path):
    from pixel_rpg_studio.project.project import Project

    return Project.create(tmp_path / "projects", "Test Game")


@pytest.fixture
def mock_settings():
    from pixel_rpg_studio.core.config import AppSettings

    return AppSettings(use_mock_providers=True, setup_completed=True)


@pytest.fixture
def mock_ctx(project, mock_settings):
    from pixel_rpg_studio.pipeline.common import PipelineContext
    from pixel_rpg_studio.providers.registry import build_providers

    return PipelineContext(project, build_providers(mock_settings, gpus=[]))


@pytest.fixture
def run_job():
    """Run a function through the real JobQueue and return (job)."""
    from pixel_rpg_studio.core.jobs import JobQueue

    queues = []

    def _run(func, timeout=120):
        q = JobQueue()
        queues.append(q)
        job = q.submit("test", func)
        assert job.wait(timeout), "job timed out"
        return job

    yield _run
    for q in queues:
        q.shutdown()


@pytest.fixture
def fake_comfy():
    from tests.fake_comfyui import FakeComfyUI

    server = FakeComfyUI()
    server.start()
    yield server
    server.stop()


@pytest.fixture
def godot_project(tmp_path):
    d = tmp_path / "godot_game"
    d.mkdir()
    (d / "project.godot").write_text('config_version=5\n[application]\nconfig/name="Test"\n', encoding="utf-8")
    return d
