"""Deterministic, procedural frame-based VFX.

Current local image models cannot reliably produce *temporally coherent*
effect frames, so the default VFX pipeline is a seeded particle simulation
rendered directly at pixel resolution. Results are fully reproducible from
(preset, parameters, seed). AI generation can still be used for concept /
palette inspiration, and frames can be imported and post-processed.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field

import numpy as np
from PIL import Image, ImageDraw

from pixel_rpg_studio.project.style import hex_to_rgb

PRESETS: dict[str, dict] = {
    "fire": {"ramp": ["#fff3b0", "#f6c75b", "#f08a4b", "#d8483c", "#5a1a22"], "kind": "emitter", "speed": 0.9, "spread": 0.35, "life": 0.7, "size": 0.16, "count": 40, "gravity": -0.6},
    "smoke": {"ramp": ["#b4adc4", "#7a7090", "#4a4061", "#2a2438"], "kind": "emitter", "speed": 0.45, "spread": 0.5, "life": 1.0, "size": 0.2, "count": 18, "gravity": -0.3, "grow": 1.8},
    "explosion": {"ramp": ["#ffffff", "#fff3b0", "#f6c75b", "#f08a4b", "#d8483c", "#4a4061"], "kind": "burst", "speed": 1.6, "spread": 1.0, "life": 0.8, "size": 0.18, "count": 46, "gravity": 0.0},
    "magic": {"ramp": ["#ffffff", "#e0b0ff", "#a86cc4", "#6a3c8c", "#3a2252"], "kind": "orbit", "speed": 1.0, "spread": 0.3, "life": 0.9, "size": 0.08, "count": 30, "gravity": 0.0},
    "lightning": {"ramp": ["#ffffff", "#d8f4ff", "#7cc8e8", "#3a8fc4"], "kind": "lightning", "count": 2},
    "soul": {"ramp": ["#e8fff6", "#9ff0d0", "#4fbfa0", "#1d5a5a"], "kind": "emitter", "speed": 0.5, "spread": 0.25, "life": 1.0, "size": 0.1, "count": 22, "gravity": -0.4, "wobble": 1.0},
    "heal": {"ramp": ["#ffffff", "#d6ffb0", "#9fd36a", "#55a04b", "#2f6a3c"], "kind": "rise", "speed": 0.6, "spread": 0.8, "life": 1.0, "size": 0.07, "count": 20, "gravity": -0.2},
    "hit": {"ramp": ["#ffffff", "#fff3b0", "#f6c75b", "#d8483c"], "kind": "burst", "speed": 2.0, "spread": 1.0, "life": 0.45, "size": 0.1, "count": 14, "gravity": 0.0, "streak": True},
    "impact": {"ramp": ["#ffffff", "#eeeaf2", "#b4adc4", "#7a7090"], "kind": "ring", "speed": 1.3, "life": 0.7, "size": 0.06, "count": 1},
}

DIRECTIONS = {"up": (0.0, -1.0), "down": (0.0, 1.0), "left": (-1.0, 0.0), "right": (1.0, 0.0), "radial": (0.0, 0.0)}


@dataclass
class VFXParams:
    preset: str = "fire"
    frames: int = 8
    width: int = 32
    height: int = 32
    loop: bool = True
    fps: int = 12
    direction: str = "up"
    seed: int = 1
    intensity: float = 1.0
    ramp: list[str] = field(default_factory=list)  # override color ramp (bright -> dark)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class _Particle:
    t0: float
    x: float
    y: float
    vx: float
    vy: float
    life: float
    size: float
    phase: float


def _ramp_color(ramp: list[tuple[int, int, int]], age: float) -> tuple[int, int, int]:
    i = min(len(ramp) - 1, int(age * len(ramp)))
    return ramp[i]


def _spawn(cfg: dict, p: VFXParams, rng: np.random.Generator, period: float) -> list[_Particle]:
    count = max(1, int(cfg.get("count", 20) * p.intensity))
    dx, dy = DIRECTIONS.get(p.direction, (0.0, -1.0))
    particles = []
    kind = cfg["kind"]
    for _ in range(count):
        t0 = float(rng.random()) * period if (p.loop and kind in ("emitter", "rise", "orbit")) else float(rng.random()) * 0.15
        speed = cfg.get("speed", 1.0) * (0.6 + 0.8 * float(rng.random()))
        if kind in ("burst",) or p.direction == "radial":
            ang = float(rng.random()) * 2 * math.pi
            vx, vy = math.cos(ang) * speed, math.sin(ang) * speed
            x, y = 0.5, 0.5
        else:
            spread = cfg.get("spread", 0.3)
            jitter = (float(rng.random()) - 0.5) * spread
            vx, vy = dx * speed + (-dy) * jitter, dy * speed + dx * jitter
            # spawn at the opposite side of travel
            x = 0.5 - dx * 0.35 + (float(rng.random()) - 0.5) * spread * (abs(dy) + 0.2)
            y = 0.5 - dy * 0.35 + (float(rng.random()) - 0.5) * spread * (abs(dx) + 0.2)
            if kind == "rise":
                x = 0.5 + (float(rng.random()) - 0.5) * cfg.get("spread", 0.8)
                y = 0.8 - float(rng.random()) * 0.2
        life = cfg.get("life", 1.0) * (0.6 + 0.4 * float(rng.random()))
        size = cfg.get("size", 0.1) * (0.6 + 0.8 * float(rng.random()))
        particles.append(_Particle(t0, x, y, vx, vy, life, size, float(rng.random()) * 2 * math.pi))
    return particles


def _draw_particle(draw: ImageDraw.ImageDraw, x: float, y: float, r: float, color) -> None:
    if r < 0.75:
        draw.point((round(x), round(y)), fill=color)
    else:
        draw.ellipse((round(x - r), round(y - r), round(x + r), round(y + r)), fill=color)


def _lightning_frame(p: VFXParams, ramp, frame: int) -> Image.Image:
    img = Image.new("RGBA", (p.width, p.height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    rng = np.random.default_rng(p.seed * 1000 + frame)
    # Visible on most frames; occasional dark frame for flicker.
    if not p.loop and frame >= p.frames - 1:
        return img
    dx, dy = DIRECTIONS.get(p.direction, (0.0, 1.0))
    if (dx, dy) == (0.0, 0.0) or p.direction == "up":
        dx, dy = 0.0, 1.0
    vertical = abs(dy) >= abs(dx)
    for bolt in range(PRESETS["lightning"]["count"]):
        steps = 8
        pts = []
        for s in range(steps + 1):
            t = s / steps
            off = (float(rng.random()) - 0.5) * 0.35 * (1 if 0 < s < steps else 0)
            if vertical:
                pts.append(((0.5 + off) * (p.width - 1), t * (p.height - 1)))
            else:
                pts.append((t * (p.width - 1), (0.5 + off) * (p.height - 1)))
        color = ramp[min(len(ramp) - 1, bolt + (frame % 2))]
        draw.line(pts, fill=(*color, 255), width=1)
        if bolt == 0:
            glow = ramp[min(len(ramp) - 1, 2)]
            draw.line([(x + 1, y) for x, y in pts], fill=(*glow, 255), width=1)
    return img


def _ring_frame(p: VFXParams, ramp, t: float) -> Image.Image:
    img = Image.new("RGBA", (p.width, p.height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    if t > 1:
        return img
    r = t * min(p.width, p.height) * 0.48
    color = _ramp_color(ramp, t)
    cx, cy = (p.width - 1) / 2, (p.height - 1) / 2
    draw.ellipse((round(cx - r), round(cy - r), round(cx + r), round(cy + r)), outline=(*color, 255), width=max(1, round((1 - t) * 3)))
    return img


def render_vfx(p: VFXParams) -> list[Image.Image]:
    """Render all frames of an effect. Deterministic for identical params."""
    if p.preset not in PRESETS:
        raise ValueError(f"Unknown VFX preset '{p.preset}'. Available: {', '.join(PRESETS)}")
    if p.frames < 1 or p.frames > 256:
        raise ValueError("Frame count must be between 1 and 256.")
    cfg = PRESETS[p.preset]
    ramp = [hex_to_rgb(c) for c in (p.ramp or cfg["ramp"])]
    kind = cfg["kind"]
    if kind == "lightning":
        return [_lightning_frame(p, ramp, f) for f in range(p.frames)]
    if kind == "ring":
        return [_ring_frame(p, ramp, f / max(1, p.frames - 1)) for f in range(p.frames)]

    rng = np.random.default_rng(p.seed)
    period = 1.0
    particles = _spawn(cfg, p, rng, period)
    scale = min(p.width, p.height)
    frames = []
    for f in range(p.frames):
        t = f / p.frames if p.loop else f / max(1, p.frames - 1)
        img = Image.new("RGBA", (p.width, p.height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        # In loop mode also draw the previous cycle's particles -> seamless loop.
        cycles = (0.0, -period) if p.loop else (0.0,)
        drawable = []
        for part in particles:
            for c in cycles:
                age_t = t - (part.t0 + c)
                if age_t < 0 or age_t > part.life:
                    continue
                age = age_t / part.life
                g = cfg.get("gravity", 0.0)
                x = part.x + part.vx * age_t * 0.5
                y = part.y + part.vy * age_t * 0.5 + 0.5 * g * age_t * age_t * 0.5
                if cfg.get("wobble"):
                    x += math.sin(part.phase + age_t * 9) * 0.04 * cfg["wobble"]
                if kind == "orbit":
                    ang = part.phase + age_t * 6 * part.vx
                    rad = 0.12 + 0.25 * age
                    x, y = 0.5 + math.cos(ang) * rad, 0.5 + math.sin(ang) * rad * 0.8
                size = part.size * (1 + (cfg.get("grow", 1.0) - 1) * age) * (1 - 0.5 * age if kind != "emitter" else 1 - 0.6 * age)
                drawable.append((age, x * (p.width - 1), y * (p.height - 1), size * scale * 0.5, _ramp_color(ramp, age)))
        # draw old (dark) particles first, bright on top
        for age, x, y, r, color in sorted(drawable, key=lambda d: -d[0]):
            _draw_particle(draw, x, y, r, (*color, 255))
            if cfg.get("streak") and r >= 0.5:
                draw.line((round(p.width / 2), round(p.height / 2), round(x), round(y)), fill=(*color, 255))
        frames.append(img)
    return frames
