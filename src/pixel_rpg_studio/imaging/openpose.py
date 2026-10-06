"""Draw OpenPose (COCO-18) skeleton images for ControlNet OpenPose.

Colours, limb order and drawing style follow the reference implementation
used to train OpenPose ControlNets (``controlnet_aux`` ``draw_bodypose``), so
generated pose images look like the training data: coloured limbs (at 60 %
intensity) and full-colour joint dots on black.
"""

from __future__ import annotations

import math
from typing import Sequence

from PIL import Image, ImageDraw

JOINTS = ["nose", "neck", "r_shoulder", "r_elbow", "r_wrist", "l_shoulder", "l_elbow", "l_wrist",
          "r_hip", "r_knee", "r_ankle", "l_hip", "l_knee", "l_ankle", "r_eye", "l_eye", "r_ear", "l_ear"]

# 1-based joint pairs, first 17 are drawn (same as controlnet_aux).
LIMBS = [(2, 3), (2, 6), (3, 4), (4, 5), (6, 7), (7, 8), (2, 9), (9, 10), (10, 11), (2, 12), (12, 13), (13, 14),
         (2, 1), (1, 15), (15, 17), (1, 16), (16, 18)]
COLORS = [(255, 0, 0), (255, 85, 0), (255, 170, 0), (255, 255, 0), (170, 255, 0), (85, 255, 0), (0, 255, 0),
          (0, 255, 85), (0, 255, 170), (0, 255, 255), (0, 170, 255), (0, 85, 255), (0, 0, 255), (85, 0, 255),
          (170, 0, 255), (255, 0, 255), (255, 0, 170), (255, 0, 85)]


def draw_pose(joints: dict[str, Sequence[float] | None], width: int, height: int, scale: float | None = None) -> Image.Image:
    """Render normalised joint positions (0..1, origin top-left) as an OpenPose image.

    Missing joints (``None``) and limbs touching them are skipped, which is
    how OpenPose encodes occluded face points (e.g. no nose in a back view).
    """
    scale = scale if scale is not None else max(width, height) / 512.0
    stick = max(2.0, 4.0 * scale)
    radius = max(2.0, 4.0 * scale)
    img = Image.new("RGB", (width, height), (0, 0, 0))
    draw = ImageDraw.Draw(img)

    def pt(name: str) -> tuple[float, float] | None:
        v = joints.get(name)
        if v is None:
            return None
        return float(v[0]) * width, float(v[1]) * height

    for i, (a, b) in enumerate(LIMBS):
        pa, pb = pt(JOINTS[a - 1]), pt(JOINTS[b - 1])
        if pa is None or pb is None:
            continue
        color = tuple(int(c * 0.6) for c in COLORS[i])
        # ellipse along the limb, like cv2.ellipse2Poly in the reference code
        mx, my = (pa[0] + pb[0]) / 2, (pa[1] + pb[1]) / 2
        length = math.hypot(pb[0] - pa[0], pb[1] - pa[1]) / 2
        ang = math.atan2(pb[1] - pa[1], pb[0] - pa[0])
        poly = []
        for k in range(0, 360, 15):
            t = math.radians(k)
            x, y = length * math.cos(t), stick * math.sin(t)
            poly.append((mx + x * math.cos(ang) - y * math.sin(ang), my + x * math.sin(ang) + y * math.cos(ang)))
        draw.polygon(poly, fill=color)
    for i, name in enumerate(JOINTS):
        p = pt(name)
        if p is not None:
            draw.ellipse((p[0] - radius, p[1] - radius, p[0] + radius, p[1] + radius), fill=COLORS[i])
    return img


def pose_bbox(joints: dict[str, Sequence[float] | None]) -> tuple[float, float, float, float] | None:
    pts = [v for v in joints.values() if v is not None]
    if not pts:
        return None
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return min(xs), min(ys), max(xs), max(ys)
