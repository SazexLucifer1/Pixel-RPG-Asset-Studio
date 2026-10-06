import numpy as np
import pytest
from PIL import Image

from pixel_rpg_studio.imaging import tiles as T
from pixel_rpg_studio.imaging.vfx import PRESETS, VFXParams, render_vfx


def smooth_texture(seed=0, size=256):
    rng = np.random.default_rng(seed)
    small = (rng.random((8, 8, 3)) * 255).astype(np.uint8)
    return Image.fromarray(small).resize((size, size), Image.BICUBIC)


def test_make_seamless_removes_seam():
    tex = smooth_texture()
    assert T.seam_score(tex)["score"] > 3
    assert T.seam_score(T.make_seamless(tex))["score"] < 1.0


def test_tile_grid_size():
    tile = Image.new("RGBA", (16, 16), (1, 2, 3, 255))
    assert T.tile_grid(tile, 3, 2).size == (48, 32)
    assert T.tile_grid(tile, 3, 2, gap=1).size == (50, 33)


def test_process_tile_opaque_exact_size():
    tile = T.process_tile(smooth_texture(), 16, [(0, 0, 0), (255, 255, 255), (100, 150, 50)])
    assert tile.size == (16, 16)
    assert np.array(tile)[..., 3].min() == 255


def test_corner_terrain_set_connects():
    base = Image.new("RGBA", (16, 16), (40, 160, 40, 255))
    over = Image.new("RGBA", (16, 16), (150, 100, 50, 255))
    atlas, meta = T.build_corner_terrain_set(base, over, 16, seed=1)
    assert atlas.size == (64, 64)
    assert sorted(m["index"] for m in meta) == list(range(16))
    by_index = {m["index"]: m for m in meta}
    a = np.array(atlas)

    def tile(idx):
        x, y = by_index[idx]["atlas"]
        return a[y * 16:(y + 1) * 16, x * 16:(x + 1) * 16, :3]

    assert (tile(0) == (40, 160, 40)).all()  # pure base
    assert (tile(15) != (40, 160, 40)).any()
    # Matching edges: a tile whose right corners are overlay next to one whose left corners are overlay
    left = tile(0b1010)   # tr + br overlay
    right = tile(0b0101)  # tl + bl overlay
    left_is_over = (left[:, -1] != (40, 160, 40)).any(axis=1)
    right_is_over = (right[:, 0] != (40, 160, 40)).any(axis=1)
    assert left_is_over.mean() > 0.8 and right_is_over.mean() > 0.8
    preview = T.preview_terrain_map(atlas, meta, 16, T.demo_corner_map(6, 4))
    assert preview.size == (96, 64)


def test_variations_deterministic():
    tile = T.process_tile(smooth_texture(), 16)
    a = [v.tobytes() for v in T.tile_variations(tile, 3, 5)]
    b = [v.tobytes() for v in T.tile_variations(tile, 3, 5)]
    assert a == b


@pytest.mark.parametrize("preset", list(PRESETS))
def test_vfx_presets(preset):
    p = VFXParams(preset=preset, frames=6, width=24, height=24, seed=3)
    frames = render_vfx(p)
    assert len(frames) == 6
    assert all(f.size == (24, 24) for f in frames)
    assert sum(1 for f in frames if f.getbbox()) >= 3
    again = render_vfx(p)
    assert [f.tobytes() for f in frames] == [f.tobytes() for f in again]  # reproducible


def test_vfx_invalid():
    with pytest.raises(ValueError):
        render_vfx(VFXParams(preset="nope"))
    with pytest.raises(ValueError):
        render_vfx(VFXParams(frames=0))
