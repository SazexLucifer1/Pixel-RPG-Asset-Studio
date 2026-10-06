from pathlib import Path

import pytest
from PIL import Image

from pixel_rpg_studio.comfyui.client import ComfyUIClient, combo_options
from pixel_rpg_studio.comfyui.workflows import WorkflowLibrary
from pixel_rpg_studio.core.errors import BackendUnavailableError, GenerationError, MissingCustomNodeError, MissingModelError
from pixel_rpg_studio.providers.base import ImageRequest, ThreeDRequest
from pixel_rpg_studio.providers.comfyui_providers import ComfyUIHunyuan3DProvider, ComfyUIImageProvider


def test_status_reachable(fake_comfy):
    st = ComfyUIClient(fake_comfy.url).status()
    assert st.reachable
    assert st.version == "0.3.99-fake"
    assert st.vram_total_gb == 12.0


def test_status_unreachable():
    st = ComfyUIClient("http://127.0.0.1:9", request_timeout=1).status()
    assert not st.reachable and st.error


def test_unreachable_raises_helpful_error():
    with pytest.raises(BackendUnavailableError) as e:
        ComfyUIClient("http://127.0.0.1:9", request_timeout=1).queue()
    assert "Start ComfyUI" in e.value.hint


def test_list_models(fake_comfy):
    c = ComfyUIClient(fake_comfy.url)
    assert "pixel-art-xl.safetensors" in c.list_models("loras")
    assert "sd_xl_base_1.0.safetensors" in c.list_models("checkpoints")


def test_combo_formats():
    assert combo_options([["a", "b"], {}]) == ["a", "b"]
    assert combo_options(["COMBO", {"options": ["x"]}]) == ["x"]
    assert combo_options(["INT", {}]) is None


def test_full_image_generation(fake_comfy, tmp_path):
    client = ComfyUIClient(fake_comfy.url)
    provider = ComfyUIImageProvider(client, WorkflowLibrary(), {}, profile="medium")
    progress = []
    res = provider.generate(ImageRequest("character_concept", "hero", "bad", seed=99), tmp_path, progress=lambda f, m: progress.append(m))
    assert res.images and res.images[0].exists()
    assert Image.open(res.images[0]).size == (64, 64)
    assert res.seed == 99 and res.workflow == "character_concept"
    assert res.models["image.checkpoint"] == "sd_xl_base_1.0.safetensors"
    sent = fake_comfy.prompts[-1]
    assert sent["3"]["inputs"]["seed"] == 99 and sent["6"]["inputs"]["text"] == "hero"
    assert fake_comfy.freed == 1  # models unloaded between stages
    assert progress


def test_reference_upload(fake_comfy, tmp_path):
    ref = tmp_path / "ref.png"
    Image.new("RGB", (32, 32)).save(ref)
    provider = ComfyUIImageProvider(ComfyUIClient(fake_comfy.url), WorkflowLibrary(), {})
    provider.generate(ImageRequest("character_concept_reference", "hero", seed=1, reference_image=ref), tmp_path / "out")
    assert fake_comfy.uploads
    assert fake_comfy.prompts[-1]["11"]["inputs"]["image"] == "pixel_rpg_studio/ref.png"


def test_missing_model_explained(fake_comfy, tmp_path):
    provider = ComfyUIImageProvider(ComfyUIClient(fake_comfy.url), WorkflowLibrary(), {"image.checkpoint": "nope.safetensors"})
    with pytest.raises(MissingModelError) as e:
        provider.generate(ImageRequest("character_concept", "x", seed=1), tmp_path)
    assert "Required model missing" in e.value.message
    assert "nope.safetensors" in e.value.message


def test_missing_node_explained(fake_comfy, tmp_path):
    fake_comfy.missing_nodes.add("SaveGLB")
    provider = ComfyUIHunyuan3DProvider(ComfyUIClient(fake_comfy.url), WorkflowLibrary(), {})
    img = tmp_path / "c.png"
    Image.new("RGB", (32, 32), "white").save(img)
    with pytest.raises(MissingCustomNodeError):
        provider.generate(ThreeDRequest(img, seed=1), tmp_path / "o")


def test_execution_error_explained(fake_comfy, tmp_path):
    fake_comfy.fail_execution = "CUDA out of memory. Tried to allocate 2 GiB"
    provider = ComfyUIImageProvider(ComfyUIClient(fake_comfy.url), WorkflowLibrary(), {})
    with pytest.raises(GenerationError) as e:
        provider.generate(ImageRequest("character_concept", "x", seed=1), tmp_path)
    assert e.value.code == "gpu_oom"
    assert "Low VRAM" in e.value.hint
    fake_comfy.fail_execution = "Some node broke"
    with pytest.raises(GenerationError) as e:
        provider.generate(ImageRequest("character_concept", "x", seed=1), tmp_path)
    assert "custom nodes" in e.value.hint


def test_http_500_translated(fake_comfy):
    fake_comfy.fail_http_500 = True
    with pytest.raises(GenerationError) as e:
        ComfyUIClient(fake_comfy.url).queue()
    assert "HTTP 500" in e.value.message
    assert "custom nodes" in e.value.hint


def test_hunyuan_mesh_download(fake_comfy, tmp_path):
    img = tmp_path / "c.png"
    Image.new("RGB", (32, 32), "white").save(img)
    provider = ComfyUIHunyuan3DProvider(ComfyUIClient(fake_comfy.url), WorkflowLibrary(), {}, profile="low")
    res = provider.generate(ThreeDRequest(img, seed=5), tmp_path / "o")
    assert res.mesh_path.suffix == ".glb" and res.mesh_path.read_bytes().startswith(b"glTF")
    sent = fake_comfy.prompts[-1]
    assert sent["8"]["inputs"]["octree_resolution"] == 192  # low VRAM profile applied
    assert sent["7"]["inputs"]["seed"] == 5


def test_cancel_interrupts(fake_comfy):
    client = ComfyUIClient(fake_comfy.url)
    wf = WorkflowLibrary().get("character_concept").build({"prompt": "x", "seed": 1})
    fake_comfy.history.clear()
    from pixel_rpg_studio.core.errors import JobCancelledError

    with pytest.raises(JobCancelledError):
        client.run(wf, cancelled=lambda: True)
    assert fake_comfy.interrupted == 1
