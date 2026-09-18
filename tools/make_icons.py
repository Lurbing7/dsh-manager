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

# App icon source. v2 (2026-09-18) is the blue-and-white window with the
# character; its crop box is measured from the artwork (the window edge), which
# also cuts off the "AI generated" watermark sitting outside the bottom-right.
APP_SOURCE = "logo-v2.png"
APP_CROP = (90, 90, 1885)


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


# Tray colours sit ALONGSIDE the built-in Windows icons rather than on top of
# them: one flat colour, transparent background, no disc. The taskbar supplies
# the contrast, exactly like the volume / battery glyphs do.
TRAY_COLORS = {
    ("stopped", "dark"): (154, 160, 166),   # dim grey on a dark taskbar
    ("running", "dark"): ACCENT,            # DeepSeek blue
    ("stopped", "light"): (107, 114, 128),  # mid grey on a light taskbar
    ("running", "light"): ACCENT,
}


def render_tray(state: str, theme: str = "dark", size: int = 32) -> Image.Image:
    """Flat single-colour silhouette with a transparent background."""
    meta = SOURCES[state]
    sil = make_silhouette(load_crop(meta["file"], meta["crop"]))
    tinted = Image.new("RGBA", sil.size, TRAY_COLORS[(state, theme)] + (255,))
    tinted.putalpha(sil.split()[3])
    return tinted.resize((size, size), Image.LANCZOS)


def clean_artwork(img: Image.Image, threshold: int = 244) -> Image.Image:
    """Two cleanups on the v2 artwork.

    1. The art is painted on a fake-transparency checkerboard whose two tones are
       255 and ~247-250. Snap every near-white NEUTRAL pixel to pure white, so the
       background reads as flat white instead of a grid. (The neutral test keeps
       coloured pixels - hair, trim - untouched.)
    2. The "AI generated" watermark is a translucent WHITE overlay. On the white
       background it is invisible, but where it crosses the dark blue frame strokes
       it washes them out into light blue. In that bottom-right corner we darken
       those washed-out bluish pixels back to the frame colour.
    """
    a = np.asarray(img.convert("RGB")).astype(np.int16)
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    lum = 0.299 * r + 0.587 * g + 0.114 * b

    # 1) checkerboard -> pure white
    neutral = (a.max(axis=2) - a.min(axis=2)) <= 6
    a[neutral & (lum >= threshold)] = (255, 255, 255)

    # 2) the watermark corner. The mark is white text with a light-grey outline:
    #    on the white background only the grey outline shows, and where it crosses
    #    the dark blue frame strokes it washes them out to light blue.
    y0, y1, x0, x1 = 1850, 2010, 1640, 2010
    zone = a[y0:y1, x0:x1]
    zl = lum[y0:y1, x0:x1]
    zr, zb = zone[..., 0], zone[..., 2]
    zmax, zmin = zone.max(axis=2), zone.min(axis=2)

    # 2a) grey outline on white background -> white
    grey_marks = ((zmax - zmin) <= 22) & (zl >= 175) & (zl < 243)
    zone[grey_marks] = (255, 255, 255)

    # 2b) washed-out blue frame strokes -> frame colour
    dark = zone[(zl < 110) & (zb - zr > 8)]
    if len(dark) > 20:
        frame = np.median(dark, axis=0).astype(np.int16)
        washed = (zb - zr > 22) & (zl >= 110) & (zl < 242)
        zone[washed] = frame

    return Image.fromarray(a.astype(np.uint8), "RGB")


def make_app_icon(size: int = 1024) -> Image.Image:
    """v2 logo pipeline: clean the artwork, crop to the window edge, round the corners."""
    im = clean_artwork(Image.open(SOURCE / APP_SOURCE))
    x, y, s = APP_CROP
    im = im.crop((x, y, x + s, y + s))
    return rounded(im, 0.055).resize((size, size), Image.LANCZOS)


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
        tray = render_tray(key, "dark", 256)

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

    # Four flat silhouettes: state x taskbar theme. 32px is the sweet spot for the
    # 16/20/24 px sizes Windows actually renders in the tray.
    for state in ("stopped", "running"):
        for theme in ("dark", "light"):
            art = render_tray(state, theme, 32)
            p = out / f"tray-{state}-{theme}.png"
            art.save(p)
            print(f"{p.name:<26} {p.stat().st_size:>6} bytes")

    app = make_app_icon()
    app_path = ROOT / "src-tauri" / "app-icon.png"
    app.save(app_path)
    app.resize((256, 256), Image.LANCZOS).save(PREVIEW / "app-icon-preview.png")
    print(f"{app_path.name:<14} {app_path.stat().st_size:>7} bytes")
    print(f"preview        {PREVIEW / 'app-icon-preview.png'}")


def rounded(img: Image.Image, radius_ratio: float) -> Image.Image:
    """Apply a rounded-corner alpha mask (radius as a fraction of the short side)."""
    w, h = img.size
    r = max(1, int(min(w, h) * radius_ratio))
    ss = 4
    mask = Image.new("L", (w * ss, h * ss), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, w * ss - 1, h * ss - 1), radius=r * ss, fill=255)
    out = img.convert("RGBA")
    out.putalpha(mask.resize((w, h), Image.LANCZOS))
    return out


def backup_logo() -> None:
    """Write the polished logo into Downloads as a backup file.

    Pipeline: flatten the fake-transparency checkerboard, crop to the window edge
    (which drops the watermark outside the bottom-right corner), round the
    corners, 1024x1024. The source artwork is never modified - this writes a new
    file next to it.
    """
    out_dir = Path.home() / "Downloads"
    out_dir.mkdir(parents=True, exist_ok=True)
    PREVIEW.mkdir(parents=True, exist_ok=True)

    icon = make_app_icon(1024)
    p = out_dir / "dsh-panel-logo-v2-rounded-1024.png"
    icon.save(p)
    print(f"saved   {p}")

    icon.resize((256, 256), Image.LANCZOS).save(PREVIEW / "logo-v2-preview.png")
    print(f"preview {PREVIEW / 'logo-v2-preview.png'}")


def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else "preview"
    if mode == "preview":
        preview()
    elif mode == "build":
        build()
    elif mode == "backup":
        backup_logo()
    else:
        print(f"unknown mode: {mode} (use: preview | build | backup)")
        sys.exit(2)


if __name__ == "__main__":
    main()
