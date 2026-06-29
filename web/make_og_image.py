#!/usr/bin/env python3
"""
Generate the Skybarometeret social share image (``web/og-image.png``).

This is a DEV-TIME generator, run by hand when the share design changes — like
``build.py`` it is not part of the prod deploy (Caddy just serves the committed
``og-image.png``). It is kept OUT of ``build.py``'s import path so the weekly
scan+build stays pure stdlib; this script needs Pillow:

  cd web && python3 make_og_image.py     # rewrites og-image.png (1200x630)

The card is on-brand (the site's dark palette + wordmark) and FACTUAL: the
headline is the US floor — "9 av 10 ... i USA" — never a moral judgement
(CLAUDE.md rule 1). "9 av 10" is a deliberate, conservative floor: the measured
combined US share is ~99 %, so ≥9-of-10 is always true.
"""
import os

from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "og-image.png")

W, H = 1200, 630
MARGIN = 80

# Brand palette (mirrors the :root design tokens in build.py's template).
BG_TOP = (19, 32, 43)        # #13202b — the radial glow top
BG_BOT = (10, 13, 17)        # #0a0d11
FG = (238, 242, 246)         # #eef2f6
MUTED = (163, 182, 198)      # #a3b6c6
FAINT = (125, 144, 159)      # #7d909f
RED = (255, 107, 107)        # #ff6b6b — the US/CLOUD-Act stat colour
GREEN = (77, 214, 160)       # #4dd6a0 — the wordmark dot
ACCENT = (92, 179, 255)      # #5cb3ff — the URL
LINE = (42, 52, 63)          # #2a343f — the footer hairline

# Font candidates: a clean sans, bold + regular. DejaVu and Liberation ship on
# essentially every Linux dev box; fall back to Pillow's built-in if neither is
# present so the script still runs (just less polished).
_BOLD = ["/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
         "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"]
_REG = ["/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]


def font(bold, size):
    for path in (_BOLD if bold else _REG):
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default(size=size)


def text_w(draw, s, fnt):
    return draw.textlength(s, font=fnt)


def main():
    img = Image.new("RGB", (W, H), BG_BOT)
    px = img.load()
    # Vertical gradient BG_TOP -> BG_BOT (cheap, no numpy).
    for y in range(H):
        t = y / (H - 1)
        row = tuple(round(BG_TOP[i] + (BG_BOT[i] - BG_TOP[i]) * t) for i in range(3))
        for x in range(W):
            px[x, y] = row
    d = ImageDraw.Draw(img)

    # --- Masthead: ● Skytilsynet  ·  SKYBAROMETERET --------------------------
    f_word = font(True, 44)
    dot_r = 11
    cy = MARGIN + 22
    d.ellipse([MARGIN, cy - dot_r, MARGIN + 2 * dot_r, cy + dot_r], fill=GREEN)
    wx = MARGIN + 2 * dot_r + 18
    d.text((wx, MARGIN), "Skytilsynet", font=f_word, fill=FG)
    f_kick = font(True, 22)
    kx = wx + text_w(d, "Skytilsynet", f_word) + 26
    d.text((kx, MARGIN + 16), "S K Y B A R O M E T E R E T", font=f_kick, fill=FAINT)

    # --- Headline: the fact, as the provocation ------------------------------
    f_stat = font(True, 160)
    d.text((MARGIN, 150), "9 av 10", font=f_stat, fill=RED)

    f_line = font(True, 56)
    d.text((MARGIN, 346), "norske offentlige organ", font=f_line, fill=FG)
    d.text((MARGIN, 412), "kjører e-posten i USA", font=f_line, fill=FG)

    f_sub = font(False, 28)
    d.text((MARGIN, 474),
           "Amerikansk jurisdiksjon (CLOUD Act). Faktabasert og kildebelagt.",
           font=f_sub, fill=MUTED)

    # --- Footer: URL (left) + independence note (right) ----------------------
    fy = H - MARGIN - 16
    d.line([MARGIN, fy - 20, W - MARGIN, fy - 20], fill=LINE, width=1)
    f_url = font(True, 30)
    d.text((MARGIN, fy), "skytilsynet.no", font=f_url, fill=ACCENT)
    f_note = font(False, 22)
    note = "Et uavhengig prosjekt — ikke et offentlig organ"
    d.text((W - MARGIN - text_w(d, note, f_note), fy + 6), note, font=f_note, fill=FAINT)

    img.save(OUT, "PNG", optimize=True)
    print(f"Wrote {OUT} ({W}x{H}, {os.path.getsize(OUT):,} bytes)")


if __name__ == "__main__":
    main()
