"""ページの並び順。

自然順(`2.jpg` < `10.jpg`)で並べ、表紙(`cover.*`、`DLRAW.TO_cover.jpg` のような接頭辞付きも)を先頭へ出す。
単純な自然順だと `cover.jpg` は末尾に並ぶ(GOAL.md「試作で確かめた前提」)。画像以外は除外する。
"""
from __future__ import annotations

import re
from pathlib import PurePosixPath

IMAGE_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".avif": "image/avif",
}

_DIGITS = re.compile(r"(\d+)")
# 語の一部の "cover"(discover 等)は表紙にしない。英数字以外の区切りの後ろか、名前全体が cover
_COVER = re.compile(r"(?:^|[^0-9a-z])cover$")


def _suffix(name: str) -> str:
    return PurePosixPath(name.replace("\\", "/")).suffix.lower()


def is_image(name: str) -> bool:
    return _suffix(name) in IMAGE_TYPES


def media_type(name: str) -> str:
    return IMAGE_TYPES.get(_suffix(name), "application/octet-stream")


def is_cover(name: str) -> bool:
    return bool(_COVER.search(PurePosixPath(name.replace("\\", "/")).stem.casefold()))


def natural_key(s: str) -> tuple:
    """数字の並びを数値として比べるキー。各位置の型が揃うよう (数値, 元の文字列) の組にする。"""
    parts = _DIGITS.split(s.casefold())
    return tuple((int(p), p) if i % 2 else p for i, p in enumerate(parts))


def page_sort_key(name: str) -> tuple:
    return (0 if is_cover(name) else 1, natural_key(name))


def sort_pages(names):
    """画像だけを、表紙 → 自然順で並べる。"""
    return sorted((n for n in names if is_image(n)), key=page_sort_key)
