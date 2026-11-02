"""小説(EPUB・テキスト)の巻。ページ画像を持たないので `Library.open` ではなく `books.Books` で開く。

このファイルはスキャナーからも読むので、library / scanner を import しない(循環を避ける)。
方式(安全方針・位置の持ち方)は wiki/components/novel-reader.md。
"""
from __future__ import annotations

from pathlib import PurePosixPath

FORMAT_EPUB = "epub"
FORMAT_TEXT = "text"
NOVEL_FORMATS = {".epub": FORMAT_EPUB, ".txt": FORMAT_TEXT}


def novel_format(name: str) -> str | None:
    """名前の拡張子から小説の形式(epub / text)。小説でなければ None。"""
    return NOVEL_FORMATS.get(PurePosixPath(name.replace("\\", "/")).suffix.lower())
