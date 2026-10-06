"""Consistency checks between a character's master reference and new frames."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageDraw

from pixel_rpg_studio.imaging.pixel import as_array, content_bbox, scale_nearest, to_rgba


@dataclass
class ConsistencyReport:
    """How well a frame matches the character's references.

    * colours are compared with the master reference (the approved design)
    * size is compared with the *render reference* (first rendered front frame)
      because the concept image and renders are framed differently
    """

    palette_match: float  # 0..1 fraction of frame pixels whose colour exists in the master
    color_histogram_similarity: float  # 0..1 histogram intersection with the master
    height_ratio: float | None = None  # frame silhouette height / render reference height
    width_ratio: float | None = None  # informative only: width depends on direction and pose

    @property
    def ok(self) -> bool:
        size_ok = self.height_ratio is None or 0.7 <= self.height_ratio <= 1.3
        return self.palette_match >= 0.7 and size_ok

    def summary(self) -> str:
        parts = [f"Palette match {self.palette_match:.0%}", f"colour similarity {self.color_histogram_similarity:.0%}"]
        if self.height_ratio is not None:
            parts.append(f"height {self.height_ratio:.2f}x of reference render")
        return ", ".join(parts) + ("" if self.ok else "  - check this frame")


def _opaque_colors(img: Image.Image) -> np.ndarray:
    arr = as_array(img)
    return arr[arr[..., 3] > 0][:, :3]


def _hist(colors: np.ndarray, bins: int = 8) -> np.ndarray:
    if len(colors) == 0:
        return np.zeros(bins**3)
    q = (colors // (256 // bins)).astype(int)
    idx = q[:, 0] * bins * bins + q[:, 1] * bins + q[:, 2]
    h = np.bincount(idx, minlength=bins**3).astype(np.float64)
    return h / h.sum()


def compare_to_master(master: Image.Image, frame: Image.Image, size_reference: Image.Image | None = None) -> ConsistencyReport:
    mc, fc = _opaque_colors(master), _opaque_colors(frame)
    if len(fc) == 0 or len(mc) == 0:
        return ConsistencyReport(0.0, 0.0, 0.0 if size_reference is not None else None)
    master_set = {tuple(c) for c in np.unique(mc, axis=0)}
    palette_match = float(np.mean([tuple(c) in master_set for c in fc]))
    hist_sim = float(np.minimum(_hist(mc), _hist(fc)).sum())
    height_ratio = width_ratio = None
    if size_reference is not None:
        rb, fb = content_bbox(size_reference), content_bbox(frame)
        if rb and fb:
            height_ratio = (fb[3] - fb[1]) / max(1, rb[3] - rb[1])
            width_ratio = (fb[2] - fb[0]) / max(1, rb[2] - rb[0])
    return ConsistencyReport(palette_match, hist_sim, height_ratio, width_ratio)


def side_by_side(images: list[Image.Image], labels: list[str] | None = None, scale: int = 4, gap: int = 8, background=(40, 40, 48, 255)) -> Image.Image:
    """Compose images next to each other (nearest-neighbour scaled) for comparison."""
    scaled = [scale_nearest(to_rgba(i), scale) for i in images]
    label_h = 14 if labels else 0
    width = sum(i.width for i in scaled) + gap * (len(scaled) + 1)
    height = max(i.height for i in scaled) + gap * 2 + label_h
    out = Image.new("RGBA", (width, height), background)
    draw = ImageDraw.Draw(out)
    x = gap
    for n, img in enumerate(scaled):
        out.alpha_composite(img, (x, gap + label_h))
        if labels and n < len(labels):
            draw.text((x, 2), labels[n], fill=(230, 230, 240, 255))
        x += img.width + gap
    return out
