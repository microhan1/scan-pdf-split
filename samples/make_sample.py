"""Make sample_spread_scan.pdf: a fake book scanned open, for trying the tool.

    python samples/make_sample.py

Everything is drawn here (text written for this sample, Windows fonts), so the
file carries no copyright. Ten pages: a portrait front cover, seven spreads
whose folds sit at different places (47-53% of the width) with a gutter
shadow and a slight tilt, one spread with a chart across the fold, one spread
stored sideways with /Rotate 90, and a portrait back cover.
"""
import io
import math
import os
import random

import pymupdf
from PIL import Image, ImageChops, ImageDraw, ImageFont

random.seed(7)
HERE = os.path.dirname(os.path.abspath(__file__))
DPI = 150
SW, SH = 1754, 1240                     # A4 landscape spread at 150 dpi
FONTS = "C:/Windows/Fonts/"
BODY = ImageFont.truetype(FONTS + "batang.ttc", 21)
HEAD = ImageFont.truetype(FONTS + "malgun.ttf", 16)
BIG = ImageFont.truetype(FONTS + "malgunbd.ttf", 64)
MID = ImageFont.truetype(FONTS + "malgun.ttf", 28)

SENT = [
    "책을 펼쳐서 스캐너에 올리면 한 번에 두 쪽이 찍힌다.",
    "가운데 제본선 근처는 종이가 휘어서 그림자가 지고 글자가 조금 기울어진다.",
    "스캔할 때마다 책이 놓인 자리가 달라지므로 가운데 선도 매번 조금씩 움직인다.",
    "그래서 모든 쪽을 정확히 절반으로 자르면 글자가 옆 쪽으로 넘어가기도 한다.",
    "이 문서는 두쪽 나누기 도구를 시험하려고 만든 가짜 스캔본이다.",
    "쪽마다 분할선의 위치를 일부러 조금씩 다르게 두었다.",
    "어떤 쪽에는 가운데를 가로지르는 그림을 넣어 자동 맞춤이 헷갈리도록 했다.",
    "앞표지와 뒤표지는 세로로 긴 한 쪽짜리이므로 나누지 않아야 한다.",
    "마지막 펼침면은 옆으로 눕혀 저장한 뒤 회전 정보로 바로 세워 두었다.",
    "결과 파일에서 쪽 번호가 차례대로 이어지는지 확인해 보면 된다.",
]
PAPER = (244, 239, 227)


def wrap(text, font, width):
    lines, cur = [], ""
    for ch in text:
        if font.getlength(cur + ch) > width:
            lines.append(cur)
            cur = ch.lstrip()
        else:
            cur += ch
    if cur:
        lines.append(cur)
    return lines


def paper(w, h):
    base = Image.new("RGB", (w, h), PAPER)
    noise = Image.effect_noise((w, h), 10).convert("RGB")
    return Image.blend(base, ImageChops.multiply(base, noise), 0.10)


def book_page(w, h, num, header):
    im = paper(w, h)
    d = ImageDraw.Draw(im)
    mx = int(w * 0.13)
    d.text((mx, 70), header, font=HEAD, fill=(95, 90, 82))
    d.line((mx, 100, w - mx, 100), fill=(170, 165, 155), width=1)
    y, k = 140, num
    while y < h - 150:
        text = " ".join(SENT[(k + i) % len(SENT)] for i in range(3))
        for ln in wrap("  " + text, BODY, w - 2 * mx):
            if y > h - 150:
                break
            d.text((mx, y), ln, font=BODY, fill=(38, 36, 33))
            y += 38
        y += 22
        k += 2
    d.text((w / 2, h - 80), str(num), font=HEAD, fill=(70, 66, 60), anchor="mm")
    return im


def gutter_shadow(img, fx, sigma=26, depth=0.58):
    """Darken around the fold; darkest exactly at it."""
    w, h = img.size
    band = Image.new("L", (w, 1))
    px = band.load()
    for x in range(w):
        v = 1 - depth * math.exp(-((x - fx) / sigma) ** 2)
        v *= 1 - 0.25 * math.exp(-((x - fx) / (sigma * 4)) ** 2)
        px[x, 0] = int(255 * v)
    band = band.resize((w, h))
    return ImageChops.multiply(img, Image.merge("RGB", (band, band, band)))


def spread(lnum, rnum, fold, tilt=0.0, figure=False, header=("두쪽 나누기 시험본", "제2장 스캔의 버릇")):
    canvas = Image.new("RGB", (SW, SH), (48, 47, 45))
    fx = int(SW * fold)
    top, bottom = 34, SH - 30
    canvas.paste(book_page(fx - 26, bottom - top, lnum, header[0]), (26, top))
    canvas.paste(book_page(SW - fx - 30, bottom - top, rnum, header[1]), (fx, top))
    if figure:
        d = ImageDraw.Draw(canvas)
        x0, x1, y0, y1 = fx - 330, fx + 330, 430, 840
        d.rectangle((x0 - 30, y0 - 50, x1 + 30, y1 + 70), fill=PAPER)      # clear the text under it
        d.rectangle((x0, y0, x1, y1), fill=(236, 231, 219), outline=(60, 58, 54), width=3)
        pts = [(x0 + 30 + i * 40, y1 - 40 - int(260 * (0.5 + 0.45 * math.sin(i / 2.2)))) for i in range(16)]
        d.line(pts, fill=(160, 40, 40), width=6)
        for i in range(0, 15, 3):
            d.rectangle((x0 + 40 + i * 40, y1 - 100 - i * 12, x0 + 70 + i * 40, y1 - 40), fill=(70, 90, 120))
        d.text((fx, y1 + 36), "그림 2-1. 가운데를 가로지르는 도표", font=HEAD, fill=(60, 56, 50), anchor="mm")
    canvas = gutter_shadow(canvas, fx)
    if tilt:
        canvas = canvas.rotate(tilt, resample=Image.BICUBIC, fillcolor=(48, 47, 45))
    return canvas


def cover(title, sub, back=False):
    w, h = 877, 1240
    im = Image.new("RGB", (w, h), (48, 47, 45))
    face = Image.new("RGB", (w - 40, h - 50), (40, 70, 98) if back else (34, 64, 92))
    d = ImageDraw.Draw(face)
    if back:
        d.text(((w - 40) / 2, h - 200), "뒤표지 · 나누지 않아야 하는 쪽", font=MID, fill=(200, 210, 220), anchor="mm")
    else:
        d.text((70, 360), title, font=BIG, fill=(245, 240, 228))
        d.text((74, 460), sub, font=MID, fill=(200, 210, 220))
        d.line((74, 520, 380, 520), fill=(229, 72, 77), width=5)
    im.paste(face, (20, 25))
    return im


def jpeg(img):
    b = io.BytesIO()
    img.save(b, "JPEG", quality=82)
    return b.getvalue()


def main():
    doc = pymupdf.open()

    def add(img, rotate=0):
        stored = img.rotate(90, expand=True) if rotate == 90 else img   # stored sideways, shown upright
        page = doc.new_page(width=stored.width * 72 / DPI, height=stored.height * 72 / DPI)
        page.insert_image(page.rect, stream=jpeg(stored))
        if rotate:
            page.set_rotation(rotate)

    add(cover("시험용 스캔 도서", "두쪽 나누기 테스트 파일"))
    plan = [(0.500, 0.0, False), (0.515, 0.3, False), (0.480, -0.4, False), (0.530, 0.2, True),
            (0.500, -0.2, False), (0.470, 0.5, False), (0.520, 0.0, False)]
    n = 1
    for fold, tilt, fig in plan:
        add(spread(n, n + 1, fold, tilt, fig))
        n += 2
    add(spread(n, n + 1, 0.505, 0.1), rotate=90)
    add(cover("", "", back=True))
    doc.set_metadata({"title": "Sample spread scan", "author": "scan-pdf-split"})
    out = os.path.join(HERE, "sample_spread_scan.pdf")
    doc.save(out, garbage=3, deflate=True)
    print(out, len(doc), "pages")


if __name__ == "__main__":
    main()
