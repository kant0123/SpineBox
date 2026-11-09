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

7z / rar のページと目次は RAM 層(`MemoryCache`、設定 `memory_cache_limit_bytes`)に置く。ページは、要求された
ページから先の数ページを 1 回の 7-Zip 起動でまとめて取り出して置く(ソリッド 7z は 1 ページごとに塊の先頭から
展開し直すため)。zip のページは置かない(毎回読んでも速く、置いても差が出なかった。計測は scripts/bench_reading.py)。
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path
from pathlib import PurePosixPath
from typing import Callable, Iterator

from . import analysis, db
from .archive import (ArchiveError, LimitExceeded, UnsupportedArchive, is_archive_name, list_entries,
                      open_archive)
from .archive.base import detect_format, inner_reader_problem
from .archive.safety import Skip, depth_problem, entry_limit_problem
from .cache import CacheTooLarge, ExtractionCache, MemoryCache, NotCached, cache_key
from .config import Settings
from .mojibake import fix_mojibake
from .pages import is_cover, is_image, natural_key, page_sort_key, sort_pages
from .scanner import _is_non_content_path, fragment_reason, record_issues, resolve_source_path, root_is_accessible


class VolumeNotFound(Exception):
    """その id の巻が無い(または見つからなくなった)。"""


class VolumeOffline(Exception):
    """巻のあるルートが今見えない。"""


class VolumeUnreadable(Exception):
    """巻を読めない(未対応の形式・壊れている・上限超過)。"""


class NeedsExtraction(Exception):
    """`open(extract=False)` で、開くには内側アーカイブの取り出しか 7-Zip の起動が要る巻。"""


log = logging.getLogger(__name__)

# 7z / rar の先読み: 要求されたページから先を、まとめて取り出す。枚数は前回の取り出しの続きを読むたびに 1 → 2 → 4 … と
# 倍にして READAHEAD_PAGES まで増やし、続きでない(開き直した・飛んだ)ら 1 に戻す
# (最初から多く取ると、開いて最初のページが出るまでが遅くなる)。
# バイト数は READAHEAD_BYTES と RAM 層の上限の 1/4 の小さい方まで
READAHEAD_PAGES = 64
READAHEAD_BYTES = 128 * 1024 ** 2
# 書庫ごとの状態(先読みの枚数・直列化のロック)を持つ数。超えたら使用中でないものを捨てる(捨てても 1 枚から始まるだけ)
MAX_ARCHIVE_STATES = 1024


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


def _is_hidden(row: sqlite3.Row | None) -> bool:
    # 整理処理で CBZ に置き換えた巻(organized_op_id)は、行を残したまま見えない扱いにする
    return (row is None or bool(row["missing"] or row["source_missing"] or not row["root_enabled"]
            or row["parent_missing"] or row["organized_op_id"] is not None))


def get_volume_row(conn: sqlite3.Connection, volume_id: int) -> sqlite3.Row:
    row = conn.execute(VOLUME_QUERY, (volume_id,)).fetchone()
    if _is_hidden(row):
        raise VolumeNotFound(volume_id)
    return row


def get_volume_rows(conn: sqlite3.Connection, volume_ids: list[int]) -> dict[int, sqlite3.Row]:
    """見える巻の行を、500 件ずつ IN でまとめて引く(get_volume_row と同じ見え方。見えない巻は含めない)。
    多数の巻を 1 件ずつ引くと巻の数だけ SELECT を発行するので、全作品を見る処理(統計)はこちらを使う。"""
    out: dict[int, sqlite3.Row] = {}
    ids = list(dict.fromkeys(volume_ids))
    base = VOLUME_QUERY.replace("WHERE v.id = ?", "WHERE v.id IN ({})")
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        for row in conn.execute(base.format(",".join("?" * len(chunk))), chunk):
            if not _is_hidden(row):
                out[row["id"]] = row
    return out


def is_analyzable(row) -> bool:
    """分解の対象か: スキャンが切り出した内側アーカイブの巻だけ(子の巻・フォルダの巻は対象外)。"""
    return row["container_path"] is not None and row["parent_id"] is None and row["inner_path"] is None


def is_offline(row: sqlite3.Row) -> bool:
    return bool(row["source_offline"] or not row["root_online"])


class Library:
    def __init__(self, settings: Settings, cache: ExtractionCache, memory: MemoryCache | None = None):
        self.settings = settings
        self.cache = cache
        self.memory = memory if memory is not None else MemoryCache(settings.memory_cache_limit_bytes)
        # 書庫(キー)ごとの取り出しの直列化。フロントは前後のページを並行で先読みするので、同じ書庫の
        # まとめ取り出しを重ねて起動しない(後の要求は先の取り出しが RAM に置いたものを使う)
        self._archives: dict[str, _ArchiveState] = {}
        self._archives_guard = threading.Lock()

    @contextmanager
    def open(self, conn: sqlite3.Connection, volume_id: int, *, extract: bool = True) -> Iterator[OpenVolume]:
        """巻を開く。extract=False なら、取り出し・7-Zip が要る巻は開かずに NeedsExtraction にする。

        extract=False で開けるのは、フォルダの巻、zip の中の画像を直接読む巻、展開キャッシュに取り出し済みの
        内側 zip の巻だけ(一覧の表紙のように、巻の数だけ呼ばれうるところで使う。spinebox/thumbs.py)。
        """
        row = get_volume_row(conn, volume_id)
        if row["format"] is not None and row["format"] != "pdf":
            # 小説(EPUB・テキスト)はページ画像を持たない。開くのは spinebox/novel/books.py
            raise VolumeUnreadable("小説の巻にはページ画像がありません")
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
            elif row["format"] == "pdf":
                opened = self._open_pdf(volume_id, path, version)
            else:
                if not extract and detect_format(path) != "zip":
                    raise NeedsExtraction(volume_id)
                opened = self._open_archive_volume(conn, stack, row, path, version, extract)
            if row["page_count"] != len(opened.page_names):
                with db.transaction(conn) as c:
                    c.execute("UPDATE volumes SET page_count = ? WHERE id = ?", (len(opened.page_names), volume_id))
            yield opened

    def _open_pdf(self, volume_id: int, path: Path, version: str) -> OpenVolume:
        """未整理の PDF の巻。整理の抽出(organize/pdf.py)をページ単位で流用し、元の PDF は読むだけ。
        ページ名は連番の .jpg(実際の形式は抽出まで分からない。配信側が中身で見分ける)。"""
        from .organize.pdf import PdfError, extract_pdf_page_image
        import pypdf
        try:
            reader = pypdf.PdfReader(str(path))
            if reader.is_encrypted:
                raise VolumeUnreadable("暗号化された PDF は開けません")
            count = len(reader.pages)
        except VolumeUnreadable:
            raise
        except Exception as e:  # noqa: BLE001
            raise VolumeUnreadable(f"読めない PDF: {type(e).__name__}: {e}") from e
        if count == 0:
            raise VolumeUnreadable("ページがありません")
        names = [f"{i + 1:04d}.jpg" for i in range(count)]
        lock = self._archive(cache_key("pdf", str(path), version)).lock
        memory = self.memory

        def read(i: int) -> bytes:
            key = cache_key("pdfpage", str(path), version, i)
            data = memory.get(key) if memory.enabled else None
            if data is not None:
                return data
            try:
                with lock:  # pypdf のリーダーは並行で使わない(先読みの並行要求を直列にする)
                    _, data = extract_pdf_page_image(reader.pages[i], i, pdf_path=path)
            except (PdfError, Exception) as e:  # noqa: BLE001
                raise VolumeUnreadable(f"PDF の {i + 1} ページ目を取り出せません: {e}") from e
            if memory.enabled:
                memory.put(key, data)
            return data

        return OpenVolume(volume_id, names, version, read)

    def _open_archive_volume(self, conn, stack: ExitStack, row, path: Path, version: str,
                             extract: bool = True) -> OpenVolume:
        limits = self.settings.limits
        sevenzip = self.settings.sevenzip_path
        units = page_units = None
        try:
            root_key = self._root_key(path)
            outer = stack.enter_context(open_archive(path, sevenzip_path=sevenzip))
            listing = self._list(outer, root_key)
            container = row["container_path"]
            if container is None:
                prefix = row["entry_prefix"]
                entries = sorted((e for e in listing.entries if e.parent == prefix and is_image(e.name)),
                                 key=lambda e: page_sort_key(e.name))
                pages = [(outer, e, root_key) for e in entries]
            elif row["inner_path"] is not None:
                pages = self._open_unit(stack, row, root_key, outer, listing, extract)
            else:
                found = self._open_inner(conn, stack, row, root_key, outer, listing, extract)
                pages = [(r, e, k) for r, e, _, k in found]
                page_units = [(chain, e.parent) for _, e, chain, _ in found]
                units = _units(found, container)
        except (UnsupportedArchive, LimitExceeded, ArchiveError) as e:
            raise VolumeUnreadable(str(e)) from e

        def read(i: int) -> bytes:
            try:
                return self._read_page(pages, i)
            except (LimitExceeded, ArchiveError) as e:
                raise VolumeUnreadable(str(e)) from e

        return OpenVolume(row["id"], [e.name for _, e, _ in pages], version, read, units, page_units)

    def _root_key(self, path: Path) -> str:
        st = path.stat()
        return cache_key("inner", str(path), st.st_size, st.st_mtime_ns)

    # --- RAM 層 -------------------------------------------------------------

    def _list(self, reader, key: str):
        """目次を読む。7z / rar は `7z l` の結果を RAM 層に持ち越す(key は中身が変われば変わるキー)。"""
        memory = self.memory
        keep = memory.enabled and hasattr(reader, "restore_listing")
        listing_key = cache_key("listing", key)
        hit = False
        if keep:
            state = memory.get(listing_key)
            if state is not None:
                reader.restore_listing(state)
                hit = True
        listing = list_entries(reader, self.settings.limits)
        if keep and not hit:
            state = reader.listing_state()
            memory.put(listing_key, state, _listing_size(state[0]))
        return listing

    def _archive(self, key: str) -> _ArchiveState:
        with self._archives_guard:
            state = self._archives.get(key)
            if state is None:
                if len(self._archives) >= MAX_ARCHIVE_STATES:
                    for k in [k for k, v in self._archives.items() if not v.lock.locked()]:
                        del self._archives[k]
                state = self._archives[key] = _ArchiveState()
            return state

    def _read_page(self, pages, i: int) -> bytes:
        """i ページ目のバイト列。7z / rar は RAM 層にあればそれを返し、無ければ先の数ページもまとめて取り出して置く。"""
        limits = self.settings.limits
        memory = self.memory
        reader, entry, key = pages[i]
        if not memory.enabled or not reader.batch_reads:
            return reader.read_entry(entry, limits)
        page_key = _page_key(key, entry)
        data = memory.get(page_key)
        if data is not None:
            return data
        archive = self._archive(key)
        with archive.lock:
            data = memory.get(page_key)
            if data is not None:
                return data  # 並行した要求の先読みで置かれた
            window = archive.next_window(i)
            batch = self._readahead(pages, i, window)
            archive.advance(i + len(batch), window)
            try:
                got = reader.read_entries([e for e, _ in batch], limits)
            except ArchiveError as e:
                # 先読みの側(要求されていないページ)の失敗で、要求されたページまで読めなくしない
                log.info("まとめ取り出しに失敗したので 1 ページだけ読みます: %s", e)
                data = reader.read_entry(entry, limits)
                memory.put(page_key, data)
                return data
            for e, k in batch:
                memory.put(k, got[e.index])
            return got[entry.index]

    def _readahead(self, pages, i: int, window: int) -> list:
        """i ページ目と、同じ書庫で RAM 層にまだ無い先のページ((エントリ, ページのキー) の列。window 枚まで)。"""
        reader, entry, key = pages[i]
        budget = min(READAHEAD_BYTES, self.memory.limit_bytes // 4)
        batch = [(entry, _page_key(key, entry))]
        total = entry.size
        for j in range(i + 1, len(pages)):
            r, e, k = pages[j]
            if r is not reader or len(batch) >= window or total + e.size > budget:
                break
            pk = _page_key(k, e)
            if pk in self.memory:
                continue
            batch.append((e, pk))
            total += e.size
        return batch

    def _container_entry(self, row, listing):
        matches = [e for e in listing.entries if e.name == row["container_path"]]
        if not matches:
            raise VolumeNotFound(row["id"])
        return matches[0]

    def _open_inner(self, conn, stack, row, root_key: str, outer, listing, extract: bool = True):
        """内側アーカイブを取り出して開き、(リーダー, エントリ, 入れ子の名前, リーダーのキー) のページ列を返す。

        内側にさらにアーカイブがあり、深さの上限に収まるなら、それも開いて名前の自然順に続ける
        (7z の中の rar の中の rar の中の zip、という実データがある)。入れ子の名前は、内側アーカイブから
        そのページを含むアーカイブまでの名前の並び(巻の単位を見分けるのに使う)。
        """
        container = row["container_path"]
        issues: list[Skip] = []
        pages = self._expand(stack, outer, self._container_entry(row, listing), root_key, 1, (), issues, extract)
        with db.transaction(conn) as c:
            record_issues(c, row["source_id"], container, issues)
        return pages

    def _open_unit(self, stack, row, root_key: str, outer, listing, extract: bool = True):
        """子の巻: 親の内側アーカイブから inner_path の入れ子を順に取り出し、entry_prefix 直下の画像を返す。"""
        try:
            chain = json.loads(row["inner_path"])
        except ValueError as e:
            raise VolumeNotFound(row["id"]) from e
        reader, inner_listing, key = self._extract(stack, outer, self._container_entry(row, listing), root_key, 1,
                                                   extract)
        for depth, name in enumerate(chain, start=2):
            matches = [e for e in inner_listing.entries if e.name == name and is_archive_name(e.name)]
            if not matches:
                raise VolumeNotFound(row["id"])
            reader, inner_listing, key = self._extract(stack, reader, matches[0], key, depth, extract)
        prefix = row["entry_prefix"]
        images = sorted((e for e in inner_listing.entries if e.parent == prefix and is_image(e.name)),
                        key=lambda e: page_sort_key(e.name))
        if not images:
            raise VolumeNotFound(row["id"])
        return [(reader, e, key) for e in images]

    def _extract(self, stack, parent, entry, parent_key: str, depth: int, extract: bool = True):
        """1 段の内側アーカイブをキャッシュへ取り出して開く。(リーダー, 目次, キー) を返す。

        キーは親のキーとエントリ名・番号から作るので、親の巻を開いたときと子の巻を開いたときで同じになる。
        """
        limits = self.settings.limits
        sevenzip = self.settings.sevenzip_path
        problem = depth_problem(depth, limits)
        if problem:
            raise LimitExceeded(problem)
        problem = entry_limit_problem(entry.size, limits)
        if problem:
            raise LimitExceeded(problem)
        # 取り出してから未対応と分かるのは無駄なので先に判定する。名前ではなく先頭の中身で見る(#62)
        problem = inner_reader_problem(parent, entry, sevenzip)
        if problem:
            raise UnsupportedArchive(f"内側アーカイブを読めない: {entry.name}: {problem}")
        key = cache_key("inner", parent_key, entry.name, entry.index)
        if extract:
            cached = stack.enter_context(
                self.cache.use(key, lambda f: parent.copy_entry(entry, f, limits.max_entry_bytes)))
        else:
            try:
                cached = stack.enter_context(self.cache.use_existing(key))
            except NotCached:
                raise NeedsExtraction(entry.name) from None
            if detect_format(cached) != "zip":
                raise NeedsExtraction(entry.name)  # 7z / rar の目次は 7-Zip の起動が要る
        inner = stack.enter_context(open_archive(cached, entry.name, sevenzip_path=sevenzip))
        return inner, self._list(inner, key), key

    def _expand(self, stack, parent, entry, parent_key: str, depth: int, chain: tuple[str, ...],
                issues: list[Skip], extract: bool = True):
        limits = self.settings.limits
        inner, listing, key = self._extract(stack, parent, entry, parent_key, depth, extract)
        label = "" if depth == 1 else entry.name + "/"
        issues.extend(Skip(label + (s.entry_name or ""), s.reason) if label else s for s in listing.skipped)
        images = sorted((e for e in listing.entries if is_image(e.name)), key=lambda e: page_sort_key(e.name))
        pages = [(inner, e, chain, key) for e in images]
        for nested in sorted((e for e in listing.entries if is_archive_name(e.name)),
                             key=lambda e: natural_key(e.name)):
            problem = depth_problem(depth + 1, limits)
            if problem:
                issues.append(Skip(f"{entry.name}/{nested.name}" if depth > 1 else nested.name, problem))
                continue
            pages += self._expand(stack, inner, nested, key, depth + 1, chain + (nested.name,), issues, extract)
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

    @staticmethod
    def pending_analysis(conn: sqlite3.Connection) -> list[int]:
        """未解析の内側アーカイブの巻の id(見えているルートのものだけ。解析の後に新しく増えることはない)。"""
        return [r[0] for r in conn.execute(
            """SELECT v.id FROM volumes v JOIN sources s ON s.id = v.source_id
               JOIN library_roots r ON r.id = s.root_id
               WHERE v.container_path IS NOT NULL AND v.parent_id IS NULL AND v.inner_path IS NULL
                 AND v.analyzed_version IS NULL AND v.missing = 0 AND s.missing = 0 AND s.offline = 0
                 AND r.online = 1 AND r.enabled = 1
               ORDER BY v.id""")]

    def analyze_next(self, conn: sqlite3.Connection) -> int | None:
        """未解析の内側アーカイブの巻を 1 つ解析する(裏で少しずつ解析する用)。解析した巻の id を返す。

        オフライン・見つからない巻は飛ばす(記録しない。再接続後にまた候補になる)。読めなかった巻は
        理由を analysis_error に残し、元ファイルが変わるか明示的に解析するまで候補にしない。
        """
        for volume_id in self.pending_analysis(conn):
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
    """(リーダー, エントリ, 入れ子の名前, キー) のページ列から巻の単位を作る。

    単位 = 画像を直接含むフォルダ(アーカイブの直下なら、そのアーカイブ自体)。並びは入れ子の名前・フォルダの自然順。
    """
    groups: dict[analysis.UnitKey, list[str]] = {}
    for _, e, chain, _ in found:
        if _is_non_content_path(e.parent):
            continue
        groups.setdefault((chain, e.parent), []).append(e.name)
    units = []
    for (chain, prefix), names in groups.items():
        if all(is_cover(n) for n in names):
            continue
        # 名前の文字化けはスキャンの切り出しと同じく直す(spinebox/scanner.py archive_volume_specs())
        if prefix:
            title = fix_mojibake(PurePosixPath(prefix.rstrip("/")).name)[0]
        else:
            title = fix_mojibake(PurePosixPath(chain[-1] if chain else container).stem)[0]
        units.append(analysis.Unit(chain, prefix, title, sorted(names, key=page_sort_key)))
    # 目次・表紙だけの小さな束は本の断片なので巻にしない(ほかに巻があるときだけ。スキャンと同じ規則)
    keep = [u for u in units if not (u.prefix and fragment_reason(u.title, len(u.page_names)))]
    if keep:
        units = keep
    units.sort(key=lambda u: (natural_key("/".join(u.chain)), natural_key(u.prefix)))
    return units


class _ArchiveState:
    """書庫ごとの取り出しの状態。lock: 同じ書庫のまとめ取り出しを直列化する。

    前回の取り出しの続き(前回の末尾から READAHEAD_PAGES 以内。フロントは前後を並行で先読みするので少し先も続きとみなす)を
    要求されたら枚数を倍にし、そうでなければ 1 枚から始める。
    """

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self._end: int | None = None
        self._window = 1

    def next_window(self, index: int) -> int:
        if self._end is not None and self._end <= index <= self._end + READAHEAD_PAGES:
            return self._window
        return 1

    def advance(self, end: int, used: int) -> None:
        self._end = end
        self._window = min(used * 2, READAHEAD_PAGES)


def _page_key(reader_key: str, entry) -> str:
    return cache_key("page", reader_key, entry.index, entry.name)


def _listing_size(entries) -> int:
    """目次を RAM 層に置くときの大きさの見積もり(名前の長さ + 1 エントリあたりの固定分)。"""
    return sum(len(e.name) * 2 + len(e.raw_name) + 256 for e in entries) + 1024
