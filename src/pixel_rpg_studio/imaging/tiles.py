"""Tile and tileset processing (deterministic).

* :func:`make_seamless`  - turn any texture into a wrap-around tile
* :func:`seam_score`     - measure how visible the seams are
* :func:`tile_grid`      - repeat a tile so the user can see seams
* :func:`build_corner_terrain_set` - 16-tile "match corners" transition set
  between two terrains, compatible with Godot 4 terrain sets.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from PIL import Image

from pixel_rpg_studio.imaging.pixel import as_array, downscale, from_array, quantize_to_palette, to_rgba

RGB = tuple[int, int, int]
SEAM_THRESHOLD = 1.25  # seam may be 25% stronger than the strongest inner edge


def make_seamless(img: Image.Image) -> Image.Image:
    """Make a texture tile seamlessly using the classic offset cross-fade.

    The output is a weighted mix of the image and a copy shifted by half its
    size. Near the borders the shifted copy dominates; because the shifted
    copy's border pixels come from the *centre* of the original, left/right
    and top/bottom edges become continuous when the tile wraps.
    """
    arr = as_array(img).astype(np.float32)
    h, w = arr.shape[:2]
    shifted = np.roll(np.roll(arr, h // 2, axis=0), w // 2, axis=1)
    wx = 1 - np.abs(np.linspace(-1, 1, w, dtype=np.float32))
    wy = 1 - np.abs(np.linspace(-1, 1, h, dtype=np.float32))
    weight = np.clip(np.outer(wy, wx) * 2.0, 0, 1)[..., None]
    out = arr * weight + shifted * (1 - weight)
    out[..., 3] = 255
    return from_array(out.round().astype(np.uint8))


def seam_score(img: Image.Image) -> dict[str, float]:
    """How visible the wrap-around seams are.

    * ``horizontal`` / ``vertical``: seam strength divided by the *strongest*
      edge between neighbouring columns/rows inside the tile. <= 1.0 means the
      seam is no stronger than detail already in the texture (invisible when
      tiled); clearly above 1.0 means a visible line.
    * ``score``: the worse of the two.
    * ``mean_ratio``: seam strength relative to the average edge (informative).
    """
    arr = as_array(img).astype(np.float32)[..., :3]
    col_edges = np.abs(np.diff(arr, axis=1)).mean(axis=(0, 2))  # per column pair
    row_edges = np.abs(np.diff(arr, axis=0)).mean(axis=(1, 2))
    seam_x = float(np.abs(arr[:, 0] - arr[:, -1]).mean())
    seam_y = float(np.abs(arr[0, :] - arr[-1, :]).mean())
    eps = 1e-3
    horizontal = seam_x / (float(col_edges.max()) + eps) if len(col_edges) else 0.0
    vertical = seam_y / (float(row_edges.max()) + eps) if len(row_edges) else 0.0
    mean_ratio = max(seam_x / (float(col_edges.mean()) + eps), seam_y / (float(row_edges.mean()) + eps)) if len(col_edges) else 0.0
    return {"horizontal": horizontal, "vertical": vertical, "score": max(horizontal, vertical), "mean_ratio": mean_ratio}


def is_seamless(img: Image.Image, threshold: float = SEAM_THRESHOLD) -> bool:
    return seam_score(img)["score"] <= threshold


def tile_grid(img: Image.Image, columns: int = 3, rows: int = 3, gap: int = 0) -> Image.Image:
    """Repeat a tile in a grid (optionally with gaps to show tile borders)."""
    img = to_rgba(img)
    w, h = img.size
    out = Image.new("RGBA", (columns * w + (columns - 1) * gap, rows * h + (rows - 1) * gap), (0, 0, 0, 0))
    for r in range(rows):
        for c in range(columns):
            out.paste(img, (c * (w + gap), r * (h + gap)))
    return out


def process_tile(img: Image.Image, tile_size: int, palette: list[RGB] | None = None, seamless: bool = True, method: str = "mode") -> Image.Image:
    """AI texture -> seamless -> exact tile size -> palette."""
    img = to_rgba(img)
    if seamless:
        img = make_seamless(img)
    tile = downscale(img, tile_size, tile_size, method)
    arr = as_array(tile)
    arr[..., 3] = 255  # tiles are fully opaque
    tile = from_array(arr)
    if palette:
        tile = quantize_to_palette(tile, palette)
    return tile


def tile_variations(tile: Image.Image, count: int, seed: int) -> list[Image.Image]:
    """Deterministic variants of a seamless tile (wrapped offsets and flips)."""
    rng = np.random.default_rng(seed)
    arr = as_array(tile)
    h, w = arr.shape[:2]
    variants = []
    for _ in range(count):
        v = np.roll(np.roll(arr, int(rng.integers(0, h)), axis=0), int(rng.integers(0, w)), axis=1)
        if rng.random() < 0.5:
            v = v[:, ::-1]
        variants.append(from_array(v))
    return variants


# ------------------------------------------------------------ terrain sets
@dataclass(frozen=True)
class CornerTile:
    """Corner flags: True = terrain B (overlay) at that corner."""

    tl: bool
    tr: bool
    bl: bool
    br: bool

    @property
    def index(self) -> int:
        return int(self.tl) | int(self.tr) << 1 | int(self.bl) << 2 | int(self.br) << 3

    @property
    def center_terrain(self) -> int:
        """Majority terrain (ties -> overlay), used as Godot's tile terrain."""
        return 1 if (self.tl + self.tr + self.bl + self.br) >= 2 else 0


# Atlas layout (4x4). Ordered so the "blob" reads naturally in an image editor.
CORNER_LAYOUT: list[list[int]] = [
    [0b0100, 0b1100, 0b1000, 0b0000],
    [0b0101, 0b1111, 0b1010, 0b1001],
    [0b0001, 0b0011, 0b0010, 0b0110],
    [0b1110, 0b1101, 0b1011, 0b0111],
]


def corner_tile_from_index(index: int) -> CornerTile:
    return CornerTile(bool(index & 1), bool(index & 2), bool(index & 4), bool(index & 8))


def _periodic_value_noise(size: int, cells: int, rng: np.random.Generator) -> np.ndarray:
    """Smooth value noise that wraps every ``size`` pixels (so tiles connect)."""
    grid = rng.random((cells, cells)).astype(np.float32)
    coords = (np.arange(size, dtype=np.float32) + 0.5) / size * cells
    i0 = np.floor(coords).astype(int) % cells
    i1 = (i0 + 1) % cells
    t = coords - np.floor(coords)
    t = t * t * (3 - 2 * t)
    a = grid[i0][:, i0] * (1 - t)[None, :] + grid[i0][:, i1] * t[None, :]
    b = grid[i1][:, i0] * (1 - t)[None, :] + grid[i1][:, i1] * t[None, :]
    return a * (1 - t)[:, None] + b * t[:, None]


def corner_mask(corners: CornerTile, size: int, noise: np.ndarray, roughness: float = 0.35) -> np.ndarray:
    """Boolean mask (True = overlay terrain) for one corner configuration."""
    c = (np.arange(size, dtype=np.float32) + 0.5) / size
    u = c[None, :]
    v = c[:, None]
    top = corners.tl * (1 - u) + corners.tr * u
    bottom = corners.bl * (1 - u) + corners.br * u
    field = top * (1 - v) + bottom * v
    # Concentrate the transition in the middle region so tile borders that are
    # fully one terrain stay clean, then perturb with periodic noise.
    field = np.clip((field - 0.5) * 1.6 + 0.5 + (noise - 0.5) * roughness * 2, 0, 1)
    return field >= 0.5


def build_corner_terrain_set(
    base: Image.Image,
    overlay: Image.Image,
    tile_size: int,
    seed: int = 0,
    edge_darken: float = 0.25,
    roughness: float = 0.35,
) -> tuple[Image.Image, list[dict]]:
    """Build a 4x4 atlas of corner-transition tiles between two terrains.

    ``base`` and ``overlay`` must be tile_size x tile_size (seamless) tiles.
    Returns the atlas and per-tile metadata (atlas coords + corner terrains)
    used by the Godot exporter to write terrain peering bits.
    """
    base_a = as_array(base.resize((tile_size, tile_size), Image.NEAREST))
    over_a = as_array(overlay.resize((tile_size, tile_size), Image.NEAREST))
    rng = np.random.default_rng(seed)
    noise = _periodic_value_noise(tile_size, max(2, tile_size // 4), rng)
    atlas = Image.new("RGBA", (tile_size * 4, tile_size * 4), (0, 0, 0, 0))
    tiles_meta = []
    for row, line in enumerate(CORNER_LAYOUT):
        for col, index in enumerate(line):
            corners = corner_tile_from_index(index)
            mask = corner_mask(corners, tile_size, noise, roughness)
            tile = np.where(mask[..., None], over_a, base_a).copy()
            if edge_darken > 0:
                pad = np.pad(mask, 1, mode="edge")
                border = mask & ~(pad[:-2, 1:-1] & pad[2:, 1:-1] & pad[1:-1, :-2] & pad[1:-1, 2:])
                tile[border, :3] = (tile[border, :3].astype(np.float32) * (1 - edge_darken)).astype(np.uint8)
            tile[..., 3] = 255
            atlas.paste(from_array(tile), (col * tile_size, row * tile_size))
            tiles_meta.append(
                {
                    "atlas": [col, row],
                    "index": index,
                    "corners": {"top_left": int(corners.tl), "top_right": int(corners.tr), "bottom_left": int(corners.bl), "bottom_right": int(corners.br)},
                    "terrain": corners.center_terrain,
                }
            )
    return atlas, tiles_meta


def preview_terrain_map(atlas: Image.Image, tiles_meta: list[dict], tile_size: int, corner_map: np.ndarray) -> Image.Image:
    """Render a map given a (H+1, W+1) grid of corner terrains (0/1).

    Used for the "how do the tiles connect" preview.
    """
    lookup = {m["index"]: m["atlas"] for m in tiles_meta}
    rows, cols = corner_map.shape[0] - 1, corner_map.shape[1] - 1
    out = Image.new("RGBA", (cols * tile_size, rows * tile_size))
    for y in range(rows):
        for x in range(cols):
            idx = int(corner_map[y, x]) | int(corner_map[y, x + 1]) << 1 | int(corner_map[y + 1, x]) << 2 | int(corner_map[y + 1, x + 1]) << 3
            ax, ay = lookup[idx]
            tile = atlas.crop((ax * tile_size, ay * tile_size, (ax + 1) * tile_size, (ay + 1) * tile_size))
            out.paste(tile, (x * tile_size, y * tile_size))
    return out


def demo_corner_map(width: int = 8, height: int = 6, seed: int = 1) -> np.ndarray:
    """A blobby deterministic corner map for previews."""
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0 : height + 1, 0 : width + 1]
    cx, cy = width / 2, height / 2
    dist = ((xx - cx) / (width / 2.5)) ** 2 + ((yy - cy) / (height / 2.5)) ** 2
    m = (dist + rng.random(dist.shape) * 0.5 < 1.0).astype(int)
    return m
