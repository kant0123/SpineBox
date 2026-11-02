"""表紙のサムネイル(#75)。

表紙はネットから取らず、巻の 1 ページ目(`spinebox/pages.py` の並び。`cover.*` が先頭)を縮小して作る(GOAL.md)。
置き場は `data_dir/thumbs/` で、展開キャッシュと同じ仕組み(`ExtractionCache`。キーの sha256 の名前・容量上限つき LRU)。
元の階層は再現せず、消えてもよい。キーには元ファイルのサイズ・mtime を入れるので、元が変われば作り直す。

一覧の表示から呼ばれる `get(extract=False)` は、取り出し・7-Zip の起動が要らない巻(フォルダ、zip の中の画像、
展開キャッシュに取り出し済みの内側 zip)だけをその場で作る。一覧で巻の数だけ入れ子のアーカイブを展開させないため。
それ以外の巻は、巻を開いて取り出し済みになった後か、明示操作(`build()`)で作る。
蔵書には読み取りしかしない。
"""
from __future__ import annotations

import io
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from PIL import Image

from .cache import ExtractionCache, cache_key
from .fingerprint import MAX_PIXELS
from .library import Library, VolumeNotFound, VolumeOffline, get_volume_row, is_offline
from .scanner import resolve_source_path, root_is_accessible

# 作り方(大きさ・画質)を変えたら上げる(古いサムネイルを使わせない)
THUMB_VERSION = 1
# 一覧のカードの表紙(375px 幅で 72px 前後)を、高密度の画面でも粗く見えない大きさ
THUMB_SIZE = (240, 360)
JPEG_QUALITY = 80


class ThumbnailUnavailable(Exception):
    """この巻からは表紙を作れない(小説・ページが無い・1 ページ目が画像として読めない)。"""


def make_thumbnail(data: bytes) -> bytes | None:
    """画像のバイト列から JPEG のサムネイルを作る。読めない・大きすぎる画像は None。"""
    try:
        im = Image.open(io.BytesIO(data))
        if im.width * im.height > MAX_PIXELS:
            return None
        if im.format == "JPEG":
            im.draft("RGB", (THUMB_SIZE[0] * 2, THUMB_SIZE[1] * 2))  # 縮小しながらデコードする(速い)
        im = im.convert("RGB")
        im.thumbnail(THUMB_SIZE, Image.Resampling.LANCZOS)
        out = io.BytesIO()
        im.save(out, "JPEG", quality=JPEG_QUALITY, optimize=True)
        return out.getvalue()
    except Exception:  # noqa: BLE001 (壊れた画像・未対応の形式。表紙が無いだけにする)
        return None


@dataclass
class BuildSummary:
    total: int = 0
    made: int = 0  # 今回作った
    existing: int = 0  # 作成済み
    skipped: int = 0  # 作れない(オフライン・見つからない・読めない・小説)


class Thumbnails:
    def __init__(self, library: Library, store: ExtractionCache):
        self.library = library
        self.store = store

    def _key(self, row) -> str:
        root = Path(row["root_path"])
        if is_offline(row) or not root_is_accessible(root):
            raise VolumeOffline(row["id"])
        try:
            st = resolve_source_path(root, row["rel_path"]).stat()
        except (OSError, ValueError) as e:
            raise VolumeNotFound(row["id"]) from e
        return cache_key("thumb", THUMB_VERSION, *THUMB_SIZE, row["root_path"], row["rel_path"], st.st_size,
                         st.st_mtime_ns, row["container_path"], row["inner_path"], row["entry_prefix"])

    def get(self, conn: sqlite3.Connection, volume_id: int, *, extract: bool = False) -> tuple[str, bytes]:
        """巻の表紙の (キー, JPEG)。作成済みならそれを、無ければ 1 ページ目から作って置く。

        extract=False で取り出し・7-Zip が要る巻なら NeedsExtraction(何も展開しない)。
        """
        row = get_volume_row(conn, volume_id)
        if row["format"] is not None:
            raise ThumbnailUnavailable("小説の巻には表紙のページがありません")
        key = self._key(row)
        data = self.store.read(key)
        if data is not None:
            return key, data
        with self.library.open(conn, volume_id, extract=extract) as vol:
            if not vol.page_names:
                raise ThumbnailUnavailable("ページがありません")
            page = vol.read_page(0)
        thumb = make_thumbnail(page)
        if thumb is None:
            raise ThumbnailUnavailable("1 ページ目を画像として読めません")
        with self.store.use(key, lambda f: f.write(thumb)):
            pass
        return key, thumb

    def has(self, conn: sqlite3.Connection, volume_id: int) -> bool:
        """作成済みか(元が変わっていれば作成済みではない)。"""
        return self.store.read(self._key(get_volume_row(conn, volume_id))) is not None

    def build(self, conn: sqlite3.Connection, volume_ids: list[int],
              progress: Callable[[int, int], None] | None = None) -> BuildSummary:
        """明示操作: 渡した巻の表紙を、内側アーカイブを取り出してでも作る(1 巻ずつ。失敗しても続ける)。"""
        summary = BuildSummary(total=len(volume_ids))
        for done, volume_id in enumerate(volume_ids):
            if progress:
                progress(done, summary.total)
            try:
                if self.has(conn, volume_id):
                    summary.existing += 1
                    continue
                self.get(conn, volume_id, extract=True)
                summary.made += 1
            except Exception:  # noqa: BLE001 (オフライン・読めない巻・上限超過・小説。1 巻のために止めない)
                summary.skipped += 1
        if progress:
            progress(summary.total, summary.total)
        return summary
