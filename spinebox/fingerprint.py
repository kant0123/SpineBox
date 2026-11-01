"""ページ画像の見た目の指紋(知覚ハッシュ)。

別スキャンの重複は、ページの CRC が 1 枚も一致しない。そこでページ画像を 9x8 の濃淡に縮めて、
隣り合う画素の明暗(dHash)を 64 ビットにする。解像度・再圧縮・色の有無にほぼ左右されない。
画像のデコードは Pillow。読めない画像・極端に大きい画像・ほぼ無地の画像(白紙・ベタ黒)は None を返して数えない
(白紙どうしが「一致」してしまうため)。
"""
from __future__ import annotations

import io
import struct
from typing import Callable

from PIL import Image

# ハッシュの作り方を変えたら上げる(保存済みの指紋を作り直させる)
FINGERPRINT_VERSION = 1

HASH_W, HASH_H = 9, 8
# 縮小後の最大輝度 - 最小輝度がこれ未満なら無地として捨てる。実データの本文ページは 100 を超える
MIN_CONTRAST = 24
# これを超える画素数の画像は読まない(展開上限の考え方と同じ。デコード爆弾への備え)
MAX_PIXELS = 80_000_000  # Pillow が警告を出す 89M 画素より下
# 1 巻で指紋にするページ数の上限。超えたら等間隔に間引く
MAX_HASHED_PAGES = 1000

_PAIR = struct.Struct(">IQ")

PageHash = tuple[int, int]  # (ページ番号, 64 ビットのハッシュ)


def image_hash(data: bytes) -> int | None:
    """画像のバイト列から 64 ビットの dHash を作る。読めない・大きすぎる・無地なら None。"""
    try:
        im = Image.open(io.BytesIO(data))
        if im.width * im.height > MAX_PIXELS:
            return None
        if im.format == "JPEG":
            # JPEG は縮小しながらデコードできる(全画素のデコードより桁違いに速い)
            im.draft("L", (HASH_W * 16, HASH_H * 16))
        pixels = im.convert("L").resize((HASH_W, HASH_H), Image.Resampling.BOX).tobytes()
    except Exception:  # noqa: BLE001 (壊れた画像・未対応の形式・巨大画像。1 枚のために巻を止めない)
        return None
    if max(pixels) - min(pixels) < MIN_CONTRAST:
        return None
    bits = 0
    for y in range(HASH_H):
        row = y * HASH_W
        for x in range(HASH_W - 1):
            bits = (bits << 1) | (pixels[row + x] > pixels[row + x + 1])
    return bits


def sample_indexes(page_count: int, limit: int = MAX_HASHED_PAGES) -> list[int]:
    """指紋にするページ番号。上限以下なら全ページ、超えたら等間隔(先頭と末尾を含む)。"""
    if page_count <= limit:
        return list(range(page_count))
    return sorted({round(i * (page_count - 1) / (limit - 1)) for i in range(limit)})


def fingerprint_pages(page_count: int, read_page: Callable[[int], bytes]) -> tuple[list[PageHash], int]:
    """巻のページを読んで指紋を作る。(ハッシュの列, 読めなかった/無地のページ数) を返す。

    read_page が例外を出したページは数えない(1 枚の読み出し失敗で巻全体を諦めない)。
    """
    hashes: list[PageHash] = []
    skipped = 0
    for i in sample_indexes(page_count):
        try:
            h = image_hash(read_page(i))
        except Exception:  # noqa: BLE001
            h = None
        if h is None:
            skipped += 1
        else:
            hashes.append((i, h))
    return hashes, skipped


def pack(hashes: list[PageHash]) -> bytes:
    return b"".join(_PAIR.pack(i, h) for i, h in hashes)


def unpack(blob: bytes) -> list[PageHash]:
    return [_PAIR.unpack_from(blob, off) for off in range(0, len(blob) - _PAIR.size + 1, _PAIR.size)]
