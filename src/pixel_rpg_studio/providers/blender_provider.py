"""Blender-backed renderer and procedural animation provider."""

from __future__ import annotations

from pathlib import Path

from pixel_rpg_studio.blender.runner import BlenderRunner
from pixel_rpg_studio.core.errors import StudioError
from pixel_rpg_studio.project.asset_types import ANIMATIONS
from pixel_rpg_studio.providers.base import (
    AnimationProvider,
    CancelledFn,
    PrepareRequest,
    PrepareResult,
    ProgressFn,
    ProviderStatus,
    RendererProvider,
    RenderRequest,
    RenderResult,
    _never,
    _noop_progress,
)


class BlenderRenderer(RendererProvider):
    id = "blender"
    label = "Blender (local)"

    def __init__(self, runner: BlenderRunner, engine: str = "cycles") -> None:
        self.runner = runner
        self.engine = engine

    def status(self) -> ProviderStatus:
        try:
            info = self.runner.info()
        except StudioError as exc:
            return ProviderStatus(False, exc.message, exc.hint)
        return ProviderStatus(True, f"Blender {info.version_str}", str(info.executable))

    def prepare(self, request: PrepareRequest, progress: ProgressFn = _noop_progress, cancelled: CancelledFn = _never) -> PrepareResult:
        request.blend_path.parent.mkdir(parents=True, exist_ok=True)
        job = {
            "mode": "prepare",
            "model_path": str(request.model_path),
            "blend_path": str(request.blend_path),
            "export_model_path": str(request.export_model_path) if request.export_model_path else "",
            "cleanup": {"decimate_faces": request.decimate_faces, "facing_correction_deg": request.facing_correction_deg,
                        "scale_mode": request.scale_mode, "target_size": 2.0},
            "texture": {"mode": request.texture_mode, "image": str(request.texture_image) if request.texture_image else "",
                        "flat_color": list(request.flat_color)},
            "shading": {"style": request.shading_style, "bands": request.shading_bands},
            "light": {"direction": list(request.light)},
            "rig": {"mode": request.rig_mode},
        }
        progress(None, "Blender: importing, cleaning and preparing the model")
        report = self.runner.run_job(job, work_dir=request.blend_path.parent / "jobs", progress=progress, cancelled=cancelled)
        return PrepareResult(request.blend_path, report, self.id)

    def render(self, request: RenderRequest, progress: ProgressFn = _noop_progress, cancelled: CancelledFn = _never) -> RenderResult:
        camera = {"elevation_deg": request.elevation_deg, "yaw_offset_deg": request.yaw_offset_deg, "margin": request.margin}
        if request.ortho_scale and request.target:
            camera["ortho_scale"] = request.ortho_scale
            camera["target"] = list(request.target)
        job = {
            "mode": "render",
            "blend_path": str(request.blend_path),
            "output_dir": str(request.output_dir),
            "render": {"width": request.width, "height": request.height, "engine": self.engine, "samples": 8},
            "camera": camera,
            "light": {"direction": list(request.light)},
            "directions": [{"key": k, "yaw_deg": y} for k, y in request.directions],
            "animations": [{"name": a.name, "frames": a.frames, "loop": a.loop} for a in request.animations],
            "framing_animations": request.framing_animations,
        }
        if request.only_frames:
            job["only_frames"] = [{"animation": a, "direction": d, "frame": f} for a, d, f in request.only_frames]
            expected = len(request.only_frames)
        else:
            expected = sum(a.frames for a in request.animations) * len(request.directions)
        report = self.runner.run_job(job, work_dir=Path(request.output_dir) / "_jobs", progress=progress,
                                     cancelled=cancelled, expected_frames=expected)
        frames: dict[tuple[str, str], dict[int, Path]] = {}
        for f in report.get("frames", []):
            frames.setdefault((f["animation"], f["direction"]), {})[int(f["frame"])] = Path(f["path"])
        return RenderResult(frames, float(report["ortho_scale"]), list(report["target"]), self.id, report)


class ProceduralAnimationProvider(AnimationProvider):
    """Automatic humanoid rig + procedural animation inside Blender.

    Limitations (shown in the UI): heuristic rig for upright T/A-pose
    humanoids; animations are simple procedural cycles. Imported rigged models
    with actions named like the animation (e.g. "Walk") use those actions.
    """

    id = "blender_procedural"
    label = "Automatic rig + procedural animation (Blender)"

    def status(self) -> ProviderStatus:
        return ProviderStatus(True, "Procedural humanoid animation (requires Blender)")

    def supported_animations(self) -> list[str]:
        return list(ANIMATIONS)

    def rig_mode(self) -> str:
        return "auto_humanoid"
