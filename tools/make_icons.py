#!/usr/bin/env python3
"""Turn the source artwork into dsh-panel icons.

The source images are line art with *open* contours (gaps where hair meets
face, hand meets cup, ...). A plain flood fill leaks through those gaps, so the
mask gets a morphological closing first, then holes are filled.

Modes:
    preview   render comparison sheets into tools/preview/
    build     write tray .ico/.png into src-tauri/icons-tray/ and the app icon

Requires Pillow + numpy (both already present on this machine).
"""

from __future__ import annotations

import sys
from collections import deque
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "tools" / "source"
PREVIEW = ROOT / "tools" / "preview"

ACCENT = (77, 107, 254)  # DeepSeek-ish blue
DISC = {"stopped": (58, 66, 86), "running": ACCENT}  # dim vs vivid

# Source artwork -> crop box (left, top, size) picking out the character.
SOURCES = {
    "stopped": {
        "file": "tray-stopped.png",
        "crop": (700, 300, 1050),
        "label": "STOPPED (lying / tea)",
    },
    "running": {
        "file": "tray-running.png",
        "crop": (250, 50, 1050),
        "label": "RUNNING (typing)",
    },
}

# App icon: the full logo, cropped to drop the "AI generated" watermark in the
# bottom-right corner.
APP_SOURCE = "logo.png"
APP_CROP = (150, 150, 1650)


# --------------------------------------------------------------------------
# image pipeline
# --------------------------------------------------------------------------

def load_crop(name: str, box: tuple[int, int, int]) -> Image.Image:
    x, y, size = box
    im = Image.open(SOURCE / name).convert("RGB")
    return im.crop((x, y, x + size, y + size))


def binarize(im: Image.Image, work: int, thresh: int) -> np.ndarray:
    """True where the artwork has ink."""
    g = im.convert("L").resize((work, work), Image.LANCZOS)
    return np.asarray(g, dtype=np.uint8) < thresh


def morphological_close(mask: np.ndarray, radius: int) -> np.ndarray:
    """Dilate then erode: seals small gaps without permanently growing the shape."""
    if radius <= 0:
        return mask
    img = Image.fromarray((mask * 255).astype(np.uint8))
    k = radius * 2 + 1
    img = img.filter(ImageFilter.MaxFilter(k)).filter(ImageFilter.MinFilter(k))
    return np.asarray(img) > 127


def fill_holes(mask: np.ndarray) -> np.ndarray:
    """Flood inwards from the border; whatever it cannot reach is inside."""
    h, w = mask.shape
    outside = np.zeros_like(mask, dtype=bool)
    dq: deque[tuple[int, int]] = deque()

    def seed(y: int, x: int) -> None:
        if not mask[y, x] and not outside[y, x]:
            outside[y, x] = True
            dq.append((y, x))

    for x in range(w):
        seed(0, x)
        seed(h - 1, x)
    for y in range(h):
        seed(y, 0)
        seed(y, w - 1)

    while dq:
        y, x = dq.popleft()
        if y > 0:
            seed(y - 1, x)
        if y < h - 1:
            seed(y + 1, x)
        if x > 0:
            seed(y, x - 1)
        if x < w - 1:
            seed(y, x + 1)

    return mask | ~outside


def make_silhouette(im: Image.Image, work: int = 256, thresh: int = 205, close: int = 3) -> Image.Image:
    mask = binarize(im, work, thresh)
    mask = morphological_close(mask, close)
    mask = fill_holes(mask)
    rgba = np.zeros((work, work, 4), dtype=np.uint8)
    rgba[..., 0:3] = 255
    rgba[..., 3] = np.where(mask, 255, 0)
    return Image.fromarray(rgba, "RGBA")


def make_thick_line(im: Image.Image, work: int = 256, thresh: int = 205, grow: int = 1) -> Image.Image:
    """Keep the line art, but as bright thick strokes."""
    mask = binarize(im, work, thresh)
    if grow > 0:
        img = Image.fromarray((mask * 255).astype(np.uint8))
        mask = np.asarray(img.filter(ImageFilter.MaxFilter(grow * 2 + 1))) > 127
    rgba = np.zeros((work, work, 4), dtype=np.uint8)
    rgba[..., 0:3] = 255
    rgba[..., 3] = np.where(mask, 255, 0)
    return Image.fromarray(rgba, "RGBA")


def render_tray(kind: str, size: int = 256) -> Image.Image:
    """White silhouette on a coloured disc - readable on light AND dark taskbars."""
    meta = SOURCES[kind]
    sil = make_silhouette(load_crop(meta["file"], meta["crop"]))
    ss = 4  # supersample for a smooth disc edge
    big = Image.new("RGBA", (size * ss, size * ss), (0, 0, 0, 0))
    ImageDraw.Draw(big).ellipse((0, 0, size * ss - 1, size * ss - 1), fill=DISC[kind] + (255,))
    inner = int(size * ss * 0.72)
    off = (size * ss - inner) // 2
    big.alpha_composite(sil.resize((inner, inner), Image.LANCZOS), (off, off))
    return big.resize((size, size), Image.LANCZOS)


def make_app_icon(size: int = 1024) -> Image.Image:
    im = Image.open(SOURCE / APP_SOURCE).convert("RGB")
    x, y, s = APP_CROP
    return im.crop((x, y, x + s, y + s)).resize((size, size), Image.LANCZOS)


# --------------------------------------------------------------------------
# modes
# --------------------------------------------------------------------------

def on_disc(art: Image.Image, size: int, cell: int) -> Image.Image:
    small = art.resize((size, size), Image.LANCZOS)
    tile = Image.new("RGBA", (cell, cell), (255, 255, 255, 255))
    inner = int(cell * 0.72)
    off = (cell - inner) // 2
    small = small.resize((inner, inner), Image.LANCZOS)
    tile.alpha_composite(small, (off, off))
    return tile.resize((cell, cell), Image.NEAREST)


def preview() -> None:
    PREVIEW.mkdir(parents=True, exist_ok=True)
    sizes = [16, 24, 32, 48]
    cell, pad, label_h = 170, 10, 22
    try:
        font = ImageFont.truetype("consola.ttf", 12)
    except OSError:
        font = ImageFont.load_default()

    cols = ["crop", "silhouette", "thick-line"] + [f"sil {s}px" for s in sizes]
    W = len(cols) * (cell + pad) + pad
    H = len(SOURCES) * (cell + pad + label_h) + pad
    sheet = Image.new("RGB", (W, H), (246, 247, 249))
    dr = ImageDraw.Draw(sheet)

    for row, (key, meta) in enumerate(SOURCES.items()):
        crop = load_crop(meta["file"], meta["crop"])
        sil = make_silhouette(crop)
        thick = make_thick_line(crop)
        tray = render_tray(key)

        y = pad + row * (cell + pad + label_h)
        sheet.paste(crop.resize((cell, cell), Image.LANCZOS), (pad, y))
        dr.text((pad + 2, y + cell + 3), f"crop {meta['label']}", fill=(20, 20, 20), font=font)

        for i, art in enumerate([sil, thick], start=1):
            x = pad + i * (cell + pad)
            tile = Image.new("RGB", (cell, cell), (255, 255, 255))
            tile.paste(art.resize((cell, cell), Image.LANCZOS), (0, 0), art.resize((cell, cell), Image.LANCZOS))
            sheet.paste(tile, (x, y))
            dr.text((x + 2, y + cell + 3), cols[i], fill=(20, 20, 20), font=font)

        for j, s in enumerate(sizes):
            x = pad + (2 + j) * (cell + pad)
            sheet.paste(on_disc(tray, s, cell).convert("RGB"), (x, y))
            dr.text((x + 2, y + cell + 3), f"tray {s}px", fill=(20, 20, 20), font=font)

        print(f"{key}: silhouette pixels = {int((np.asarray(sil)[..., 3] > 0).sum())} / {256 * 256}")

    out = PREVIEW / "tray-compare.png"
    sheet.save(out)
    print(f"saved {out}")


def build() -> None:
    PREVIEW.mkdir(parents=True, exist_ok=True)
    out = ROOT / "src-tauri" / "icons-tray"
    out.mkdir(parents=True, exist_ok=True)

    ico_sizes = [(16, 16), (20, 20), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    for kind in SOURCES:
        art = render_tray(kind, 256)
        ico = out / f"{kind}.ico"
        art.save(ico, format="ICO", sizes=ico_sizes)
        # PNG for the tray at runtime: 64px scales down cleanly to the 16/20/24/32
        # px sizes Windows actually renders, without the mush a 256px source gives.
        art.resize((64, 64), Image.LANCZOS).save(out / f"{kind}.png")
        print(f"{ico.name:<14} {ico.stat().st_size:>7} bytes")

    app = make_app_icon()
    app_path = ROOT / "src-tauri" / "app-icon.png"
    app.save(app_path)
    app.resize((256, 256), Image.LANCZOS).save(PREVIEW / "app-icon-preview.png")
    print(f"{app_path.name:<14} {app_path.stat().st_size:>7} bytes")
    print(f"preview        {PREVIEW / 'app-icon-preview.png'}")


def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else "preview"
    if mode == "preview":
        preview()
    elif mode == "build":
        build()
    else:
        print(f"unknown mode: {mode} (use: preview | build)")
        sys.exit(2)


if __name__ == "__main__":
    main()
