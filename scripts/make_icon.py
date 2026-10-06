"""Generate the application icon (assets/icon.png + assets/icon.ico) as pixel art.

Run: python scripts/make_icon.py  - output is deterministic.
"""

from pathlib import Path

from PIL import Image

PIXELS = [
    "................",
    "..........KKK...",
    ".........KWWK...",
    "........KWWLK...",
    ".......KWWLK....",
    "......KWWLK.....",
    "..K..KWWLK......",
    "..KK.KWLK.......",
    "...KKKLK........",
    "....KGGK........",
    "...KGYGKK.......",
    "..KGYGK.KK......",
    ".KPPK...........",
    ".KPPK...........",
    "..KK............",
    "................",
]
COLORS = {"K": (20, 16, 28, 255), "W": (238, 234, 242, 255), "L": (124, 92, 255, 255), "G": (200, 154, 58, 255),
          "Y": (246, 199, 91, 255), "P": (106, 60, 140, 255), ".": (0, 0, 0, 0)}


def main() -> None:
    out = Path(__file__).resolve().parents[1] / "assets"
    out.mkdir(exist_ok=True)
    img = Image.new("RGBA", (16, 16))
    for y, row in enumerate(PIXELS):
        for x, ch in enumerate(row):
            img.putpixel((x, y), COLORS[ch])
    big = img.resize((256, 256), Image.NEAREST)
    big.save(out / "icon.png")
    big.save(out / "icon.ico", sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print("written", out / "icon.png", out / "icon.ico")


if __name__ == "__main__":
    main()
