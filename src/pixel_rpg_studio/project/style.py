"""Global project style: the single source of truth for visual consistency.

Stored in ``<project>/style/style.json`` and ``style/palette.json``. Every
pipeline reads it so that all assets share resolution, perspective, lighting,
outline, palette and shading rules.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

from pixel_rpg_studio.core.config import read_json, write_json_atomic

PERSPECTIVES = {
    # key: (label, camera elevation in degrees, extra yaw, prompt words)
    "side": ("Side view (platformer)", 0.0, 0.0, "side view"),
    "three_quarter": ("3/4 top-down (classic RPG)", 30.0, 0.0, "three-quarter top-down view"),
    "top_down": ("Top-down", 75.0, 0.0, "top-down view"),
    "isometric": ("Isometric", 30.0, 45.0, "isometric view"),
}

LIGHT_DIRECTIONS = {
    # key: (label, azimuth degrees in camera space (0 = from camera), elevation)
    "top_left": ("Top-left", -45.0, 50.0),
    "top_right": ("Top-right", 45.0, 50.0),
    "top": ("Top", 0.0, 80.0),
    "front": ("Front", 0.0, 25.0),
    "left": ("Left", -90.0, 30.0),
    "right": ("Right", 90.0, 30.0),
}

OUTLINE_STYLES = ("none", "dark", "black", "selective")
SHADING_STYLES = ("cel", "soft", "flat")
SHADOW_STYLES = ("none", "drop", "contact")
DOWNSCALE_METHODS = ("mode", "nearest", "box")
PROPORTIONS = {
    "chibi": "chibi proportions, big head",
    "stylized": "stylized proportions",
    "heroic": "heroic proportions",
    "realistic": "realistic proportions",
}

DEFAULT_PALETTE = [
    # "Studio Default 32" - an original general-purpose fantasy palette.
    "#0d0b14", "#2a2438", "#4a4061", "#7a7090", "#b4adc4", "#eeeaf2",
    "#3b1f1a", "#6b3a2a", "#a0603f", "#d39a6a", "#f2d0a4",
    "#5a1a22", "#9c2a2f", "#d8483c", "#f08a4b", "#f6c75b",
    "#1d3b2a", "#2f6a3c", "#55a04b", "#9fd36a",
    "#14304a", "#1f5a85", "#3a8fc4", "#7cc8e8",
    "#3a2252", "#6a3c8c", "#a86cc4",
    "#4a4a3a", "#7c7a5c", "#b8b48a",
    "#8a5a2a", "#c89a3a",
]


@dataclass
class StyleDefinition:
    name: str = "Default Style"
    # Resolution
    sprite_width: int = 48
    sprite_height: int = 48
    tile_size: int = 16
    pixels_per_unit: int = 16
    render_scale: int = 8  # Blender renders at sprite size * render_scale before deterministic downscale
    # Camera / lighting
    perspective: str = "three_quarter"
    camera_elevation_deg: float | None = None  # None = use perspective default
    lighting_direction: str = "top_left"
    # Look
    outline: str = "dark"
    shading_style: str = "cel"
    shading_bands: int = 3
    shadow_style: str = "none"
    contrast: float = 1.0
    saturation: float = 1.0
    max_colors: int = 24
    use_palette: bool = True
    dithering: bool = False
    anti_aliasing: bool = False
    downscale_method: str = "mode"
    background_treatment: str = "transparent"
    proportions: str = "stylized"
    # Prompting
    positive_prompt: str = "pixel art, 16-bit RPG style, clean shapes, strong silhouette, limited palette"
    negative_prompt: str = (
        "blurry, photo, photorealistic, 3d render, noise, jpeg artifacts, text, watermark, signature, "
        "cropped, multiple views, deformed"
    )
    extra: dict[str, Any] = field(default_factory=dict)

    def elevation(self) -> float:
        if self.camera_elevation_deg is not None:
            return float(self.camera_elevation_deg)
        return PERSPECTIVES.get(self.perspective, PERSPECTIVES["three_quarter"])[1]

    def perspective_yaw(self) -> float:
        return PERSPECTIVES.get(self.perspective, PERSPECTIVES["three_quarter"])[2]

    def perspective_prompt(self) -> str:
        return PERSPECTIVES.get(self.perspective, PERSPECTIVES["three_quarter"])[3]

    def light(self) -> tuple[float, float]:
        _, az, el = LIGHT_DIRECTIONS.get(self.lighting_direction, LIGHT_DIRECTIONS["top_left"])
        return az, el

    def validate(self) -> list[str]:
        problems = []
        if not (4 <= self.sprite_width <= 1024 and 4 <= self.sprite_height <= 1024):
            problems.append("Sprite size must be between 4 and 1024 pixels.")
        if not (4 <= self.tile_size <= 256):
            problems.append("Tile size must be between 4 and 256 pixels.")
        if not (1 <= self.render_scale <= 32):
            problems.append("Render scale must be between 1 and 32.")
        if not (2 <= self.max_colors <= 256):
            problems.append("Max colors must be between 2 and 256.")
        if self.perspective not in PERSPECTIVES:
            problems.append(f"Unknown perspective '{self.perspective}'.")
        if self.lighting_direction not in LIGHT_DIRECTIONS:
            problems.append(f"Unknown lighting direction '{self.lighting_direction}'.")
        if self.outline not in OUTLINE_STYLES:
            problems.append(f"Unknown outline style '{self.outline}'.")
        if self.downscale_method not in DOWNSCALE_METHODS:
            problems.append(f"Unknown downscale method '{self.downscale_method}'.")
        if not (1 <= self.shading_bands <= 8):
            problems.append("Shading bands must be between 1 and 8.")
        return problems

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "StyleDefinition":
        style = cls()
        if not data:
            return style
        names = {f.name for f in fields(cls)}
        for key, value in data.items():
            if key in names:
                setattr(style, key, value)
            else:
                style.extra[key] = value
        return style


@dataclass
class Palette:
    name: str = "Studio Default 32"
    colors: list[str] = field(default_factory=lambda: list(DEFAULT_PALETTE))

    def rgb(self) -> list[tuple[int, int, int]]:
        return [hex_to_rgb(c) for c in self.colors]

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "colors": list(self.colors)}

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "Palette":
        if not data or not data.get("colors"):
            return cls()
        colors = [normalize_hex(c) for c in data["colors"]]
        return cls(name=str(data.get("name", "Palette")), colors=colors)

    @classmethod
    def from_rgb(cls, name: str, colors: list[tuple[int, int, int]]) -> "Palette":
        return cls(name=name, colors=[rgb_to_hex(c) for c in colors])


def hex_to_rgb(value: str) -> tuple[int, int, int]:
    value = normalize_hex(value)
    return int(value[1:3], 16), int(value[3:5], 16), int(value[5:7], 16)


def rgb_to_hex(rgb: tuple[int, int, int] | list[int]) -> str:
    r, g, b = (int(x) for x in rgb[:3])
    return f"#{r:02x}{g:02x}{b:02x}"


def normalize_hex(value: str) -> str:
    v = str(value).strip().lstrip("#").lower()
    if len(v) == 3:
        v = "".join(ch * 2 for ch in v)
    if len(v) == 8:  # drop alpha
        v = v[:6]
    if len(v) != 6 or any(ch not in "0123456789abcdef" for ch in v):
        raise ValueError(f"Invalid color '{value}'")
    return "#" + v


def load_style(style_dir: Path) -> tuple[StyleDefinition, Palette]:
    style = StyleDefinition.from_dict(read_json(style_dir / "style.json", {}))
    palette = Palette.from_dict(read_json(style_dir / "palette.json", {}))
    return style, palette


def save_style(style_dir: Path, style: StyleDefinition, palette: Palette) -> None:
    style_dir.mkdir(parents=True, exist_ok=True)
    write_json_atomic(style_dir / "style.json", style.to_dict())
    write_json_atomic(style_dir / "palette.json", palette.to_dict())
