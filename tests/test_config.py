import json

from pixel_rpg_studio.core import paths
from pixel_rpg_studio.core.config import AppSettings, load_settings, save_settings, settings_path


def test_defaults_when_missing():
    s = load_settings()
    assert isinstance(s, AppSettings)
    assert s.comfyui.port == 8188
    assert s.setup_completed is False


def test_roundtrip(isolated_home):
    s = AppSettings()
    s.comfyui.port = 8000
    s.blender.executable = r"C:\Program Files\Blender Foundation\Blender 4.2\blender.exe"
    s.models.roles["image.checkpoint"] = "my_model.safetensors"
    path = save_settings(s)
    assert path.parent == isolated_home  # stored outside the repository, in the app-data dir
    loaded = load_settings()
    assert loaded.comfyui.port == 8000
    assert loaded.blender.executable.endswith("blender.exe")
    assert loaded.models.roles == {"image.checkpoint": "my_model.safetensors"}


def test_corrupt_file_falls_back_to_defaults(isolated_home):
    settings_path().write_text("{ this is not json", encoding="utf-8")
    s = load_settings()
    assert s.comfyui.port == 8188
    assert (isolated_home / "settings.json.corrupt").exists()


def test_unknown_keys_preserved_and_bad_types_ignored():
    settings_path().write_text(json.dumps({"future_option": 42, "comfyui": {"port": "not a number", "host": "localhost"}}), encoding="utf-8")
    s = load_settings()
    assert s.comfyui.port == 8188  # bad type ignored
    assert s.comfyui.host == "localhost"
    save_settings(s)
    data = json.loads(settings_path().read_text(encoding="utf-8"))
    assert data["future_option"] == 42


def test_validation_rejects_bad_values():
    s = AppSettings()
    s.comfyui.port = 70000
    s.gpu.vram_profile = "huge"
    problems = s.validate()
    assert len(problems) == 2


def test_recent_projects_deduplicated():
    s = AppSettings()
    for p in ("a", "b", "a"):
        s.add_recent_project(p)
    assert [str(x) for x in s.ui.recent_projects] == ["a", "b"]
    assert s.ui.last_project == "a"


def test_app_data_dir_override(isolated_home):
    assert paths.app_data_dir() == isolated_home
