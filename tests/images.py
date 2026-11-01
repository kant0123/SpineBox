"""重複検出のテスト用に、本物の画像(JPEG / PNG)を決まった乱数から作る。"""
from __future__ import annotations

import io
import random

from PIL import Image, ImageDraw


def make_page(seed: int, size=(300, 420)) -> Image.Image:
    """濃淡の違う長方形を重ねた「ページ」。seed ごとに別の図柄になる。"""
    rnd = random.Random(seed)
    im = Image.new("L", size, rnd.randint(200, 255))
    d = ImageDraw.Draw(im)
    for _ in range(14):
        x0, y0 = rnd.randint(0, size[0] - 40), rnd.randint(0, size[1] - 40)
        x1, y1 = x0 + rnd.randint(20, size[0] // 2), y0 + rnd.randint(20, size[1] // 2)
        d.rectangle([x0, y0, x1, y1], fill=rnd.randint(0, 255))
    return im


def make_series(series: int, n: int) -> list[Image.Image]:
    return [make_page(series * 10_000 + i) for i in range(n)]


def jpeg(im: Image.Image, *, scale: float = 1.0, quality: int = 90) -> bytes:
    if scale != 1.0:
        im = im.resize((max(8, round(im.width * scale)), max(8, round(im.height * scale))))
    buf = io.BytesIO()
    im.convert("RGB").save(buf, "JPEG", quality=quality)
    return buf.getvalue()


def png(im: Image.Image) -> bytes:
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def blank_page(value: int = 255, size=(300, 420)) -> bytes:
    return jpeg(Image.new("L", size, value))
