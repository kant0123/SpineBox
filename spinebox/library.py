"""巻を開いてページを並べ、1 ページのバイト列を返す。

- 画像フォルダの巻: フォルダ直下の画像をそのまま読む
- アーカイブ内フォルダの巻: 外側アーカイブの目次から、そのフォルダ直下の画像エントリをメモリに読む
- 内側アーカイブの巻: ここで初めて内側アーカイブを展開キャッシュへ取り出し(ハッシュ名)、
  その中の画像をメモリに読む。今は zip/cbz の内側まで(7z/rar は後続 Issue)

どの経路も蔵書には読み取りしかしない。ページの並びは毎回目次から作り直す(DB にはページ数だけ持つ)。
"""
from __future__ import annotations

import sqlite3
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator

from . import db
from .archive import ArchiveError, LimitExceeded, UnsupportedArchive, list_entries, open_archive
from .archive.base import reader_available
from .archive.safety import depth_problem, entry_limit_problem
from .cache import ExtractionCache, cache_key
from .config import Settings
from .pages import is_image, page_sort_key, sort_pages
from .scanner import record_issues, resolve_source_path, root_is_accessible


class VolumeNotFound(Exception):
    """その id の巻が無い(または見つからなくなった)。"""


class VolumeOffline(Exception):
    """巻のあるルートが今見えない。"""


class VolumeUnreadable(Exception):
    """巻を読めない(未対応の形式・壊れている・上限超過)。"""


@dataclass
class OpenVolume:
    volume_id: int
    page_names: list[str]
    version: str  # 元ファイルが変わったら変わる値(ETag 用)
    _read: Callable[[int], bytes]

    def read_page(self, index: int) -> bytes:
        if not 0 <= index < len(self.page_names):
            raise IndexError(index)
        return self._read(index)


VOLUME_QUERY = """
SELECT v.*, s.rel_path, s.kind, s.offline AS source_offline, s.missing AS source_missing,
       r.path AS root_path, r.online AS root_online, r.enabled AS root_enabled
FROM volumes v JOIN sources s ON s.id = v.source_id JOIN library_roots r ON r.id = s.root_id
WHERE v.id = ?
"""


def get_volume_row(conn: sqlite3.Connection, volume_id: int) -> sqlite3.Row:
    row = conn.execute(VOLUME_QUERY, (volume_id,)).fetchone()
    if row is None or row["missing"] or row["source_missing"] or not row["root_enabled"]:
        raise VolumeNotFound(volume_id)
    return row


def is_offline(row: sqlite3.Row) -> bool:
    return bool(row["source_offline"] or not row["root_online"])


class Library:
    def __init__(self, settings: Settings, cache: ExtractionCache):
        self.settings = settings
        self.cache = cache

    @contextmanager
    def open(self, conn: sqlite3.Connection, volume_id: int) -> Iterator[OpenVolume]:
        row = get_volume_row(conn, volume_id)
        root = Path(row["root_path"])
        if is_offline(row) or not root_is_accessible(root):
            raise VolumeOffline(volume_id)
        path = resolve_source_path(root, row["rel_path"])
        try:
            st = path.stat()
        except OSError as e:
            raise VolumeNotFound(volume_id) from e
        version = f"{st.st_size}-{st.st_mtime_ns}"
        limits = self.settings.limits
        with ExitStack() as stack:
            if row["kind"] == "folder":
                names = sort_pages(p.name for p in path.iterdir() if p.is_file())

                def read(i: int) -> bytes:
                    page = path / names[i]
                    problem = entry_limit_problem(page.stat().st_size, limits)
                    if problem:
                        raise VolumeUnreadable(problem)
                    return page.read_bytes()

                opened = OpenVolume(volume_id, names, version, read)
            else:
                opened = self._open_archive_volume(conn, stack, row, path, version)
            if row["page_count"] != len(opened.page_names):
                with db.transaction(conn) as c:
                    c.execute("UPDATE volumes SET page_count = ? WHERE id = ?", (len(opened.page_names), volume_id))
            yield opened

    def _open_archive_volume(self, conn, stack: ExitStack, row, path: Path, version: str) -> OpenVolume:
        limits = self.settings.limits
        try:
            outer = stack.enter_context(open_archive(path))
            listing = list_entries(outer, limits)
            container = row["container_path"]
            if container is None:
                prefix = row["entry_prefix"]
                entries = sorted((e for e in listing.entries if e.parent == prefix and is_image(e.name)),
                                 key=lambda e: page_sort_key(e.name))
                reader = outer
            else:
                reader, entries = self._open_inner(conn, stack, row, path, outer, listing, container)
        except (UnsupportedArchive, LimitExceeded, ArchiveError) as e:
            raise VolumeUnreadable(str(e)) from e

        def read(i: int) -> bytes:
            try:
                return reader.read_entry(entries[i], limits)
            except (LimitExceeded, ArchiveError) as e:
                raise VolumeUnreadable(str(e)) from e

        return OpenVolume(row["id"], [e.name for e in entries], version, read)

    def _open_inner(self, conn, stack, row, path, outer, listing, container: str):
        limits = self.settings.limits
        problem = depth_problem(1, limits)
        if problem:
            raise LimitExceeded(problem)
        matches = [e for e in listing.entries if e.name == container]
        if not matches:
            raise VolumeNotFound(row["id"])
        entry = matches[0]
        if not reader_available(container):
            # 取り出してから未対応と分かるのは無駄なので先に判定する
            raise UnsupportedArchive(f"内側アーカイブは未対応の形式: {container}")
        problem = entry_limit_problem(entry.size, limits)
        if problem:
            raise LimitExceeded(problem)
        st = path.stat()
        key = cache_key("inner", str(path), st.st_size, st.st_mtime_ns, container, entry.index)
        cached = stack.enter_context(
            self.cache.use(key, lambda f: outer.copy_entry(entry, f, limits.max_entry_bytes)))
        inner = stack.enter_context(open_archive(cached, container))
        inner_listing = list_entries(inner, limits)
        with db.transaction(conn) as c:
            record_issues(c, row["source_id"], container, inner_listing.skipped)
        entries = sorted((e for e in inner_listing.entries if is_image(e.name)), key=lambda e: page_sort_key(e.name))
        return inner, entries
