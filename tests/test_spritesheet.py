import pytest
from PIL import Image

from pixel_rpg_studio.spritesheet.builder import AnimationSpec, SheetLayout, animated_preview, build_sheet, load_metadata, save_sheet, slice_sheet


def frames(n, color, size=(16, 16)):
    return [Image.new("RGBA", size, (*color, 255 - i)) for i in range(n)]


def test_row_per_animation_layout(tmp_path):
    anims = [AnimationSpec("idle", frames(4, (255, 0, 0)), 6, True, "s"), AnimationSpec("walk", frames(6, (0, 255, 0)), 10, True, "s")]
    sheet, meta = build_sheet(anims, SheetLayout(16, 16, padding=1, spacing=2, margin=3))
    assert meta.columns == 6 and meta.rows == 2
    assert sheet.size == (3 * 2 + 6 * 18 + 5 * 2, 3 * 2 + 2 * 18 + 2)
    walk = meta.animations[1]
    assert walk.name == "walk_s" and walk.fps == 10
    first = walk.frames[0]
    assert (first.x, first.y) == (3 + 1, 3 + 18 + 2 + 1)
    assert sheet.getpixel((first.x, first.y))[:3] == (0, 255, 0)
    png, js = save_sheet(sheet, meta, tmp_path / "out" / "hero.png")
    loaded = load_metadata(js)
    assert loaded.image == "hero.png" and loaded.animations[0].frames[3].x == meta.animations[0].frames[3].x


def test_fixed_columns_wraps():
    sheet, meta = build_sheet([AnimationSpec("a", frames(5, (1, 1, 1), (8, 8)))], SheetLayout(8, 8, columns=2))
    assert meta.rows == 3 and meta.columns == 2


def test_size_mismatch_rejected():
    with pytest.raises(ValueError):
        build_sheet([AnimationSpec("a", [Image.new("RGBA", (8, 8)), Image.new("RGBA", (9, 8))])], SheetLayout(8, 8))
    with pytest.raises(ValueError):
        build_sheet([], SheetLayout(8, 8))


def test_slice_roundtrip():
    anims = [AnimationSpec("a", frames(5, (9, 9, 9)))]
    sheet, _ = build_sheet(anims, SheetLayout(16, 16, columns=3))
    sliced = slice_sheet(sheet, 16, 16)
    assert len(sliced) == 5


def test_gif_preview(tmp_path):
    out = animated_preview(frames(3, (255, 0, 0)), tmp_path / "p.gif", fps=8)
    img = Image.open(out)
    assert img.n_frames == 3
