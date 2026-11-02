"""小説(EPUB・テキスト)の巻を開いて、区切りごとの表示用 HTML と画像の資源を返す。

巻の `format` が `epub` / `text` のもの。ページ画像を持たないので `Library.open` ではなくここで開く。
読み取りだけで、蔵書には書かない。

- 区切り(section): EPUB は spine の本文 1 つ、テキストはおよそ SECTION_CHARS 文字。読書位置は
  「区切りの番号(reading_progress.page)+区切りの中の割合(reading_progress.position)」で持つ
- 本文の HTML は必ずここで作る(EPUB はサニタイズ、テキストはエスケープしてから組み立てる)。方式は wiki/components/novel-reader.md
"""
from __future__ import annotations

import dataclasses
import posixpath
import sqlite3
import threading
from collections import OrderedDict
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator

from .. import db
from ..archive import ArchiveError, open_archive
from ..archive.safety import Skip, entry_limit_problem
from ..config import Settings
from ..library import VolumeNotFound, VolumeOffline, VolumeUnreadable, get_volume_row, is_offline
from ..scanner import record_issues, resolve_source_path, root_is_accessible
from . import FORMAT_EPUB, FORMAT_TEXT
from . import epub as epub_mod
from .sanitize import sanitize_html
from .text import TextDecodeError, decode_text, section_to_html, split_sections

MAX_TEXT_BYTES = 64 * 1024 * 1024  # テキスト 1 ファイル(全体を復号して区切るため)


def resource_url(volume_id: int, index: int) -> str:
    return f"/api/volumes/{volume_id}/resources/{index}"


@dataclass
class OpenBook:
    volume_id: int
    format: str
    version: str
    lengths: list[int]            # 区切りごとの目安の長さ(全体の進み具合の重み)
    encoding: str | None          # テキストの文字コード
    _section: Callable[[int], str]
    _resource: Callable[[int], tuple[bytes, str]]

    def section_html(self, index: int) -> str:
        if not 0 <= index < len(self.lengths):
            raise IndexError(index)
        return self._section(index)

    def resource(self, index: int) -> tuple[bytes, str]:
        return self._resource(index)


class Books:
    """小説の巻を開く。復号・区切りの結果(テキスト)と EPUB の構造は、元ファイルの版ごとに数冊だけ覚えておく。"""

    def __init__(self, settings: Settings, keep: int = 4):
        self.settings = settings
        self.keep = keep
        self._memo: OrderedDict[tuple, object] = OrderedDict()
        self._lock = threading.Lock()

    def _remember(self, key: tuple, make: Callable[[], object]):
        with self._lock:
            if key in self._memo:
                self._memo.move_to_end(key)
                return self._memo[key]
        value = make()
        with self._lock:
            self._memo[key] = value
            while len(self._memo) > self.keep:
                self._memo.popitem(last=False)
        return value

    @contextmanager
    def open(self, conn: sqlite3.Connection, volume_id: int) -> Iterator[OpenBook]:
        row = get_volume_row(conn, volume_id)
        if row["format"] not in (FORMAT_EPUB, FORMAT_TEXT):
            raise VolumeUnreadable("小説(EPUB・テキスト)の巻ではありません")
        root = Path(row["root_path"])
        if is_offline(row) or not root_is_accessible(root):
            raise VolumeOffline(volume_id)
        path = resolve_source_path(root, row["rel_path"])
        try:
            st = path.stat()
        except OSError as e:
            raise VolumeNotFound(volume_id) from e
        version = f"{st.st_size}-{st.st_mtime_ns}"
        with ExitStack() as stack:
            if row["format"] == FORMAT_TEXT:
                book = self._open_text(conn, row, path, version, st.st_size)
            else:
                book = self._open_epub(stack, row, path, version)
            if row["page_count"] != len(book.lengths):
                with db.transaction(conn) as c:
                    c.execute("UPDATE volumes SET page_count = ? WHERE id = ?", (len(book.lengths), volume_id))
            yield book

    # --- テキスト -----------------------------------------------------------

    def _open_text(self, conn, row, path: Path, version: str, size: int) -> OpenBook:
        limit = min(MAX_TEXT_BYTES, self.settings.limits.max_entry_bytes)
        problem = entry_limit_problem(size, dataclasses.replace(self.settings.limits, max_entry_bytes=limit))
        if problem:
            self._record(conn, row, problem)
            raise VolumeUnreadable(problem)

        def make():
            try:
                text, encoding = decode_text(path.read_bytes())
            except TextDecodeError as e:
                return e
            return split_sections(text), encoding

        try:
            made = self._remember(("text", str(path), version), make)
        except OSError as e:
            raise VolumeNotFound(row["id"]) from e
        if isinstance(made, TextDecodeError):
            self._record(conn, row, str(made))
            raise VolumeUnreadable(str(made))
        sections, encoding = made

        def no_resource(index: int) -> tuple[bytes, str]:
            raise IndexError(index)

        return OpenBook(row["id"], FORMAT_TEXT, version, [max(len(s), 1) for s in sections], encoding,
                        lambda i: section_to_html(sections[i]), no_resource)

    def _record(self, conn, row, reason: str) -> None:
        with db.transaction(conn) as c:
            record_issues(c, row["source_id"], None, [Skip(None, reason)])

    # --- EPUB --------------------------------------------------------------

    def _open_epub(self, stack: ExitStack, row, path: Path, version: str) -> OpenBook:
        limits = self.settings.limits
        volume_id = row["id"]
        try:
            reader = stack.enter_context(open_archive(path, sevenzip_path=self.settings.sevenzip_path))
            book = self._remember(("epub", str(path), version), lambda: epub_mod.open_book(reader, limits))
        except ArchiveError as e:
            raise VolumeUnreadable(str(e)) from e

        def section(index: int) -> str:
            try:
                markup, name = epub_mod.read_section(reader, book, index, limits)
            except ArchiveError as e:
                raise VolumeUnreadable(str(e)) from e
            base = posixpath.dirname(name)

            def resolve(href: str) -> str | None:
                target = epub_mod.resolve_href(base, href)
                idx = book.resource_index.get(target) if target else None
                return None if idx is None else resource_url(volume_id, idx)

            return sanitize_html(markup, resolve)

        def resource(index: int) -> tuple[bytes, str]:
            if not 0 <= index < len(book.resources):
                raise IndexError(index)
            res = book.resources[index]
            try:
                return reader.read_entry(res.entry, limits), res.media_type
            except ArchiveError as e:
                raise VolumeUnreadable(str(e)) from e

        return OpenBook(volume_id, FORMAT_EPUB, version, [s.length for s in book.sections], None, section, resource)
