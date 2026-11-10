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

from .archive import ArchiveError, open_archive
from .cache import ExtractionCache, cache_key
from .fingerprint import MAX_PIXELS
from .library import Library, VolumeNotFound, VolumeOffline, get_volume_row, get_volume_rows, is_offline
from .novel import FORMAT_EPUB
from .novel.epub import read_meta
from .scanner import resolve_source_path, root_is_accessible

# 作り方(大きさ・画質)を変えたら上げる(古いサムネイルを使わせない)
THUMB_VERSION = 2
# 作品カード(240px〜幅、Retina 2x で 480x720)の比率 2:3 に合わせた基準サイズ(#222)
THUMB_TARGET_WIDTH = 480
THUMB_TARGET_HEIGHT = 720
# 横長表紙の最大幅(パノラマ等の過大サイズを防ぐ)
THUMB_MAX_WIDTH = 1440
# 既存コード・テストとの互換性のためのタプル
THUMB_SIZE = (THUMB_TARGET_WIDTH, THUMB_TARGET_HEIGHT)
JPEG_QUALITY = 80


class ThumbnailUnavailable(Exception):
    """この巻からは表紙を作れない(小説・ページが無い・1 ページ目が画像として読めない)。"""


def calc_thumbnail_size(width: int, height: int,
                        target_w: int = THUMB_TARGET_WIDTH,
                        target_h: int = THUMB_TARGET_HEIGHT,
                        max_w: int = THUMB_MAX_WIDTH) -> tuple[int, int]:
    """元画像の寸法からサムネイルの (width, height) を計算する(#222)。

    作品カードは aspect-ratio: 2 / 3 (target_w / target_h) の縦長枠で表示される。
    - 縦長画像 (w/h <= target_w / target_h): 幅を target_w に合わせる (高さは target_h 以上)
    - 横長画像 (w/h > target_w / target_h): カード枠に cover (切り抜き) でフィットさせた際に
      縦解像度が不足しないよう、高さを target_h に合わせる (幅は target_h * (w/h))。
      ただし極端な横長で肥大化しないよう max_w を上限とする。
    - 元画像が小さい場合は拡大せず、元の寸法を上限とする。
    """
    if width <= 0 or height <= 0:
        return width, height

    if width / height > target_w / target_h:
        # 横長: 高さを target_h に合わせる (cover 時に縦解像度を維持)
        new_h = min(height, target_h)
        new_w = int(round(width * (new_h / height)))
        if new_w > max_w:
            new_w = max_w
            new_h = int(round(height * (new_w / width)))
    else:
        # 縦長: 幅を target_w に合わせる
        new_w = min(width, target_w)
        new_h = int(round(height * (new_w / width)))

    return max(1, new_w), max(1, new_h)


def make_thumbnail(data: bytes) -> bytes | None:
    """画像のバイト列から JPEG のサムネイルを作る。読めない・大きすぎる画像は None。"""
    try:
        im = Image.open(io.BytesIO(data))
        if im.width * im.height > MAX_PIXELS:
            return None
        target_size = calc_thumbnail_size(im.width, im.height)
        if im.format == "JPEG":
            im.draft("RGB", (target_size[0] * 2, target_size[1] * 2))  # 縮小しながらデコードする(速い)
        im = im.convert("RGB")
        target_size = calc_thumbnail_size(im.width, im.height)
        if im.size != target_size:
            im = im.resize(target_size, Image.Resampling.LANCZOS)
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

    def _key(self, row, roots: dict[str, bool] | None = None) -> str:
        """表紙のキー。roots は「ルートのパス → 見えるか」の辞書で、多数の巻をまとめて見るときに
        root_is_accessible(is_dir + scandir)をルートごとに 1 回にする。"""
        root = Path(row["root_path"])
        if is_offline(row):
            raise VolumeOffline(row["id"])
        if roots is None:
            accessible = root_is_accessible(root)
        else:
            if row["root_path"] not in roots:
                roots[row["root_path"]] = root_is_accessible(root)
            accessible = roots[row["root_path"]]
        if not accessible:
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
        if row["format"] not in (None, FORMAT_EPUB, "pdf"):
            raise ThumbnailUnavailable("この形式の巻には表紙のページがありません")
        key = self._key(row)
        data = self.store.read(key)
        if data is not None:
            return key, data
        if row["format"] == FORMAT_EPUB:
            thumb = self._epub_cover(row)
            with self.store.use(key, lambda f: f.write(thumb)):
                pass
            return key, thumb
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

    def _epub_cover(self, row) -> bytes:
        """EPUB の OPF が示す表紙画像の縮小 JPEG(#250)。表紙が示されていない・読めないなら ThumbnailUnavailable。"""
        settings = self.library.settings
        path = resolve_source_path(Path(row["root_path"]), row["rel_path"])
        try:
            with open_archive(path, sevenzip_path=settings.sevenzip_path) as reader:
                cover = read_meta(reader, settings.limits).cover
                if cover is None:
                    raise ThumbnailUnavailable("EPUB に表紙画像の指定がありません")
                data = reader.read_entry(cover, settings.limits)
        except ArchiveError as e:
            raise ThumbnailUnavailable(f"EPUB の表紙を読めません: {e}") from e
        thumb = make_thumbnail(data)
        if thumb is None:
            raise ThumbnailUnavailable("EPUB の表紙を画像として読めません")
        return thumb

    def is_built(self, conn: sqlite3.Connection, volume_id: int) -> bool:
        """作成済みか(元が変わっていれば作成済みではない)。表紙を読まず、最後に使った順も変えない。
        管理の統計・カタログ・表紙の補完で全作品を見る用。"""
        return self.store.contains(self._key(get_volume_row(conn, volume_id)))

    def are_built(self, conn: sqlite3.Connection, volume_ids: list[int], *,
                  include_unavailable: bool = True) -> dict[int, bool]:
        """is_built() を多数の巻にまとめて行う(巻の行は IN でまとめて引き、巻ごとの SELECT を発行しない)。
        ルートが見えるかの確認(is_dir + scandir)もルートごとに 1 回だけ行う。

        見えない・オフライン・見つからない巻は、include_unavailable が真なら False
        (表紙を出せないので統計は「表紙なし」に数える)、偽なら結果に含めない
        (カタログの「表紙なし」。表紙を作るジョブもその巻を飛ばすので数えない)。
        """
        rows = get_volume_rows(conn, volume_ids)
        roots: dict[str, bool] = {}
        out: dict[int, bool] = {}
        for vid in volume_ids:
            row = rows.get(vid)
            try:
                out[vid] = row is not None and self.store.contains(self._key(row, roots))
            except (VolumeNotFound, VolumeOffline):
                if include_unavailable:
                    out[vid] = False
        return out

    def build(self, conn: sqlite3.Connection, volume_ids: list[int],
              progress: Callable[[int, int], None] | None = None) -> BuildSummary:
        """明示操作: 渡した巻の表紙を、内側アーカイブを取り出してでも作る(1 巻ずつ。失敗しても続ける)。"""
        summary = BuildSummary(total=len(volume_ids))
        for done, volume_id in enumerate(volume_ids):
            if progress:
                progress(done, summary.total)
            try:
                if self.is_built(conn, volume_id):
                    summary.existing += 1
                    continue
                self.get(conn, volume_id, extract=True)
                summary.made += 1
            except Exception:  # noqa: BLE001 (オフライン・読めない巻・上限超過・小説。1 巻のために止めない)
                summary.skipped += 1
        if progress:
            progress(summary.total, summary.total)
        return summary
