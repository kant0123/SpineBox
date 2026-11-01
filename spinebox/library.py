"""巻を開いてページを並べ、1 ページのバイト列を返す。

- 画像フォルダの巻: フォルダ直下の画像をそのまま読む
- アーカイブ内フォルダの巻: 外側アーカイブの目次から、そのフォルダ直下の画像エントリをメモリに読む
- 内側アーカイブの巻: ここで初めて内側アーカイブを展開キャッシュへ取り出し(ハッシュ名)、
  その中の画像をメモリに読む。zip / cbz / 7z / rar(7z / rar は 7-Zip CLI)。内側にさらにアーカイブが
  あれば、入れ子の深さの上限まで同じ方法で開いて続ける。開いたときに中の巻の単位(Unit)も集め、
  2 つ以上あれば子の巻として登録する(巻への分解。spinebox/analysis.py)
- 子の巻(分解で作った巻): 親の内側アーカイブから inner_path の入れ子を順に取り出し、
  entry_prefix のフォルダ直下の画像を読む。取り出しは親と同じキャッシュキーなので、親を開いた後なら取り出し直さない

どの経路も蔵書には読み取りしかしない。ページの並びは毎回目次から作り直す(DB にはページ数だけ持つ)。
"""
from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path
from pathlib import PurePosixPath
from typing import Callable, Iterator

from . import analysis, db
from .archive import (ArchiveError, LimitExceeded, UnsupportedArchive, is_archive_name, list_entries,
                      open_archive)
from .archive.base import reader_available
from .archive.safety import Skip, depth_problem, entry_limit_problem
from .cache import CacheTooLarge, ExtractionCache, cache_key
from .config import Settings
from .pages import is_image, natural_key, page_sort_key, sort_pages
from .scanner import record_issues, resolve_source_path, root_is_accessible


class VolumeNotFound(Exception):
    """その id の巻が無い(または見つからなくなった)。"""


class VolumeOffline(Exception):
    """巻のあるルートが今見えない。"""


class VolumeUnreadable(Exception):
    """巻を読めない(未対応の形式・壊れている・上限超過)。"""


log = logging.getLogger(__name__)


@dataclass
class OpenVolume:
    volume_id: int
    page_names: list[str]
    version: str  # 元ファイルが変わったら変わる値(ETag 用)
    _read: Callable[[int], bytes]
    # 内側アーカイブの巻(親を持たないもの)を開いたときだけ: 中で見つかった巻の単位と、各ページの単位
    units: list[analysis.Unit] | None = None
    page_units: list[analysis.UnitKey] | None = None

    def read_page(self, index: int) -> bytes:
        if not 0 <= index < len(self.page_names):
            raise IndexError(index)
        return self._read(index)


VOLUME_QUERY = """
SELECT v.*, s.rel_path, s.kind, s.offline AS source_offline, s.missing AS source_missing,
       r.path AS root_path, r.online AS root_online, r.enabled AS root_enabled,
       pv.missing AS parent_missing, pv.title AS parent_title
FROM volumes v JOIN sources s ON s.id = v.source_id JOIN library_roots r ON r.id = s.root_id
LEFT JOIN volumes pv ON pv.id = v.parent_id
WHERE v.id = ?
"""


def get_volume_row(conn: sqlite3.Connection, volume_id: int) -> sqlite3.Row:
    row = conn.execute(VOLUME_QUERY, (volume_id,)).fetchone()
    # 整理処理で CBZ に置き換えた巻(organized_op_id)は、行を残したまま見えない扱いにする
    if (row is None or row["missing"] or row["source_missing"] or not row["root_enabled"]
            or row["parent_missing"] or row["organized_op_id"] is not None):
        raise VolumeNotFound(volume_id)
    return row


def is_analyzable(row) -> bool:
    """分解の対象か: スキャンが切り出した内側アーカイブの巻だけ(子の巻・フォルダの巻は対象外)。"""
    return row["container_path"] is not None and row["parent_id"] is None and row["inner_path"] is None


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
        sevenzip = self.settings.sevenzip_path
        units = page_units = None
        try:
            outer = stack.enter_context(open_archive(path, sevenzip_path=sevenzip))
            listing = list_entries(outer, limits)
            container = row["container_path"]
            if container is None:
                prefix = row["entry_prefix"]
                entries = sorted((e for e in listing.entries if e.parent == prefix and is_image(e.name)),
                                 key=lambda e: page_sort_key(e.name))
                pages = [(outer, e) for e in entries]
            elif row["inner_path"] is not None:
                pages = self._open_unit(stack, row, path, outer, listing)
            else:
                found = self._open_inner(conn, stack, row, path, outer, listing)
                pages = [(r, e) for r, e, _ in found]
                page_units = [(chain, e.parent) for _, e, chain in found]
                units = _units(found, container)
        except (UnsupportedArchive, LimitExceeded, ArchiveError) as e:
            raise VolumeUnreadable(str(e)) from e

        def read(i: int) -> bytes:
            reader, entry = pages[i]
            try:
                return reader.read_entry(entry, limits)
            except (LimitExceeded, ArchiveError) as e:
                raise VolumeUnreadable(str(e)) from e

        return OpenVolume(row["id"], [e.name for _, e in pages], version, read, units, page_units)

    def _root_key(self, path: Path) -> str:
        st = path.stat()
        return cache_key("inner", str(path), st.st_size, st.st_mtime_ns)

    def _container_entry(self, row, listing):
        matches = [e for e in listing.entries if e.name == row["container_path"]]
        if not matches:
            raise VolumeNotFound(row["id"])
        return matches[0]

    def _open_inner(self, conn, stack, row, path, outer, listing):
        """内側アーカイブを取り出して開き、(リーダー, エントリ, 入れ子の名前) のページ列を返す。

        内側にさらにアーカイブがあり、深さの上限に収まるなら、それも開いて名前の自然順に続ける
        (7z の中の rar の中の rar の中の zip、という実データがある)。入れ子の名前は、内側アーカイブから
        そのページを含むアーカイブまでの名前の並び(巻の単位を見分けるのに使う)。
        """
        container = row["container_path"]
        issues: list[Skip] = []
        pages = self._expand(stack, outer, self._container_entry(row, listing), self._root_key(path), 1, (),
                             issues)
        with db.transaction(conn) as c:
            record_issues(c, row["source_id"], container, issues)
        return pages

    def _open_unit(self, stack, row, path, outer, listing):
        """子の巻: 親の内側アーカイブから inner_path の入れ子を順に取り出し、entry_prefix 直下の画像を返す。"""
        try:
            chain = json.loads(row["inner_path"])
        except ValueError as e:
            raise VolumeNotFound(row["id"]) from e
        reader, inner_listing, key = self._extract(stack, outer, self._container_entry(row, listing),
                                                   self._root_key(path), 1)
        for depth, name in enumerate(chain, start=2):
            matches = [e for e in inner_listing.entries if e.name == name and is_archive_name(e.name)]
            if not matches:
                raise VolumeNotFound(row["id"])
            reader, inner_listing, key = self._extract(stack, reader, matches[0], key, depth)
        prefix = row["entry_prefix"]
        images = sorted((e for e in inner_listing.entries if e.parent == prefix and is_image(e.name)),
                        key=lambda e: page_sort_key(e.name))
        if not images:
            raise VolumeNotFound(row["id"])
        return [(reader, e) for e in images]

    def _extract(self, stack, parent, entry, parent_key: str, depth: int):
        """1 段の内側アーカイブをキャッシュへ取り出して開く。(リーダー, 目次, キー) を返す。

        キーは親のキーとエントリ名・番号から作るので、親の巻を開いたときと子の巻を開いたときで同じになる。
        """
        limits = self.settings.limits
        sevenzip = self.settings.sevenzip_path
        problem = depth_problem(depth, limits)
        if problem:
            raise LimitExceeded(problem)
        if not reader_available(entry.name, sevenzip):
            # 取り出してから未対応と分かるのは無駄なので先に判定する
            raise UnsupportedArchive(f"内側アーカイブは未対応の形式(または 7-Zip が見つからない): {entry.name}")
        problem = entry_limit_problem(entry.size, limits)
        if problem:
            raise LimitExceeded(problem)
        key = cache_key("inner", parent_key, entry.name, entry.index)
        cached = stack.enter_context(
            self.cache.use(key, lambda f: parent.copy_entry(entry, f, limits.max_entry_bytes)))
        inner = stack.enter_context(open_archive(cached, entry.name, sevenzip_path=sevenzip))
        return inner, list_entries(inner, limits), key

    def _expand(self, stack, parent, entry, parent_key: str, depth: int, chain: tuple[str, ...],
                issues: list[Skip]):
        limits = self.settings.limits
        inner, listing, key = self._extract(stack, parent, entry, parent_key, depth)
        label = "" if depth == 1 else entry.name + "/"
        issues.extend(Skip(label + (s.entry_name or ""), s.reason) if label else s for s in listing.skipped)
        images = sorted((e for e in listing.entries if is_image(e.name)), key=lambda e: page_sort_key(e.name))
        pages = [(inner, e, chain) for e in images]
        for nested in sorted((e for e in listing.entries if is_archive_name(e.name)),
                             key=lambda e: natural_key(e.name)):
            problem = depth_problem(depth + 1, limits)
            if problem:
                issues.append(Skip(f"{entry.name}/{nested.name}" if depth > 1 else nested.name, problem))
                continue
            pages += self._expand(stack, inner, nested, key, depth + 1, chain + (nested.name,), issues)
        return pages

    # --- 巻への分解 ---------------------------------------------------------

    def analyze(self, conn: sqlite3.Connection, volume_id: int, *, force: bool = False) -> bool:
        """内側アーカイブの巻を開いて巻の単位を見つけ、子の巻として登録する。分解の対象でなければ False。

        解析済み(元ファイルが変わっていない)なら、force でない限り開き直さない。
        オフラインなら VolumeOffline(レコードには触らない)。
        """
        row = get_volume_row(conn, volume_id)
        if not is_analyzable(row):
            return False
        if not force and row["analyzed_version"] is not None and row["analysis_error"] is None:
            current = self.current_analysis_version(row)
            if current is not None and current == row["analyzed_version"]:
                return True
        with self.open(conn, volume_id) as vol:
            self.register_analysis(conn, vol, force=force)
        return True

    def register_analysis(self, conn: sqlite3.Connection, vol: OpenVolume, *, force: bool = False) -> bool:
        """開いた巻(内側アーカイブの巻)の単位を子の巻として登録する。開いた巻でなければ何もしない。

        同じ版で解析済みなら、force でない限り登録し直さない(巻を開くたびに DB へ書かないため)。
        登録し直したかどうかを返す(作品への割り当てを取り直す契機)。
        """
        if vol.units is None:
            return False
        row = get_volume_row(conn, vol.volume_id)
        version = analysis.analysis_version(vol.version, self.settings.limits.max_depth)
        if not force and row["analyzed_version"] == version and row["analysis_error"] is None:
            return False
        analysis.register_units(conn, row, vol.units, list(zip(vol.page_units, vol.page_names)), version)
        return True

    def current_analysis_version(self, row) -> str | None:
        try:
            st = resolve_source_path(Path(row["root_path"]), row["rel_path"]).stat()
        except (OSError, ValueError):
            return None
        return analysis.analysis_version(f"{st.st_size}-{st.st_mtime_ns}", self.settings.limits.max_depth)

    def analyze_next(self, conn: sqlite3.Connection) -> int | None:
        """未解析の内側アーカイブの巻を 1 つ解析する(裏で少しずつ解析する用)。解析した巻の id を返す。

        オフライン・見つからない巻は飛ばす(記録しない。再接続後にまた候補になる)。読めなかった巻は
        理由を analysis_error に残し、元ファイルが変わるか明示的に解析するまで候補にしない。
        """
        rows = conn.execute(
            """SELECT v.id FROM volumes v JOIN sources s ON s.id = v.source_id
               JOIN library_roots r ON r.id = s.root_id
               WHERE v.container_path IS NOT NULL AND v.parent_id IS NULL AND v.inner_path IS NULL
                 AND v.analyzed_version IS NULL AND v.missing = 0 AND s.missing = 0 AND s.offline = 0
                 AND r.online = 1 AND r.enabled = 1
               ORDER BY v.id""").fetchall()
        for (volume_id,) in rows:
            try:
                self.analyze(conn, volume_id)
            except (VolumeOffline, VolumeNotFound):
                continue
            except (VolumeUnreadable, CacheTooLarge) as e:
                analysis.record_error(conn, volume_id, str(e))
                log.info("巻 %s を解析できません: %s", volume_id, e)
            return volume_id
        return None


def _units(found, container: str) -> list[analysis.Unit]:
    """(リーダー, エントリ, 入れ子の名前) のページ列から巻の単位を作る。

    単位 = 画像を直接含むフォルダ(アーカイブの直下なら、そのアーカイブ自体)。並びは入れ子の名前・フォルダの自然順。
    """
    groups: dict[analysis.UnitKey, list[str]] = {}
    for _, e, chain in found:
        groups.setdefault((chain, e.parent), []).append(e.name)
    units = []
    for (chain, prefix), names in groups.items():
        if prefix:
            title = PurePosixPath(prefix.rstrip("/")).name
        else:
            title = PurePosixPath(chain[-1] if chain else container).stem
        units.append(analysis.Unit(chain, prefix, title, sorted(names, key=page_sort_key)))
    units.sort(key=lambda u: (natural_key("/".join(u.chain)), natural_key(u.prefix)))
    return units
