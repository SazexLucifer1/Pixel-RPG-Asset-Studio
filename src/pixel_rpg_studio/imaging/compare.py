"""Consistency checks between a character's master reference and new frames."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageDraw

from pixel_rpg_studio.imaging.pixel import as_array, content_bbox, scale_nearest, to_rgba


@dataclass
class ConsistencyReport:
    palette_match: float  # 0..1 fraction of frame pixels whose colour exists in the master
    height_ratio: float  # frame silhouette height / master silhouette height
    width_ratio: float
    color_histogram_similarity: float  # 0..1 histogram intersection

    @property
    def ok(self) -> bool:
        return self.palette_match >= 0.8 and 0.75 <= self.height_ratio <= 1.25 and self.color_histogram_similarity >= 0.5

    def summary(self) -> str:
        return (
            f"Palette match {self.palette_match:.0%}, height {self.height_ratio:.2f}x, "
            f"width {self.width_ratio:.2f}x, colour similarity {self.color_histogram_similarity:.0%}"
            + ("" if self.ok else "  - check this frame")
        )


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


def compare_to_master(master: Image.Image, frame: Image.Image) -> ConsistencyReport:
    mc, fc = _opaque_colors(master), _opaque_colors(frame)
    if len(fc) == 0 or len(mc) == 0:
        return ConsistencyReport(0.0, 0.0, 0.0, 0.0)
    master_set = {tuple(c) for c in np.unique(mc, axis=0)}
    in_master = np.array([tuple(c) in master_set for c in fc])
    palette_match = float(in_master.mean())
    mb, fb = content_bbox(master), content_bbox(frame)
    mh, mw = (mb[3] - mb[1]), (mb[2] - mb[0])
    fh, fw = (fb[3] - fb[1]), (fb[2] - fb[0])
    hist_sim = float(np.minimum(_hist(mc), _hist(fc)).sum())
    return ConsistencyReport(palette_match, fh / max(1, mh), fw / max(1, mw), hist_sim)


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
