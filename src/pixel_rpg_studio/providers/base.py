"""Provider interfaces.

Every AI or external tool sits behind one of these interfaces so it can be
replaced (another local model, a different 3D generator, or an optional cloud
service) without touching pipelines or UI. Local providers are the default;
no interface requires network access or paid services.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

ProgressFn = Callable[[float | None, str], None]
CancelledFn = Callable[[], bool]


def _noop_progress(fraction: float | None, message: str) -> None:  # pragma: no cover
    pass


def _never() -> bool:  # pragma: no cover
    return False


@dataclass
class ProviderStatus:
    available: bool
    message: str
    details: str = ""


@dataclass
class ImageRequest:
    workflow: str  # logical workflow name, e.g. "character_concept"
    prompt: str
    negative_prompt: str = ""
    seed: int = 0
    width: int | None = None
    height: int | None = None
    reference_image: Path | None = None  # img2img / reference guidance
    params: dict[str, Any] = field(default_factory=dict)
    count: int = 1


@dataclass
class ImageResult:
    images: list[Path]
    seed: int
    provider: str
    model: str = ""
    models: dict[str, str] = field(default_factory=dict)
    workflow: str = ""
    params: dict[str, Any] = field(default_factory=dict)
    duration_s: float = 0.0


@dataclass
class ThreeDRequest:
    image: Path  # concept image (background removed)
    seed: int = 0
    asset_kind: str = "generic"  # "character", "weapon", "building"...
    params: dict[str, Any] = field(default_factory=dict)


@dataclass
class ThreeDResult:
    mesh_path: Path
    provider: str
    seed: int
    model: str = ""
    workflow: str = ""
    params: dict[str, Any] = field(default_factory=dict)
    duration_s: float = 0.0


@dataclass
class PrepareRequest:
    model_path: Path
    blend_path: Path
    export_model_path: Path | None
    texture_image: Path | None
    rig_mode: str  # "auto_humanoid" | "existing" | "none"
    shading_style: str = "cel"
    shading_bands: int = 3
    light: tuple[float, float] = (-45.0, 50.0)
    facing_correction_deg: float = 0.0
    scale_mode: str = "height"
    decimate_faces: int = 12000
    texture_mode: str = "project"  # project | flat | keep
    concept_image: Path | None = None  # original concept (with background), for providers that need it
    flat_color: tuple[float, float, float] = (0.7, 0.7, 0.7)


@dataclass
class PrepareResult:
    blend_path: Path
    report: dict[str, Any]
    provider: str


@dataclass
class AnimationSpec:
    name: str
    frames: int
    loop: bool = True


@dataclass
class RenderRequest:
    blend_path: Path
    output_dir: Path
    width: int
    height: int
    directions: list[tuple[str, float]]  # (key, yaw degrees)
    animations: list[AnimationSpec]
    elevation_deg: float = 30.0
    yaw_offset_deg: float = 0.0
    light: tuple[float, float] = (-45.0, 50.0)
    ortho_scale: float | None = None
    target: list[float] | None = None
    framing_animations: list[str] = field(default_factory=list)
    only_frames: list[tuple[str, str, int]] | None = None  # (animation, direction, frame)
    margin: float = 1.06


@dataclass
class RenderResult:
    frames: dict[tuple[str, str], dict[int, Path]]  # (animation, direction) -> {frame index: path}
    ortho_scale: float
    target: list[float]
    provider: str
    report: dict[str, Any] = field(default_factory=dict)


class Provider(ABC):
    #: stable identifier stored in metadata
    id: str = "provider"
    #: human readable name
    label: str = "Provider"
    #: True for providers that never leave the machine
    local: bool = True

    @abstractmethod
    def status(self) -> ProviderStatus:
        """Quick availability check (must not raise)."""


class ImageGenerationProvider(Provider):
    @abstractmethod
    def generate(self, request: ImageRequest, out_dir: Path, progress: ProgressFn = _noop_progress,
                 cancelled: CancelledFn = _never) -> ImageResult: ...

    def free_memory(self) -> None:
        """Release VRAM between stages (optional)."""


class ThreeDGenerationProvider(Provider):
    @abstractmethod
    def generate(self, request: ThreeDRequest, out_dir: Path, progress: ProgressFn = _noop_progress,
                 cancelled: CancelledFn = _never) -> ThreeDResult: ...

    def free_memory(self) -> None:
        pass


class RendererProvider(Provider):
    """Turns a 3D model into a prepared scene and renders sprite frames."""

    @abstractmethod
    def prepare(self, request: PrepareRequest, progress: ProgressFn = _noop_progress,
                cancelled: CancelledFn = _never) -> PrepareResult: ...

    @abstractmethod
    def render(self, request: RenderRequest, progress: ProgressFn = _noop_progress,
               cancelled: CancelledFn = _never) -> RenderResult: ...


class AnimationProvider(Provider):
    """Describes how animations are produced (procedural rig, imported actions...).

    The current implementations animate inside the renderer's prepared scene;
    this interface exposes capabilities so UI and pipelines can explain them.
    """

    @abstractmethod
    def supported_animations(self) -> list[str]: ...

    @abstractmethod
    def rig_mode(self) -> str: ...


class ImageProcessingProvider(Provider):
    @abstractmethod
    def process(self, image_path: Path, steps: list[dict[str, Any]], out_path: Path) -> Path: ...
