"""ComfyUI-backed providers (local AI, default)."""

from __future__ import annotations

import time
from pathlib import Path

from PIL import Image

from pixel_rpg_studio.comfyui.client import ComfyUIClient
from pixel_rpg_studio.comfyui.workflows import WorkflowLibrary
from pixel_rpg_studio.core.errors import GenerationError
from pixel_rpg_studio.providers.base import (
    CancelledFn,
    ImageGenerationProvider,
    ImageRequest,
    ImageResult,
    ProgressFn,
    ProviderStatus,
    ThreeDGenerationProvider,
    ThreeDRequest,
    ThreeDResult,
    _never,
    _noop_progress,
)


class _ComfyBase:
    def __init__(self, client: ComfyUIClient, library: WorkflowLibrary, model_roles: dict[str, str],
                 profile: str = "medium", timeout_s: float = 1800.0, unload_after: bool = True) -> None:
        self.client = client
        self.library = library
        self.model_roles = model_roles
        self.profile = profile
        self.timeout_s = timeout_s
        self.unload_after = unload_after

    def status(self) -> ProviderStatus:
        st = self.client.status()
        if st.reachable:
            return ProviderStatus(True, f"ComfyUI {st.version or ''} at {st.url}".strip())
        return ProviderStatus(False, f"ComfyUI not reachable at {st.url}", st.error)

    def free_memory(self) -> None:
        self.client.free_memory(unload_models=True)


class ComfyUIImageProvider(_ComfyBase, ImageGenerationProvider):
    id = "comfyui"
    label = "ComfyUI (local)"

    def generate(self, request: ImageRequest, out_dir: Path, progress: ProgressFn = _noop_progress,
                 cancelled: CancelledFn = _never) -> ImageResult:
        template = self.library.get(request.workflow)
        params = dict(request.params)
        params.update({"prompt": request.prompt, "negative_prompt": request.negative_prompt, "seed": request.seed})
        if request.width:
            params["width"] = request.width
        if request.height:
            params["height"] = request.height
        if request.count > 1 and "batch_size" in template.parameters:
            params["batch_size"] = request.count
        if request.reference_image is not None:
            if "init_image" not in template.parameters:
                raise GenerationError(f"Workflow '{template.name}' does not accept a reference image.")
            progress(None, "Uploading reference image to ComfyUI")
            params["init_image"] = self.client.upload_image(request.reference_image)
        params = {k: v for k, v in params.items() if k in template.parameters}
        graph = template.build(params, self.model_roles, profile=self.profile)
        started = time.time()
        result = self.client.run(graph, progress=progress, cancelled=cancelled, timeout=self.timeout_s)
        images = result.files_with_extension(".png", ".jpg", ".jpeg", ".webp")
        if not images:
            raise GenerationError(
                "ComfyUI finished but produced no images.",
                hint="Check that the workflow ends with a SaveImage node and that its manifest lists the correct output node.",
            )
        out_dir.mkdir(parents=True, exist_ok=True)
        paths = []
        for n, f in enumerate(images):
            dest = out_dir / f"comfy_{request.seed}_{n}{Path(f.filename).suffix}"
            self.client.download(f, dest)
            Image.open(dest).verify()
            paths.append(dest)
        if self.unload_after:
            self.free_memory()
        models = template.resolved_models(self.model_roles)
        return ImageResult(
            images=paths, seed=request.seed, provider=self.id, model=models.get("image.checkpoint", ""), models=models,
            workflow=template.name, params={k: v for k, v in params.items() if k not in ("prompt", "negative_prompt")},
            duration_s=time.time() - started,
        )


class ComfyUIHunyuan3DProvider(_ComfyBase, ThreeDGenerationProvider):
    id = "comfyui_hunyuan3d"
    label = "Hunyuan3D 2 mini via ComfyUI (local)"
    workflow = "image_to_3d_hunyuan"

    def generate(self, request: ThreeDRequest, out_dir: Path, progress: ProgressFn = _noop_progress,
                 cancelled: CancelledFn = _never) -> ThreeDResult:
        template = self.library.get(self.workflow)
        progress(None, "Uploading concept image to ComfyUI")
        uploaded = self.client.upload_image(request.image)
        params = {"image": uploaded, "seed": request.seed}
        params.update({k: v for k, v in request.params.items() if k in template.parameters})
        graph = template.build(params, self.model_roles, profile=self.profile)
        started = time.time()
        result = self.client.run(graph, progress=progress, cancelled=cancelled, timeout=self.timeout_s)
        meshes = result.files_with_extension(".glb", ".gltf", ".obj", ".ply", ".stl")
        if not meshes:
            raise GenerationError(
                "ComfyUI finished but produced no 3D mesh.",
                hint="Make sure your ComfyUI version includes the native Hunyuan3D nodes (SaveGLB) and the shape model is installed.",
            )
        out_dir.mkdir(parents=True, exist_ok=True)
        dest = out_dir / f"mesh_{request.seed}{Path(meshes[0].filename).suffix}"
        self.client.download(meshes[0], dest)
        if self.unload_after:
            self.free_memory()
        models = template.resolved_models(self.model_roles)
        return ThreeDResult(dest, self.id, request.seed, model=models.get("threed.shape_model", ""), workflow=template.name,
                            params={k: v for k, v in params.items() if k != "image"}, duration_s=time.time() - started)
