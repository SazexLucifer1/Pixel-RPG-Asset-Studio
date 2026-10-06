import numpy as np
import pytest
from PIL import Image, ImageDraw

from pixel_rpg_studio.imaging import pixel
from pixel_rpg_studio.project.style import Palette, StyleDefinition


def concept(size=256, bg=(255, 255, 255)):
    img = Image.new("RGB", (size, size), bg)
    d = ImageDraw.Draw(img)
    d.ellipse((size * 0.3, size * 0.2, size * 0.7, size * 0.95), fill=(200, 40, 40))
    d.rectangle((size * 0.42, size * 0.05, size * 0.58, size * 0.22), fill=(240, 200, 160))
    d.point((size // 2, size // 2), fill=(255, 255, 255))  # interior white must survive bg removal
    return img


def colors(img):
    a = np.array(img.convert("RGBA"))
    return {tuple(c) for c in a[a[..., 3] > 0][:, :3]}


def test_remove_background_keeps_interior():
    out = pixel.remove_background(concept())
    a = np.array(out)
    assert a[0, 0, 3] == 0
    assert a[128, 128, 3] == 255  # interior white pixel kept
    assert a[150, 128, 3] == 255


def test_remove_background_skips_transparent_images():
    img = Image.new("RGBA", (10, 10), (0, 0, 0, 0))
    img.putpixel((5, 5), (255, 0, 0, 255))
    assert pixel.remove_background(img).tobytes() == img.tobytes()


def test_alpha_threshold_binary():
    img = Image.new("RGBA", (4, 1))
    for x, a in enumerate((0, 100, 200, 255)):
        img.putpixel((x, 0), (10, 20, 30, a))
    out = np.array(pixel.alpha_threshold(img))
    assert set(out[..., 3].ravel()) <= {0, 255}
    assert list(out[0, :, 3]) == [0, 0, 255, 255]


@pytest.mark.parametrize("method", ["mode", "nearest", "box"])
def test_downscale_exact_size(method):
    out = pixel.downscale(pixel.remove_background(concept()), 32, 32, method)
    assert out.size == (32, 32)
    assert set(np.array(out)[..., 3].ravel()) <= {0, 255}


def test_mode_downscale_keeps_flat_colors():
    img = Image.new("RGB", (64, 64), (10, 200, 30))
    img.paste((250, 0, 0), (0, 0, 32, 64))
    out = pixel.downscale(img, 8, 8, "mode")
    assert colors(out) == {(10, 200, 30), (250, 0, 0)}  # no blended colours


def test_quantize_to_palette():
    pal = [(0, 0, 0), (255, 255, 255), (200, 0, 0)]
    out = pixel.quantize_to_palette(concept(64), pal)
    assert colors(out) <= set(pal)
    dithered = pixel.quantize_to_palette(concept(64), pal, dither=True)
    assert colors(dithered) <= set(pal)


def test_extract_palette():
    pal = pixel.extract_palette([concept(64)], 4)
    assert 2 <= len(pal) <= 4
    lum = [0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2] for c in pal]
    assert lum == sorted(lum)


def test_fit_canvas_anchor_bottom():
    sprite = Image.new("RGBA", (10, 20), (255, 0, 0, 255))
    out = pixel.fit_canvas(sprite, 32, 32, anchor="bottom", margin=1)
    bbox = pixel.content_bbox(out)
    assert bbox[3] == 31  # bottom margin of 1
    assert out.size == (32, 32)
    big = Image.new("RGBA", (100, 200), (255, 0, 0, 255))
    out = pixel.fit_canvas(big, 32, 32, margin=1)
    x0, y0, x1, y1 = pixel.content_bbox(out)
    assert y1 - y0 <= 30


def test_outline_styles():
    img = Image.new("RGBA", (8, 8), (0, 0, 0, 0))
    img.paste((200, 50, 50, 255), (3, 3, 5, 5))
    for style in ("black", "dark", "selective"):
        out = pixel.add_outline(img, style, [(0, 0, 0), (100, 20, 20), (200, 50, 50)])
        a = np.array(out)
        assert a[2, 3, 3] == 255  # pixel above the sprite now outline
        assert a[0, 0, 3] == 0
    assert pixel.add_outline(img, "none").tobytes() == img.tobytes()


def test_orphan_cleanup():
    img = Image.new("RGBA", (9, 9), (0, 0, 0, 0))
    img.paste((0, 200, 0, 255), (1, 1, 8, 8))
    img.putpixel((4, 4), (255, 0, 255, 255))  # isolated noise pixel inside a flat area
    out = pixel.remove_orphan_pixels(img)
    assert out.getpixel((4, 4))[:3] == (0, 200, 0)
    speck = Image.new("RGBA", (5, 5), (0, 0, 0, 0))
    speck.putpixel((2, 2), (255, 255, 255, 255))
    assert pixel.remove_orphan_pixels(speck).getpixel((2, 2))[3] == 0


def test_pipeline_deterministic_and_recordable():
    style = StyleDefinition()
    steps = pixel.steps_from_style(style, Palette().colors, 48, 48)
    a, inter = pixel.run_pipeline(concept(), steps, keep_intermediate=True)
    b, _ = pixel.run_pipeline(concept(), steps)
    assert a.tobytes() == b.tobytes()
    assert a.size == (48, 48)
    assert len(inter) == len(steps)
    assert colors(a) <= {tuple(int(v) for v in c) for c in Palette().rgb()}
    import json

    json.dumps(steps)  # steps must be JSON-serialisable for metadata


def test_render_steps_keep_alignment():
    style = StyleDefinition(outline="none")
    steps = pixel.steps_from_style(style, None, 32, 32, source="render")
    frame = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
    frame.paste((255, 0, 0, 255), (100, 150, 140, 256))
    out, _ = pixel.run_pipeline(frame, steps)
    bbox = pixel.content_bbox(out)
    assert bbox[3] == 32  # touches the bottom exactly like the render: no re-centering


def test_unknown_step():
    with pytest.raises(ValueError):
        pixel.run_pipeline(concept(32), [{"op": "magic"}])


def test_scale_nearest():
    img = Image.new("RGBA", (3, 2), (1, 2, 3, 255))
    assert pixel.scale_nearest(img, 4).size == (12, 8)


def test_from_array_images_are_drawable():
    """Regression: Pillow 12 fromarray() images may ignore in-place drawing."""
    from PIL import ImageDraw

    img = pixel.from_array(np.zeros((4, 4, 4), dtype=np.uint8))
    ImageDraw.Draw(img).point((1, 1), fill=(255, 0, 0, 255))
    assert img.getpixel((1, 1)) == (255, 0, 0, 255)
