"""Deterministic pixel-art post-processing.

Every function is pure (same input + parameters -> identical output), so a
processing run can be recorded as a list of steps and replayed exactly.

A pipeline is a list of step dicts, e.g.::

    [{"op": "remove_background", "tolerance": 30},
     {"op": "crop_to_content"},
     {"op": "fit_canvas", "width": 48, "height": 48, "anchor": "bottom", "margin": 1},
     {"op": "quantize", "palette": ["#000000", ...]},
     {"op": "outline", "style": "dark"}]

Use :func:`run_pipeline` to execute it.
"""

from __future__ import annotations

from typing import Any, Callable, Iterable, Sequence

import numpy as np
from PIL import Image, ImageDraw, ImageEnhance

RGB = tuple[int, int, int]


# --------------------------------------------------------------------- basics
def to_rgba(img: Image.Image) -> Image.Image:
    return img if img.mode == "RGBA" else img.convert("RGBA")


def as_array(img: Image.Image) -> np.ndarray:
    return np.array(to_rgba(img), dtype=np.uint8)


def from_array(arr: np.ndarray) -> Image.Image:
    return Image.fromarray(np.ascontiguousarray(arr, dtype=np.uint8), "RGBA")


def has_transparency(img: Image.Image, min_fraction: float = 0.01) -> bool:
    arr = as_array(img)
    return float((arr[..., 3] < 250).mean()) >= min_fraction


def alpha_threshold(img: Image.Image, threshold: int = 128) -> Image.Image:
    """Make alpha strictly binary (pixel art has no semi-transparent edges)."""
    arr = as_array(img).copy()
    opaque = arr[..., 3] >= threshold
    arr[..., 3] = np.where(opaque, 255, 0)
    arr[~opaque, :3] = 0
    return from_array(arr)


def remove_background(img: Image.Image, tolerance: int = 32, force: bool = False) -> Image.Image:
    """Remove a plain background by flood-filling from the image border.

    If the image already has meaningful transparency it is returned unchanged
    (unless ``force``). The background color is the median of the border
    pixels; connected pixels within ``tolerance`` (max channel difference) are
    made transparent. Interior regions with the same color are kept, which
    protects e.g. white eyes on a white background.
    """
    img = to_rgba(img)
    if not force and has_transparency(img):
        return img
    arr = as_array(img).copy()
    h, w = arr.shape[:2]
    border = np.concatenate([arr[0, :, :3], arr[-1, :, :3], arr[:, 0, :3], arr[:, -1, :3]])
    bg = np.median(border, axis=0)
    diff = np.abs(arr[..., :3].astype(np.int16) - bg.astype(np.int16)).max(axis=2)
    candidate = diff <= tolerance
    # Flood fill over the candidate mask from every border seed (PIL's C
    # implementation; fast even for 2048px images).
    mask = Image.fromarray(np.where(candidate, 255, 0).astype(np.uint8), "L")
    seeds = [(x, y) for x in range(w) for y in (0, h - 1)] + [(x, y) for y in range(h) for x in (0, w - 1)]
    for seed in seeds:
        if mask.getpixel(seed) == 255:
            ImageDraw.floodfill(mask, seed, 128, thresh=0)
    visited = np.array(mask) == 128
    arr[visited] = 0
    return from_array(arr)


def content_bbox(img: Image.Image, alpha_min: int = 1) -> tuple[int, int, int, int] | None:
    arr = as_array(img)
    ys, xs = np.nonzero(arr[..., 3] >= alpha_min)
    if len(xs) == 0:
        return None
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def crop_to_content(img: Image.Image, padding: int = 0) -> Image.Image:
    bbox = content_bbox(img)
    if bbox is None:
        return to_rgba(img)
    x0, y0, x1, y1 = bbox
    w, h = img.size
    return to_rgba(img).crop((max(0, x0 - padding), max(0, y0 - padding), min(w, x1 + padding), min(h, y1 + padding)))


# ------------------------------------------------------------------ resizing
def scale_nearest(img: Image.Image, factor: int) -> Image.Image:
    """Integer nearest-neighbour upscale (for previews and exports)."""
    factor = max(1, int(factor))
    return to_rgba(img).resize((img.width * factor, img.height * factor), Image.NEAREST)


def downscale(img: Image.Image, width: int, height: int, method: str = "mode", alpha_coverage: float = 0.5) -> Image.Image:
    """Reduce an image to an exact pixel size.

    ``mode``    - each output pixel takes the most common color of its source
                  block (best for pixel art: no blending, keeps flat areas).
    ``nearest`` - plain nearest neighbour.
    ``box``     - area average followed by alpha thresholding.
    """
    img = to_rgba(img)
    width, height = max(1, int(width)), max(1, int(height))
    if img.size == (width, height):
        return img.copy()
    if method == "nearest":
        return alpha_threshold(img.resize((width, height), Image.NEAREST))
    if method == "box":
        return alpha_threshold(img.resize((width, height), Image.BOX), int(255 * alpha_coverage))
    if method != "mode":
        raise ValueError(f"Unknown downscale method '{method}'")
    # Block size k: resample (nearest) to exactly (width*k, height*k), max 8x8 blocks.
    k = max(1, min(8, img.width // width, img.height // height))
    if k == 1:
        return alpha_threshold(img.resize((width, height), Image.NEAREST))
    if img.width > width * 8 or img.height > height * 8:
        img = img.resize((width * 8, height * 8), Image.BOX)
        k = 8
    src = np.array(img.resize((width * k, height * k), Image.NEAREST), dtype=np.uint8)
    blocks = src.reshape(height, k, width, k, 4).transpose(0, 2, 1, 3, 4).reshape(height * width, k * k, 4)
    opaque = blocks[..., 3] >= 128
    q = (blocks[..., :3] >> 3).astype(np.int32)
    keys = (q[..., 0] << 10) | (q[..., 1] << 5) | q[..., 2]
    keys = np.where(opaque, keys, -1)
    # count occurrences of each key within its block (O(k^4) per block, k<=8)
    counts = (keys[:, :, None] == keys[:, None, :]).sum(axis=2)
    counts = np.where(opaque, counts, -1)
    best = counts.argmax(axis=1)
    idx = np.arange(blocks.shape[0])
    colors = blocks[idx, best, :3]
    coverage = opaque.mean(axis=1)
    out = np.zeros((height * width, 4), dtype=np.uint8)
    keep = coverage >= alpha_coverage
    out[keep, :3] = colors[keep]
    out[keep, 3] = 255
    return from_array(out.reshape(height, width, 4))


def fit_canvas(
    img: Image.Image,
    width: int,
    height: int,
    anchor: str = "center",
    margin: int = 0,
    method: str = "mode",
    allow_upscale: bool = False,
) -> Image.Image:
    """Place content on a fixed-size transparent canvas.

    Content larger than the canvas (minus margin) is downscaled preserving the
    aspect ratio. ``anchor="bottom"`` aligns the feet of characters so frames
    and different assets line up.
    """
    img = to_rgba(img)
    avail_w, avail_h = max(1, width - 2 * margin), max(1, height - 2 * margin)
    scale = min(avail_w / img.width, avail_h / img.height)
    if scale < 1 or (allow_upscale and scale > 1):
        new_w, new_h = max(1, round(img.width * scale)), max(1, round(img.height * scale))
        img = downscale(img, new_w, new_h, method) if scale < 1 else img.resize((new_w, new_h), Image.NEAREST)
    canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    x = (width - img.width) // 2
    if anchor == "bottom":
        y = height - margin - img.height
    elif anchor == "top":
        y = margin
    else:
        y = (height - img.height) // 2
    canvas.paste(img, (x, y), img)
    return canvas


# ------------------------------------------------------------------- colour
def adjust(img: Image.Image, contrast: float = 1.0, saturation: float = 1.0, brightness: float = 1.0) -> Image.Image:
    img = to_rgba(img)
    alpha = img.getchannel("A")
    rgb = img.convert("RGB")
    if abs(contrast - 1.0) > 1e-6:
        rgb = ImageEnhance.Contrast(rgb).enhance(contrast)
    if abs(saturation - 1.0) > 1e-6:
        rgb = ImageEnhance.Color(rgb).enhance(saturation)
    if abs(brightness - 1.0) > 1e-6:
        rgb = ImageEnhance.Brightness(rgb).enhance(brightness)
    out = rgb.convert("RGBA")
    out.putalpha(alpha)
    return out


def _color_distance(pixels: np.ndarray, palette: np.ndarray) -> np.ndarray:
    """'Redmean' perceptual RGB distance, shape (N, P)."""
    p = pixels[:, None, :].astype(np.float32)
    c = palette[None, :, :].astype(np.float32)
    rmean = (p[..., 0] + c[..., 0]) / 2
    d = p - c
    return (2 + rmean / 256) * d[..., 0] ** 2 + 4 * d[..., 1] ** 2 + (2 + (255 - rmean) / 256) * d[..., 2] ** 2


def nearest_palette_indices(pixels: np.ndarray, palette: np.ndarray, chunk: int = 65536) -> np.ndarray:
    out = np.empty(len(pixels), dtype=np.int32)
    for start in range(0, len(pixels), chunk):
        out[start : start + chunk] = _color_distance(pixels[start : start + chunk], palette).argmin(axis=1)
    return out


_BAYER4 = np.array([[0, 8, 2, 10], [12, 4, 14, 6], [3, 11, 1, 9], [15, 7, 13, 5]], dtype=np.float32) / 16.0 - 0.5


def quantize_to_palette(img: Image.Image, palette: Sequence[RGB], dither: bool = False, dither_strength: float = 24.0) -> Image.Image:
    """Map every opaque pixel to the nearest palette color (ordered dither optional)."""
    if not palette:
        return to_rgba(img)
    arr = as_array(img).copy()
    pal = np.array(palette, dtype=np.uint8)
    opaque = arr[..., 3] > 0
    rgb = arr[..., :3].astype(np.float32)
    if dither:
        h, w = arr.shape[:2]
        tiled = np.tile(_BAYER4, (h // 4 + 1, w // 4 + 1))[:h, :w]
        rgb = np.clip(rgb + tiled[..., None] * dither_strength, 0, 255)
    pixels = rgb[opaque]
    if len(pixels):
        arr[opaque, :3] = pal[nearest_palette_indices(pixels, pal)]
    return from_array(arr)


def extract_palette(images: Iterable[Image.Image], max_colors: int = 16) -> list[RGB]:
    """Median-cut palette from the opaque pixels of one or more images, sorted by luminance."""
    pixels = []
    for img in images:
        arr = as_array(img)
        pixels.append(arr[arr[..., 3] > 0][:, :3])
    if not pixels:
        return []
    allpx = np.concatenate(pixels) if len(pixels) > 1 else pixels[0]
    if len(allpx) == 0:
        return []
    # Subsample deterministically to keep this fast for big references.
    if len(allpx) > 200_000:
        allpx = allpx[:: len(allpx) // 200_000 + 1]
    strip = Image.fromarray(allpx.reshape(1, -1, 3).astype(np.uint8), "RGB")
    quant = strip.quantize(colors=max(2, min(256, max_colors)), method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
    raw = quant.getpalette()[: 3 * max_colors]
    used = sorted(np.unique(np.array(quant)).tolist())
    colors = [tuple(raw[i * 3 : i * 3 + 3]) for i in used if i * 3 + 2 < len(raw)]
    colors = list(dict.fromkeys(colors))  # unique, stable
    colors.sort(key=lambda c: 0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2])
    return [tuple(int(v) for v in c) for c in colors]


def quantize_adaptive(img: Image.Image, max_colors: int) -> Image.Image:
    palette = extract_palette([img], max_colors)
    return quantize_to_palette(img, palette)


# ------------------------------------------------------------------ cleanup
def remove_orphan_pixels(img: Image.Image, min_same_neighbors: int = 0) -> Image.Image:
    """Clean isolated pixels.

    * Opaque pixels with no opaque 4-neighbour are removed (stray specks).
    * A pixel whose color appears in none of its 8 neighbours while one color
      dominates (>= 5 of 8) is replaced by that color (anti-aliasing noise).
    """
    arr = as_array(img).copy()
    h, w = arr.shape[:2]
    alpha = arr[..., 3] > 0
    pad_a = np.pad(alpha, 1)
    n4 = pad_a[:-2, 1:-1].astype(int) + pad_a[2:, 1:-1] + pad_a[1:-1, :-2] + pad_a[1:-1, 2:]
    stray = alpha & (n4 == 0)
    arr[stray] = 0
    alpha = arr[..., 3] > 0

    key = (arr[..., 0].astype(np.int32) << 16) | (arr[..., 1].astype(np.int32) << 8) | arr[..., 2]
    key = np.where(alpha, key, -1)
    pad_k = np.pad(key, 1, constant_values=-2)
    neighbours = np.stack(
        [pad_k[1 + dy : 1 + dy + h, 1 + dx : 1 + dx + w] for dy in (-1, 0, 1) for dx in (-1, 0, 1) if (dy, dx) != (0, 0)],
        axis=-1,
    )
    same = (neighbours == key[..., None]).sum(axis=-1)
    candidates = alpha & (same <= min_same_neighbors)
    ys, xs = np.nonzero(candidates)
    for y, x in zip(ys, xs):
        nb = neighbours[y, x]
        nb = nb[nb >= 0]
        if len(nb) == 0:
            continue
        values, counts = np.unique(nb, return_counts=True)
        i = counts.argmax()
        if counts[i] >= 5:
            v = int(values[i])
            arr[y, x, :3] = ((v >> 16) & 255, (v >> 8) & 255, v & 255)
    return from_array(arr)


def _darkest(palette: Sequence[RGB]) -> RGB:
    return min(palette, key=lambda c: 0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2])


def add_outline(img: Image.Image, style: str = "dark", palette: Sequence[RGB] | None = None, darken: float = 0.45) -> Image.Image:
    """Add a 1-pixel outline around the opaque silhouette.

    * ``black``     - pure black (or the darkest palette color)
    * ``dark``      - one fixed dark outline color (darkest palette color)
    * ``selective`` - each outline pixel is a darkened version of the adjacent
                      sprite color (snapped to the palette if given)
    * ``none``      - unchanged
    """
    if style == "none":
        return to_rgba(img)
    arr = as_array(img).copy()
    h, w = arr.shape[:2]
    alpha = arr[..., 3] > 0
    pad = np.pad(alpha, 1)
    near = pad[:-2, 1:-1] | pad[2:, 1:-1] | pad[1:-1, :-2] | pad[1:-1, 2:]
    ring = near & ~alpha
    if not ring.any():
        return from_array(arr)
    if style == "black":
        color = np.array(_darkest(palette) if palette else (0, 0, 0), dtype=np.uint8)
        arr[ring, :3] = color
    elif style == "dark":
        color = np.array(_darkest(palette) if palette else (20, 16, 28), dtype=np.uint8)
        arr[ring, :3] = color
    elif style == "selective":
        padded = np.pad(arr, ((1, 1), (1, 1), (0, 0)))
        ys, xs = np.nonzero(ring)
        cols = np.zeros((len(ys), 3), dtype=np.float32)
        for dy, dx in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            nb = padded[ys + 1 + dy, xs + 1 + dx]
            take = (nb[:, 3] > 0) & (cols.sum(axis=1) == 0)
            cols[take] = nb[take, :3]
        cols = cols * (1 - darken)
        if palette:
            pal = np.array(palette, dtype=np.uint8)
            cols = pal[nearest_palette_indices(cols, pal)]
        arr[ys, xs, :3] = cols.astype(np.uint8)
    else:
        raise ValueError(f"Unknown outline style '{style}'")
    arr[ring, 3] = 255
    return from_array(arr)


def add_drop_shadow(img: Image.Image, color: RGB = (0, 0, 0), opacity: int = 90, offset: tuple[int, int] = (1, 1)) -> Image.Image:
    img = to_rgba(img)
    alpha = np.array(img.getchannel("A")) > 0
    shadow = np.zeros((*alpha.shape, 4), dtype=np.uint8)
    dx, dy = offset
    shifted = np.zeros_like(alpha)
    h, w = alpha.shape
    shifted[max(0, dy) : h + min(0, dy), max(0, dx) : w + min(0, dx)] = alpha[max(0, -dy) : h - max(0, dy), max(0, -dx) : w - max(0, dx)]
    shadow[shifted] = (*color, opacity)
    base = from_array(shadow)
    base.alpha_composite(img)
    return base


# ------------------------------------------------------------------ pipeline
StepFunc = Callable[..., Image.Image]


def _hex_list_to_rgb(colors: Sequence[Any]) -> list[RGB]:
    from pixel_rpg_studio.project.style import hex_to_rgb

    return [hex_to_rgb(c) if isinstance(c, str) else tuple(int(v) for v in c[:3]) for c in colors]


def _op_quantize(img: Image.Image, palette: Sequence[Any] | None = None, max_colors: int = 0, dither: bool = False) -> Image.Image:
    if palette:
        return quantize_to_palette(img, _hex_list_to_rgb(palette), dither=dither)
    if max_colors:
        return quantize_adaptive(img, max_colors)
    return img


def _op_outline(img: Image.Image, style: str = "dark", palette: Sequence[Any] | None = None) -> Image.Image:
    return add_outline(img, style, _hex_list_to_rgb(palette) if palette else None)


def _op_resize_canvas(img: Image.Image, width: int, height: int, anchor: str = "center") -> Image.Image:
    return fit_canvas(img, width, height, anchor=anchor, margin=0)


OPS: dict[str, StepFunc] = {
    "alpha_threshold": alpha_threshold,
    "remove_background": remove_background,
    "crop_to_content": crop_to_content,
    "downscale": downscale,
    "fit_canvas": fit_canvas,
    "resize_canvas": _op_resize_canvas,
    "adjust": adjust,
    "quantize": _op_quantize,
    "remove_orphans": remove_orphan_pixels,
    "outline": _op_outline,
    "drop_shadow": add_drop_shadow,
    "scale_nearest": scale_nearest,
}


def run_pipeline(img: Image.Image, steps: Sequence[dict[str, Any]], keep_intermediate: bool = False) -> tuple[Image.Image, list[Image.Image]]:
    """Run processing steps in order. Returns (result, intermediates)."""
    current = to_rgba(img)
    intermediates: list[Image.Image] = []
    for step in steps:
        params = dict(step)
        op = params.pop("op")
        if params.pop("enabled", True) is False:
            continue
        func = OPS.get(op)
        if func is None:
            raise ValueError(f"Unknown processing step '{op}'. Known: {', '.join(OPS)}")
        current = func(current, **params)
        if keep_intermediate:
            intermediates.append(current.copy())
    return current, intermediates


def steps_from_style(style: Any, palette_colors: Sequence[str] | None, width: int, height: int, source: str = "concept", anchor: str = "bottom") -> list[dict[str, Any]]:
    """Build the default processing recipe from a project's StyleDefinition.

    ``source="concept"`` - AI image with background: remove bg, crop, fit.
    ``source="render"``  - fixed-camera render with alpha: plain downscale of
                           the whole frame (keeps animation frames aligned).
    """
    outline = getattr(style, "outline", "none")
    margin = 1 if outline != "none" else 0
    steps: list[dict[str, Any]] = []
    if source == "concept":
        steps += [
            {"op": "remove_background", "tolerance": 32},
            {"op": "alpha_threshold", "threshold": 128},
            {"op": "crop_to_content"},
            {"op": "fit_canvas", "width": width, "height": height, "anchor": anchor, "margin": margin, "method": style.downscale_method},
        ]
    else:
        steps += [
            {"op": "downscale", "width": width, "height": height, "method": style.downscale_method},
        ]
    if abs(style.contrast - 1) > 1e-6 or abs(style.saturation - 1) > 1e-6:
        steps.append({"op": "adjust", "contrast": style.contrast, "saturation": style.saturation})
    if style.use_palette and palette_colors:
        steps.append({"op": "quantize", "palette": list(palette_colors), "dither": bool(style.dithering)})
    else:
        steps.append({"op": "quantize", "max_colors": int(style.max_colors), "dither": False})
    steps.append({"op": "remove_orphans"})
    if outline != "none":
        steps.append({"op": "outline", "style": outline, "palette": list(palette_colors) if (style.use_palette and palette_colors) else None})
    if getattr(style, "shadow_style", "none") == "drop":
        steps.append({"op": "drop_shadow"})
    return steps
