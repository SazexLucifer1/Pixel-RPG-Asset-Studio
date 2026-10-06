"""Mock providers for tests and "demo mode".

They need no GPU, no ComfyUI and no Blender, produce deterministic output
from the seed, and exercise exactly the same pipeline code paths as the real
providers. Everything they produce is clearly labelled as mock output in the
metadata (provider id ``mock_*``). They are NOT a replacement for real
generation.
"""

from __future__ import annotations

import hashlib
import json
import math
import time
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from pixel_rpg_studio.imaging.pixel import crop_to_content, remove_background
from pixel_rpg_studio.providers.base import (
    CancelledFn,
    ImageGenerationProvider,
    ImageProcessingProvider,
    ImageRequest,
    ImageResult,
    PrepareRequest,
    PrepareResult,
    ProgressFn,
    ProviderStatus,
    RendererProvider,
    RenderRequest,
    RenderResult,
    ThreeDGenerationProvider,
    ThreeDRequest,
    ThreeDResult,
    _never,
    _noop_progress,
)
from pixel_rpg_studio.providers.blockout import object_boxes, write_obj


def _rng(seed: int, text: str = "") -> np.random.Generator:
    h = int(hashlib.sha256(f"{seed}|{text}".encode()).hexdigest()[:12], 16)
    return np.random.default_rng(h)


def _color(rng: np.random.Generator) -> tuple[int, int, int]:
    return tuple(int(v) for v in rng.integers(40, 230, 3))


def mock_reference(size: int = 512) -> Image.Image:
    """A simple character drawing on white (self-test / demo reference image)."""
    img = Image.new("RGB", (size, size), (255, 255, 255))
    d = ImageDraw.Draw(img)
    s = size / 100
    steel, dark, blue = (170, 175, 190), (60, 60, 75), (40, 80, 170)
    d.rectangle((42 * s, 52 * s, 49 * s, 90 * s), fill=dark)  # legs
    d.rectangle((51 * s, 52 * s, 58 * s, 90 * s), fill=dark)
    d.rectangle((38 * s, 26 * s, 62 * s, 55 * s), fill=steel)  # torso
    d.rectangle((38 * s, 40 * s, 62 * s, 44 * s), fill=blue)
    d.rectangle((31 * s, 27 * s, 37 * s, 52 * s), fill=steel)  # arms
    d.rectangle((63 * s, 27 * s, 69 * s, 52 * s), fill=steel)
    d.ellipse((41 * s, 8 * s, 59 * s, 26 * s), fill=steel)  # helmet
    d.rectangle((44 * s, 15 * s, 56 * s, 19 * s), fill=dark)  # visor
    d.rectangle((70 * s, 10 * s, 73 * s, 52 * s), fill=(210, 210, 225))  # sword
    d.rectangle((22 * s, 30 * s, 31 * s, 50 * s), fill=blue)  # shield
    return img


def mock_character_frame(size: int, seed: int, pose_image: Path | None, reference: Path | None) -> Image.Image:
    """Demo stand-in for the AI character frame: the OpenPose skeleton is
    thickened into a puppet silhouette and coloured from the reference image
    (sampled at the same canvas position). It follows the pose exactly, so
    the whole character pipeline can be tested without a GPU."""
    canvas = Image.new("RGB", (size, size), (255, 255, 255))
    if pose_image is None or not Path(pose_image).is_file():
        return canvas
    small = 192  # thicken the limbs at low resolution (fast), then scale up
    pose = np.array(Image.open(pose_image).convert("RGB").resize((small, small), Image.BOX)).astype(np.int32)
    mask = Image.fromarray(((pose.max(axis=2) > 20) * 255).astype(np.uint8), "L").copy()
    mask = mask.filter(ImageFilter.MaxFilter(7)).filter(ImageFilter.MaxFilter(7))
    m = np.array(mask.resize((size, size), Image.NEAREST)) > 0
    rng = _rng(seed)
    if reference is not None and Path(reference).is_file():
        ref = np.array(Image.open(reference).convert("RGB").resize((size, size), Image.NEAREST)).astype(np.int32)
        body = ref[ref.min(axis=2) < 235]
        base = np.median(body, axis=0) if len(body) else np.array(_color(rng))
        colors = np.where((ref.min(axis=2) < 235)[..., None], ref, base[None, None, :])
    else:
        colors = np.broadcast_to(np.array(_color(rng)), (size, size, 3))
    arr = np.array(canvas).astype(np.int32)
    arr[m] = colors[m]
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGB").copy()


def mock_object(size: int, seed: int, prompt: str) -> Image.Image:
    rng = _rng(seed, prompt)
    img = Image.new("RGB", (size, size), (255, 255, 255))
    d = ImageDraw.Draw(img)
    s = size / 100
    main, accent = _color(rng), _color(rng)
    shape = int(rng.integers(0, 3))
    if shape == 0:
        d.rectangle((30 * s, 35 * s, 70 * s, 80 * s), fill=main)
        d.rectangle((30 * s, 50 * s, 70 * s, 56 * s), fill=accent)
    elif shape == 1:
        d.ellipse((28 * s, 28 * s, 72 * s, 82 * s), fill=main)
        d.rectangle((44 * s, 18 * s, 56 * s, 30 * s), fill=accent)
    else:
        d.polygon([(50 * s, 12 * s), (58 * s, 70 * s), (42 * s, 70 * s)], fill=main)
        d.rectangle((36 * s, 70 * s, 64 * s, 75 * s), fill=accent)
        d.rectangle((47 * s, 75 * s, 53 * s, 90 * s), fill=(90, 60, 40))
    return img


def mock_texture(size: int, seed: int, prompt: str) -> Image.Image:
    rng = _rng(seed, prompt)
    base = np.array(_color(rng), dtype=np.float32)
    small = rng.random((16, 16, 1)).astype(np.float32)
    big = np.array(Image.fromarray((small[..., 0] * 255).astype(np.uint8)).resize((size, size), Image.BICUBIC), dtype=np.float32) / 255
    arr = np.clip(base[None, None, :] * (0.7 + 0.6 * big[..., None]), 0, 255).astype(np.uint8)
    return Image.fromarray(arr, "RGB")


def mock_background(width: int, height: int, seed: int, prompt: str) -> Image.Image:
    rng = _rng(seed, prompt)
    sky_top, sky_bottom = np.array(_color(rng)), np.array((230, 220, 200))
    t = np.linspace(0, 1, height)[:, None, None]
    arr = (sky_top * (1 - t) + sky_bottom * t).repeat(width, axis=1).astype(np.uint8)
    img = Image.fromarray(arr, "RGB").copy()  # copy: drawing on a shared buffer is lost (Pillow 12)
    d = ImageDraw.Draw(img)
    for layer, (col, base_h) in enumerate(((_color(rng), 0.55), (_color(rng), 0.7), (_color(rng), 0.85))):
        phase = float(rng.random()) * 6
        pts = [(x, height * base_h + math.sin(x / width * 6 + phase) * height * 0.06) for x in range(0, width + 8, 8)]
        d.polygon(pts + [(width, height), (0, height)], fill=col)
    return img


class MockImageProvider(ImageGenerationProvider):
    id = "mock_image"
    label = "Mock image generator (testing only)"

    def status(self) -> ProviderStatus:
        return ProviderStatus(True, "Mock image generator (no AI)")

    def generate(self, request: ImageRequest, out_dir: Path, progress: ProgressFn = _noop_progress,
                 cancelled: CancelledFn = _never) -> ImageResult:
        out_dir.mkdir(parents=True, exist_ok=True)
        w, h = request.width or 512, request.height or 512
        paths = []
        for n in range(max(1, request.count)):
            seed = request.seed + n
            wf = request.workflow
            if "character" in wf:
                img = mock_character_frame(min(w, h), seed, request.images.get("pose_image"), request.images.get("reference_image"))
            elif "background" in wf:
                img = mock_background(w, h, seed, request.prompt)
            elif "tile" in wf:
                img = mock_texture(min(w, h), seed, request.prompt)
            else:
                img = mock_object(min(w, h), seed, request.prompt)
            if request.reference_image is not None and "character" not in wf:
                ref = Image.open(request.reference_image).convert("RGB").resize(img.size)
                img = Image.blend(img, ref, 0.35)
            path = out_dir / f"mock_{seed}_{n}.png"
            img.save(path)
            paths.append(path)
            progress((n + 1) / max(1, request.count), "Mock generation")
        return ImageResult(paths, request.seed, self.id, model="mock", models={"image.checkpoint": "mock"},
                           workflow=request.workflow, params=dict(request.params))


class MockThreeDProvider(ThreeDGenerationProvider):
    id = "mock_3d"
    label = "Blockout mesh (placeholder, no AI)"

    def status(self) -> ProviderStatus:
        return ProviderStatus(True, "Procedural blockout mesh (no AI)")

    def generate(self, request: ThreeDRequest, out_dir: Path, progress: ProgressFn = _noop_progress,
                 cancelled: CancelledFn = _never) -> ThreeDResult:
        boxes = object_boxes(request.asset_kind)
        path = write_obj(boxes, Path(out_dir) / f"blockout_{request.asset_kind}_{request.seed}.obj")
        return ThreeDResult(path, self.id, request.seed, model="blockout", workflow="procedural_blockout")


class MockRenderer(RendererProvider):
    """2D stand-in for Blender: transforms the concept image per frame."""

    id = "mock_renderer"
    label = "Mock renderer (testing only, no Blender)"

    def status(self) -> ProviderStatus:
        return ProviderStatus(True, "Mock renderer (no Blender)")

    def prepare(self, request: PrepareRequest, progress: ProgressFn = _noop_progress, cancelled: CancelledFn = _never) -> PrepareResult:
        request.blend_path.parent.mkdir(parents=True, exist_ok=True)
        source = request.concept_image or request.texture_image
        data = {"mock": True, "texture": str(source) if source else "", "model": str(request.model_path)}
        request.blend_path.write_text(json.dumps(data), encoding="utf-8")
        report = {"ok": True, "warnings": ["Mock renderer: no real 3D processing was performed."],
                  "mesh": {}}
        return PrepareResult(request.blend_path, report, self.id)

    def render(self, request: RenderRequest, progress: ProgressFn = _noop_progress, cancelled: CancelledFn = _never) -> RenderResult:
        data = json.loads(Path(request.blend_path).read_text(encoding="utf-8"))
        tex = Path(data.get("texture") or "")
        if tex.is_file():
            base = crop_to_content(remove_background(Image.open(tex).convert("RGBA")))
        else:
            base = Image.new("RGBA", (40, 80), (150, 150, 160, 255))
        W, H = request.width, request.height
        scale = 0.8 * min(W / base.width, H / base.height)
        base = base.resize((max(1, int(base.width * scale)), max(1, int(base.height * scale))), Image.NEAREST)
        frames: dict[tuple[str, str], dict[int, Path]] = {}
        only = set(request.only_frames) if request.only_frames else None
        total = sum(a.frames for a in request.animations) * len(request.directions)
        done = 0
        for anim in request.animations:
            for key, yaw in request.directions:
                for i in range(anim.frames):
                    if only is not None and (anim.name, key, i) not in only:
                        continue
                    if cancelled():
                        from pixel_rpg_studio.core.errors import JobCancelledError

                        raise JobCancelledError()
                    img = base
                    if key in ("w", "e", "sw", "se", "nw", "ne"):
                        img = img.resize((max(1, int(img.width * 0.6)), img.height), Image.NEAREST)
                        if key in ("e", "se", "ne"):
                            img = img.transpose(Image.FLIP_LEFT_RIGHT)
                    if key in ("n", "nw", "ne"):
                        arr = np.array(img)
                        arr[..., :3] = (arr[..., :3] * 0.7).astype(np.uint8)
                        img = Image.fromarray(arr, "RGBA")
                    canvas = Image.new("RGBA", (W, H), (0, 0, 0, 0))
                    x = (W - img.width) // 2
                    y = H - img.height - int(H * 0.05)
                    canvas.alpha_composite(img, (max(0, x), max(0, y)))
                    path = Path(request.output_dir) / anim.name / key / f"frame_{i:03d}.png"
                    path.parent.mkdir(parents=True, exist_ok=True)
                    canvas.save(path)
                    frames.setdefault((anim.name, key), {})[i] = path
                    done += 1
                    progress(done / max(1, total), f"Mock render {done}/{total}")
        return RenderResult(frames, request.ortho_scale or 1.0, request.target or [0.0, 0.0, 1.0], self.id,
                            {"ok": True, "warnings": ["Mock renderer output"]})


class BuiltinImageProcessor(ImageProcessingProvider):
    id = "builtin"
    label = "Built-in deterministic pixel processing"

    def status(self) -> ProviderStatus:
        return ProviderStatus(True, "Built-in (Pillow/numpy)")

    def process(self, image_path: Path, steps: list[dict], out_path: Path) -> Path:
        from pixel_rpg_studio.imaging.pixel import run_pipeline

        start = time.time()
        result, _ = run_pipeline(Image.open(image_path), steps)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        result.save(out_path)
        self.last_duration = time.time() - start
        return out_path
