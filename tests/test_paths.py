from pathlib import Path

from pixel_rpg_studio.core import paths


def test_safe_filename_basic():
    assert paths.safe_filename("My Hero!") == "my_hero"
    assert paths.safe_filename("  ..  ") == "untitled"
    assert paths.safe_filename("Ärger über Öl") == "rger_ber_l"
    assert paths.safe_filename("a/b\\c:d*e?f") == "abcdef"


def test_safe_filename_windows_reserved():
    assert paths.safe_filename("CON") == "con_"
    assert paths.safe_filename("lpt1") == "lpt1_"


def test_safe_filename_length():
    assert len(paths.safe_filename("x" * 500)) <= 64


def test_is_within(tmp_path):
    root = tmp_path / "root"
    (root / "a").mkdir(parents=True)
    assert paths.is_within(root / "a", root)
    assert not paths.is_within(tmp_path, root)
    assert not paths.is_within(root / ".." / "elsewhere", root)


def test_resources_exist():
    assert (paths.bundled_workflows_dir() / "object_concept.json").is_file()
    assert (paths.bundled_config_dir() / "models_catalog.json").is_file()
    assert (paths.blender_scripts_dir() / "studio_blender.py").is_file()


def test_writable(tmp_path):
    assert paths.is_writable_dir(tmp_path / "new" / "dir")
    assert paths.logs_dir().is_dir()
