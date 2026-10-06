import pytest

from pixel_rpg_studio.core.errors import ProjectError
from pixel_rpg_studio.project.asset import Asset, GenerationRecord
from pixel_rpg_studio.project.asset_types import all_asset_types
from pixel_rpg_studio.project.project import Project


def test_create_structure(project):
    root = project.root
    assert (root / "project.json").is_file()
    for sub in ("style/style.json", "style/palette.json", "style/rules.md", "style/references", "style/prompts", "exports"):
        assert (root / sub).exists(), sub
    for t in all_asset_types():
        assert (root / t.folder).is_dir()


def test_reopen(project):
    project.style.sprite_width = 64
    project.palette.colors = ["#000000", "#ffffff"]
    project.save()
    again = Project.open(project.root)
    assert again.info.name == "Test Game"
    assert again.style.sprite_width == 64
    assert again.palette.colors == ["#000000", "#ffffff"]


def test_duplicate_and_invalid(tmp_path, project):
    with pytest.raises(ProjectError):
        Project.create(project.root.parent, "Test Game")
    with pytest.raises(ProjectError):
        Project.create(tmp_path, "   ")
    with pytest.raises(ProjectError):
        Project.open(tmp_path / "nothing_here")


def test_damaged_project_file(project):
    (project.root / "project.json").write_text("[1,2,3]", encoding="utf-8")
    with pytest.raises(ProjectError):
        Project.open(project.root)


def test_newer_format_refused(project):
    project.info.format_version = 999
    project.save()
    with pytest.raises(ProjectError):
        Project.open(project.root)


def test_assets_ids_unique(project):
    a = project.create_asset("character", "Soldier")
    b = project.create_asset("character", "Soldier")
    assert a.id == "soldier_001" and b.id == "soldier_002"
    assert [x.id for x in project.list_assets("character")] == ["soldier_001", "soldier_002"]
    assert project.summary()["character"] == 2


def test_asset_metadata_roundtrip(project):
    a = project.create_asset("weapon", "Sword", "a sword", "sword")
    rec = a.add_generation(GenerationRecord(stage="concept", provider="comfyui", seed=123, prompt="p", negative_prompt="n",
                                            model="m.safetensors", workflow="object_concept", params={"steps": 20}))
    a.meta.settings["x"] = 1
    a.save()
    b = Asset.load(a.root)
    assert b.meta.generations[0].seed == 123
    assert b.meta.generations[0].id == rec.id
    assert b.last_generation("concept").params == {"steps": 20}
    assert b.meta.subtype == "sword"


def test_versioned_paths_never_overwrite(project):
    a = project.create_asset("item", "Potion")
    p1 = a.versioned_path("concept", "concept")
    p1.write_bytes(b"x")
    p2 = a.versioned_path("concept", "concept")
    assert p1 != p2


def test_style_references(project, tmp_path):
    from PIL import Image

    src = tmp_path / "My Ref.PNG"
    Image.new("RGB", (8, 8)).save(src)
    added = project.add_style_reference(src)
    assert added in project.style_references()
    project.remove_style_reference(added)
    assert project.style_references() == []
    with pytest.raises(ProjectError):
        project.add_style_reference(tmp_path / "notes.txt")


def test_delete_asset(project):
    a = project.create_asset("prop", "Crate")
    project.delete_asset(a)
    assert not a.root.exists()


def test_style_validation(project):
    project.style.sprite_width = 2
    with pytest.raises(ProjectError):
        project.save_style()
