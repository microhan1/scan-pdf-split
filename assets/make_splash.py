"""Draws the start-up picture (assets/splash.png) the one-file exe shows while
it unpacks and starts Python (PyInstaller --splash in build.bat).

    python assets/make_splash.py [output path]

The picture carries no words besides the exe name, which is the same in all
four languages: a spread with its split line, then "scan-pdf-split". It is
drawn large and scaled down so the edges are smooth, and saved small (a few KB).
"""
from __future__ import annotations

import os
import sys

from PIL import Image, ImageDraw, ImageFont

sys.stdout.reconfigure(encoding="utf-8")
SCALE = 4
W, H = 300, 104
INK, RULE, PAGE, PAGE_EDGE, LINE = "#17202B", "#D9E0E6", "#F3F6F8", "#B8C2CC", "#E5484D"
TEXT_RULE = "#6B7785"
FONT = r"C:\Windows\Fonts\segoeuib.ttf"


def main(path: str) -> None:
    s = SCALE
    img = Image.new("RGB", (W * s, H * s), "white")
    d = ImageDraw.Draw(img)
    d.rectangle((0, 0, W * s - 1, H * s - 1), outline=RULE, width=s)
    # an open book: two pages side by side, text lines, the split line down the fold
    x0, y0, x1, y1 = 24 * s, 24 * s, 88 * s, 80 * s
    mid = (x0 + x1) // 2
    d.rectangle((x0, y0, x1, y1), fill=PAGE, outline=PAGE_EDGE, width=s)
    for left, right in ((x0 + 7 * s, mid - 6 * s), (mid + 6 * s, x1 - 7 * s)):
        for k in range(5):
            yy = y0 + (9 + k * 9) * s
            short = 10 * s if k == 4 else 0
            d.rectangle((left, yy, right - short, yy + 2 * s), fill=TEXT_RULE)
    d.line((mid, y0 - 5 * s, mid, y1 + 5 * s), fill=LINE, width=2 * s)
    font = ImageFont.truetype(FONT, 26 * s)
    d.text((104 * s, 52 * s), "scan-pdf-split", font=font, fill=INK, anchor="lm")
    img.resize((W, H), Image.LANCZOS).save(path, optimize=True)
    print(path, os.path.getsize(path), "bytes")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(os.path.abspath(__file__)), "splash.png"))
