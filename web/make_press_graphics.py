#!/usr/bin/env python3
"""
Generate the high-resolution PNG press graphics (``web/graphics/*.png``).

Like ``make_og_image.py`` this is a DEV-TIME generator — run by hand (or by the
operator alongside the scan) when the data or design changes. It is kept OUT of
``build.py``'s import path so the weekly scan+build stays pure stdlib; this
script needs Pillow:

  cd web && python3 make_press_graphics.py   # rewrites graphics/gauge.png + kart.png

The companion SVGs (graphics/gauge.svg, graphics/kart.svg) ARE produced by
build.py (pure stdlib); these PNGs are the raster equivalents for editors that
want a ready-to-publish bitmap. Both encode the same FACTUAL figure — the US
floor — never a moral judgement (CLAUDE.md rule 1). The figure is read from the
live datasets, never hardcoded.
"""
import json
import math
import os

from PIL import Image, ImageDraw, ImageFont

import build

HERE = os.path.dirname(os.path.abspath(__file__))
GFX = os.path.join(HERE, "graphics")

# Brand palette (mirrors the :root design tokens / the embed colours).
BG = (14, 18, 23)            # #0e1217
TRACK = (56, 70, 84)         # #384654
RED = (255, 107, 107)        # #ff6b6b
MUTED = (163, 182, 198)      # #a3b6c6
HEX_LABEL = (11, 14, 18)     # #0b0e12
FILL = {"r": (255, 107, 107), "a": (242, 181, 107),
        "g": (77, 214, 160), "x": (125, 144, 159)}

_BOLD = ["/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
         "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"]
_REG = ["/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]


def font(bold, size):
    for path in (_BOLD if bold else _REG):
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default(size=size)


def _centered(d, cx, y, text, fnt, fill):
    w = d.textlength(text, font=fnt)
    d.text((cx - w / 2, y), text, font=fnt, fill=fill)


def _categories():
    """Build the same category model build.main() uses, from the committed data."""
    data = json.load(open(build.DATA))
    stat = json.load(open(build.STAT_DATA)) if os.path.exists(build.STAT_DATA) else None
    seeded = [(json.load(open(p)), key, label)
              for p, key, label in build.SEEDED_DATA if os.path.exists(p)]
    web = json.load(open(build.WEB_DATA)) if os.path.exists(build.WEB_DATA) else None
    return build.build_categories(data, stat, web, seeded)


def gauge_png(categories, scale=4):
    """The dominant dial rasterised: a semicircular track with the US share filled
    in red, the number, and the caption."""
    combined = build.combine_summaries([c["summary"] for c in categories])
    pct = combined["us_pct"]
    frac = max(0.0, min(1.0, pct / 100.0))
    W, H = 340 * scale, 210 * scale
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    cx, cy, r, wdt = 170 * scale, 170 * scale, 150 * scale, 26 * scale
    box = [cx - r, cy - r, cx + r, cy + r]
    # Pillow arc angles run clockwise from 3 o'clock; the top semicircle is 180→360.
    d.arc(box, 180, 360, fill=TRACK, width=wdt)
    if frac > 0:
        d.arc(box, 180, 180 + 180 * frac, fill=RED, width=wdt)
    num = build._no_pct(pct) + " %"
    _centered(d, cx, int(108 * scale), num, font(True, 64 * scale), RED)
    _centered(d, cx, int(176 * scale), "på USA-kontrollert sky",
              font(False, 20 * scale), MUTED)
    out = os.path.join(GFX, "gauge.png")
    img.save(out, "PNG", optimize=True)
    print(f"Wrote {out} ({W}x{H}, {os.path.getsize(out):,} bytes)")


def kart_png(categories, scale=4):
    """The hex cartogram rasterised from the same geometry + colour string the
    SVG/embeds use, so the PNG cannot drift from the live data."""
    colorstring = build.cartogram_colorstring(categories)
    cells, vb = build._hex_cells()
    W, H = int(vb[2] * scale), int(vb[3] * scale)
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    ox, oy = vb[0], vb[1]
    f = font(True, 15 * scale)
    for i, c in enumerate(cells):
        ch = colorstring[i] if i < len(colorstring) else "x"
        pts = [((p[0] - ox) * scale, (p[1] - oy) * scale) for p in c["pts"]]
        d.polygon(pts, fill=FILL.get(ch, FILL["x"]), outline=BG, width=2 * scale)
        lx, ly = (c["lx"] - ox) * scale, (c["ly"] - oy) * scale
        w = d.textlength(c["short"], font=f)
        d.text((lx - w / 2, ly - 14 * scale), c["short"], font=f, fill=HEX_LABEL)
    out = os.path.join(GFX, "kart.png")
    img.save(out, "PNG", optimize=True)
    print(f"Wrote {out} ({W}x{H}, {os.path.getsize(out):,} bytes)")


def main():
    os.makedirs(GFX, exist_ok=True)
    categories = _categories()
    gauge_png(categories)
    kart_png(categories)


if __name__ == "__main__":
    main()
