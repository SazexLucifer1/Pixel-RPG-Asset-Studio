import json

import pytest

from pixel_rpg_studio.comfyui.workflows import WorkflowLibrary, WorkflowTemplate
from pixel_rpg_studio.core.errors import WorkflowError
from tests.fake_comfyui import object_info

BUNDLED = ["character_concept", "character_concept_reference", "object_concept", "object_concept_reference", "building_concept",
           "background_generation", "tileset_generation", "pixel_cleanup", "image_to_3d_hunyuan"]


def test_all_bundled_workflows_valid():
    lib = WorkflowLibrary()
    names = lib.names()
    for n in BUNDLED:
        assert n in names
        wf = lib.get(n)
        assert wf.static_problems() == []
        assert wf.parameters and wf.outputs


def test_build_sets_parameters_and_models():
    wf = WorkflowLibrary().get("character_concept")
    graph = wf.build({"prompt": "hero", "seed": 42, "width": 768}, {"image.checkpoint": "other.safetensors"})
    assert graph["6"]["inputs"]["text"] == "hero"
    assert graph["3"]["inputs"]["seed"] == 42
    assert graph["5"]["inputs"]["width"] == 768
    assert graph["4"]["inputs"]["ckpt_name"] == "other.safetensors"
    # LoRA strength binds two inputs at once
    g2 = wf.build({"lora_strength": 0.5})
    assert g2["10"]["inputs"]["strength_model"] == 0.5 == g2["10"]["inputs"]["strength_clip"]
    # original template untouched
    assert wf.graph["6"]["inputs"]["text"] == ""


def test_profile_and_clamping():
    wf = WorkflowLibrary().get("character_concept")
    g = wf.build({}, profile="low")
    assert g["5"]["inputs"]["width"] == 768
    g = wf.build({"steps": 10_000})
    assert g["3"]["inputs"]["steps"] == 150


def test_unknown_parameter_rejected():
    wf = WorkflowLibrary().get("character_concept")
    with pytest.raises(WorkflowError):
        wf.build({"no_such_param": 1})
    with pytest.raises(WorkflowError):
        wf.build({"seed": "abc"})


def test_validation_against_server():
    wf = WorkflowLibrary().get("character_concept")
    assert wf.validate_against_server(object_info()).ok
    report = wf.validate_against_server(object_info(), {"image.checkpoint": "missing.safetensors"})
    assert report.missing_models == {"image.checkpoint": "missing.safetensors"}
    info = object_info()
    del info["KSampler"]
    report = wf.validate_against_server(info)
    assert report.missing_node_types == ["KSampler"]
    # new-style COMBO format
    assert WorkflowLibrary().get("image_to_3d_hunyuan").validate_against_server(object_info()).ok


def test_ui_format_rejected(tmp_path):
    (tmp_path / "x.json").write_text(json.dumps({"nodes": [], "links": []}))
    (tmp_path / "x.manifest.json").write_text("{}")
    with pytest.raises(WorkflowError) as e:
        WorkflowTemplate.load(tmp_path / "x.json")
    assert "API format" in e.value.message


def test_manifest_inconsistency_detected(tmp_path):
    (tmp_path / "w.json").write_text(json.dumps({"1": {"class_type": "KSampler", "inputs": {"seed": 0}}}))
    (tmp_path / "w.manifest.json").write_text(json.dumps({"parameters": {"seed": {"node": "1", "input": "sead"}}}))
    with pytest.raises(WorkflowError):
        WorkflowTemplate.load(tmp_path / "w.json")
    (tmp_path / "v.json").write_text("{}")
    with pytest.raises(WorkflowError):
        WorkflowTemplate.load(tmp_path / "v.json")  # no manifest


def test_project_workflow_overrides_bundled(tmp_path):
    lib0 = WorkflowLibrary()
    src = lib0.find_path("character_concept")
    override = tmp_path / "workflows"
    override.mkdir()
    data = json.loads(src.read_text())
    data["3"]["inputs"]["steps"] = 7
    (override / "character_concept.json").write_text(json.dumps(data))
    (override / "character_concept.manifest.json").write_text(src.with_name("character_concept.manifest.json").read_text())
    lib = WorkflowLibrary([override])
    assert lib.get("character_concept").graph["3"]["inputs"]["steps"] == 7
    with pytest.raises(WorkflowError):
        lib.get("does_not_exist")
