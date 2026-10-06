"""Builds the active provider set from settings.

New providers (e.g. an optional cloud service) register a factory here; the
pipelines only see the interfaces from :mod:`providers.base`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from pixel_rpg_studio.blender.runner import BlenderRunner
from pixel_rpg_studio.comfyui.client import ComfyUIClient
from pixel_rpg_studio.comfyui.workflows import WorkflowLibrary
from pixel_rpg_studio.core.config import AppSettings
from pixel_rpg_studio.providers.base import (
    ImageGenerationProvider,
    ImageProcessingProvider,
    RendererProvider,
    ThreeDGenerationProvider,
)
from pixel_rpg_studio.providers.blender_provider import BlenderRenderer
from pixel_rpg_studio.providers.comfyui_providers import ComfyUIHunyuan3DProvider, ComfyUIImageProvider
from pixel_rpg_studio.providers.mock import BuiltinImageProcessor, MockImageProvider, MockRenderer, MockThreeDProvider
from pixel_rpg_studio.system.gpu import resolve_profile


@dataclass
class ProviderSet:
    image: ImageGenerationProvider
    threed: ThreeDGenerationProvider
    renderer: RendererProvider
    processing: ImageProcessingProvider
    profile: str = "medium"
    mock: bool = False

    def all(self) -> dict[str, object]:
        return {"image": self.image, "threed": self.threed, "renderer": self.renderer, "processing": self.processing}


Factory = Callable[[AppSettings, "BuildContext"], object]


@dataclass
class BuildContext:
    client: ComfyUIClient
    library: WorkflowLibrary
    profile: str


IMAGE_PROVIDERS: dict[str, tuple[str, Factory]] = {}
THREED_PROVIDERS: dict[str, tuple[str, Factory]] = {}
RENDERER_PROVIDERS: dict[str, tuple[str, Factory]] = {}


def register_image_provider(key: str, label: str, factory: Factory) -> None:
    IMAGE_PROVIDERS[key] = (label, factory)


def register_threed_provider(key: str, label: str, factory: Factory) -> None:
    THREED_PROVIDERS[key] = (label, factory)


def register_renderer_provider(key: str, label: str, factory: Factory) -> None:
    RENDERER_PROVIDERS[key] = (label, factory)


def _comfy_kwargs(s: AppSettings, ctx: BuildContext) -> dict:
    return dict(client=ctx.client, library=ctx.library, model_roles=s.models.roles, profile=ctx.profile,
                timeout_s=s.comfyui.generation_timeout_s, unload_after=s.gpu.unload_models_between_stages)


register_image_provider("comfyui", "ComfyUI (local)", lambda s, c: ComfyUIImageProvider(**_comfy_kwargs(s, c)))
register_image_provider("mock", "Mock (testing)", lambda s, c: MockImageProvider())
register_threed_provider("comfyui_hunyuan3d", "Hunyuan3D 2 mini via ComfyUI (local)", lambda s, c: ComfyUIHunyuan3DProvider(**_comfy_kwargs(s, c)))
register_threed_provider("blockout", "Placeholder blockout mesh (no AI)", lambda s, c: MockThreeDProvider())
register_threed_provider("mock", "Mock (testing)", lambda s, c: MockThreeDProvider())
register_renderer_provider("blender", "Blender (local)", lambda s, c: BlenderRenderer(BlenderRunner(s.blender.executable, s.blender.render_timeout_s)))
register_renderer_provider("mock", "Mock (testing)", lambda s, c: MockRenderer())


def make_client(settings: AppSettings) -> ComfyUIClient:
    return ComfyUIClient(settings.comfyui.base_url, settings.comfyui.request_timeout_s)


def build_providers(settings: AppSettings, library: WorkflowLibrary | None = None, client: ComfyUIClient | None = None,
                    gpus: list | None = None) -> ProviderSet:
    profile = resolve_profile(settings.gpu.vram_profile, gpus).key
    ctx = BuildContext(client or make_client(settings), library or WorkflowLibrary(), profile)
    if settings.use_mock_providers:
        return ProviderSet(MockImageProvider(), MockThreeDProvider(), MockRenderer(), BuiltinImageProcessor(), profile, mock=True)

    def pick(table: dict, key: str, default: str):
        _, factory = table.get(key) or table[default]
        return factory(settings, ctx)

    return ProviderSet(
        image=pick(IMAGE_PROVIDERS, settings.providers.image, "comfyui"),
        threed=pick(THREED_PROVIDERS, settings.providers.threed, "comfyui_hunyuan3d"),
        renderer=pick(RENDERER_PROVIDERS, settings.providers.renderer, "blender"),
        processing=BuiltinImageProcessor(),
        profile=profile,
    )
