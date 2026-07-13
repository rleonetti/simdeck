"""
Generate simdeck.ico with PNG-compressed frames for every size.
PIL's built-in ICO writer only PNG-compresses the 256x256 frame — smaller
frames get saved as BMP, which is what Windows and Stream Deck extract at
typical display sizes, and they look blurry.  Encoding all frames as PNG
fixes this.
"""
import io
import struct
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

SIZES = [16, 24, 32, 48, 64, 128, 256]
BG = (22, 22, 22, 255)
FG = (255, 170, 0, 255)  # amber

_FONT_PATHS = [
    "C:/Windows/Fonts/arialbd.ttf",
    "C:/Windows/Fonts/arial.ttf",
    "C:/Windows/Fonts/calibrib.ttf",
    "C:/Windows/Fonts/segoeui.ttf",
]


def _load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in _FONT_PATHS:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _best_font_size(draw: ImageDraw.ImageDraw, canvas: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Binary-search for the font size that fills ~88% of the canvas."""
    target = canvas * 0.88
    lo, hi, best = 4, canvas, None
    for _ in range(20):
        mid = (lo + hi) // 2
        f = _load_font(mid)
        bb = draw.textbbox((0, 0), "SD", font=f)
        span = max(bb[2] - bb[0], bb[3] - bb[1])
        if span <= target:
            best = f
            lo = mid + 1
        else:
            hi = mid - 1
        if lo > hi:
            break
    return best or _load_font(8)


def _render(size: int) -> Image.Image:
    """Render the SD icon at the requested pixel size (8× supersampling)."""
    ss = 8 if size <= 64 else 4 if size <= 128 else 2
    big = size * ss
    img = Image.new("RGBA", (big, big), BG)
    draw = ImageDraw.Draw(img)

    font = _best_font_size(draw, big)
    bb = draw.textbbox((0, 0), "SD", font=font)
    x = (big - (bb[2] - bb[0])) // 2 - bb[0]
    y = (big - (bb[3] - bb[1])) // 2 - bb[1]
    draw.text((x, y), "SD", fill=FG, font=font)

    return img.resize((size, size), Image.LANCZOS)


def _build_ico(frames: dict[int, Image.Image]) -> bytes:
    """
    Assemble an ICO binary where every frame is stored as PNG data.
    Standard ICO layout:
      6-byte ICONDIR  +  16-byte ICONDIRENTRY × n  +  raw image blobs
    """
    sizes = sorted(frames)
    blobs = []
    for sz in sizes:
        buf = io.BytesIO()
        frames[sz].save(buf, format="PNG")
        blobs.append(buf.getvalue())

    header = struct.pack("<HHH", 0, 1, len(sizes))

    dir_offset = 6 + len(sizes) * 16
    entries = b""
    for i, sz in enumerate(sizes):
        w = sz if sz < 256 else 0  # 0 == 256 in ICO spec
        h = sz if sz < 256 else 0
        entries += struct.pack("<BBBBHHII", w, h, 0, 0, 1, 32, len(blobs[i]), dir_offset)
        dir_offset += len(blobs[i])

    return header + entries + b"".join(blobs)


if __name__ == "__main__":
    out = Path(__file__).parent.parent / "assets" / "simdeck.ico"
    print(f"Rendering {len(SIZES)} sizes: {SIZES}")
    frames = {sz: _render(sz) for sz in SIZES}
    data = _build_ico(frames)
    out.write_bytes(data)
    print(f"Written {out} — {len(data):,} bytes, all frames PNG-compressed")
