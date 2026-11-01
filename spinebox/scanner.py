"""蔵書フォルダのスキャン。

各ルートを走査して、次を 1 巻の候補として volumes に登録する。

- (a) 画像を直接含むフォルダ
- (b) zip/cbz の中で画像を直接含むフォルダごと(1 アーカイブに複数巻フォルダがあればフォルダ単位)
- (c) アーカイブの中の内側アーカイブ。**取り出さない・読まない**(目次に名前が出るだけ)。
  ページ数は開くまで NULL
- (d) 小説: `.epub`(zip の目次だけ読んで危険な名前・上限超え・container.xml の有無を見る。本文は読まない)と
  `.txt`(中身を読まない。文字コードは開いたときに判定する)。画像のあるフォルダの `.txt` は添え物として扱わない。
  アーカイブの中の `.epub` / `.txt` は巻にしない(漫画のアーカイブに添えられた説明書きを小説と取り違えないため)

蔵書には読み取りしかしない。サイズ・mtime・SCAN_VERSION が前回と同じアーカイブは開き直さない。
ルートが見えない・読めないときはオフラインにするだけで、レコードは消さない。見つからなくなった
ソース・巻も missing の印を付けるだけで消さない(読書位置ごと失わないため)。
整理処理の作業場・退避フォルダ(`Settings.organize_base`。既定 `<ルート>/.spinebox`)は走査しない。
"""
from __future__ import annotations

import os
import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from . import db
from .archive import (ArchiveError, LimitExceeded, UnsupportedArchive, is_archive_name, list_entries,
                      open_archive)
from .archive.safety import Skip, depth_problem
from .config import DEFAULT_ORGANIZE_DIR, Settings
from .naming import parse_number
from .novel import FORMAT_EPUB, novel_format
from .novel.epub import scan_problems as epub_scan_problems
from .pages import is_image

# 巻の切り出し方を変えたら上げる(同じサイズ・mtime のアーカイブも読み直させるため)
# 2: 巻の名前から番号の候補(number_*)を記録する(#30)
SCAN_VERSION = 2

# 一覧に出る巻の条件。分解済みの親(見えている子を持つ巻)は出さず、子を並べる。親が消えた子も出さない
LISTED_VOLUMES_JOIN = """
FROM volumes v JOIN sources s ON s.id = v.source_id JOIN library_roots r ON r.id = s.root_id
LEFT JOIN volumes pv ON pv.id = v.parent_id
"""
LISTED_VOLUMES_WHERE = """
WHERE r.enabled = 1 AND v.missing = 0 AND s.missing = 0
  AND (v.parent_id IS NULL OR pv.missing = 0)
  AND NOT EXISTS (SELECT 1 FROM volumes c WHERE c.parent_id = v.id AND c.missing = 0)
  AND v.organized_op_id IS NULL
"""


@dataclass
class ScanResult:
    volumes: int
    offline_roots: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class VolumeSpec:
    container_path: str | None
    entry_prefix: str
    title: str
    name_raw: bytes
    name_encoding: str
    page_count: int | None
    format: str | None = None  # 小説なら 'epub' / 'text'


def _fs_name_bytes(name: str) -> bytes:
    return name.encode("utf-8", "surrogateescape")


def _rel(root: Path, path: str) -> str:
    rel = os.path.relpath(path, root)
    return "" if rel == "." else rel.replace(os.sep, "/")


def _norm(path: str) -> str:
    return os.path.normcase(os.path.normpath(os.path.abspath(path)))


def root_is_accessible(root: Path) -> bool:
    try:
        if not root.is_dir():
            return False
        with os.scandir(root):
            return True
    except OSError:
        return False


def resolve_source_path(root: Path, rel_path: str) -> Path:
    """DB の相対パスをルート配下の絶対パスにする。ルートの外を指す値は拒む。"""
    root_norm = os.path.normpath(os.path.abspath(root))
    full = os.path.normpath(os.path.join(root_norm, *rel_path.split("/"))) if rel_path else root_norm
    if full != root_norm and not full.startswith(root_norm.rstrip(os.sep) + os.sep):
        raise ValueError(f"ルートの外を指すパス: {rel_path!r}")
    return Path(full)


def archive_volume_specs(listing, archive_name: str, limits) -> tuple[list[VolumeSpec], list[Skip]]:
    """検証済みの目次から巻の候補を切り出す。内側アーカイブは名前を見るだけ。"""
    specs: list[VolumeSpec] = []
    skipped: list[Skip] = []
    groups: dict[str, list] = defaultdict(list)
    for e in listing.entries:
        if is_image(e.name):
            groups[e.parent].append(e)
    dirs = {d.name: d for d in listing.dirs}
    for prefix, pages in groups.items():
        if prefix:
            defining = dirs.get(prefix) or min(pages, key=lambda e: e.index)
            title = PurePosixPath(prefix.rstrip("/")).name
            raw, enc = defining.raw_name, defining.encoding
        else:
            title = PurePosixPath(archive_name).stem
            raw, enc = _fs_name_bytes(archive_name), "filesystem"
        specs.append(VolumeSpec(None, prefix, title, raw, enc, len(pages)))
    for e in listing.entries:
        if not is_archive_name(e.name):
            continue
        problem = depth_problem(1, limits)
        if problem:
            skipped.append(Skip(e.name, problem))
            continue
        specs.append(VolumeSpec(e.name, "", PurePosixPath(e.name).stem, e.raw_name, e.encoding, None))
    return specs, skipped


class Scanner:
    def __init__(self, conn: sqlite3.Connection, settings: Settings):
        self.conn = conn
        self.settings = settings

    # --- ルート -------------------------------------------------------------

    def _sync_roots(self) -> list[tuple[int, Path]]:
        configured = [Path(p) for p in self.settings.library_roots]
        paths = [str(p) for p in configured]
        with db.transaction(self.conn) as c:
            for p in paths:
                c.execute("INSERT OR IGNORE INTO library_roots (path) VALUES (?)", (p,))
            c.execute("UPDATE library_roots SET enabled = 0")
            c.executemany("UPDATE library_roots SET enabled = 1 WHERE path = ?", [(p,) for p in paths])
        ids = dict(self.conn.execute("SELECT path, id FROM library_roots").fetchall())
        return [(ids[str(p)], p) for p in configured]

    def scan(self) -> ScanResult:
        result = ScanResult(volumes=0)
        for root_id, root in self._sync_roots():
            if root_is_accessible(root):
                self._scan_root(root_id, root)
            else:
                self._mark_root_offline(root_id)
                result.offline_roots.append(str(root))
        result.volumes = self.conn.execute(
            "SELECT COUNT(*) " + LISTED_VOLUMES_JOIN + LISTED_VOLUMES_WHERE).fetchone()[0]
        return result

    def _excluded_dirs(self, root: Path) -> set[str]:
        bases = [root / DEFAULT_ORGANIZE_DIR, self.settings.organize_base(root)]
        bases += [self.settings.organize_base(Path(r)) for r in self.settings.library_roots]
        return {_norm(str(b)) for b in bases}

    def _mark_root_offline(self, root_id: int) -> None:
        with db.transaction(self.conn) as c:
            c.execute("UPDATE library_roots SET online = 0, last_checked_at = ? WHERE id = ?", (db.now(), root_id))
            c.execute("UPDATE sources SET offline = 1 WHERE root_id = ?", (root_id,))

    def _scan_root(self, root_id: int, root: Path) -> None:
        found: set[str] = set()
        error_dirs: list[str] = []

        def onerror(err: OSError) -> None:
            if err.filename:
                error_dirs.append(_rel(root, err.filename))

        excluded = self._excluded_dirs(root)
        for dirpath, dirnames, filenames in os.walk(root, onerror=onerror, followlinks=False):
            # 整理処理の作業場・退避フォルダ(既定 <ルート>/.spinebox)は蔵書として扱わない
            dirnames[:] = sorted(d for d in dirnames if _norm(os.path.join(dirpath, d)) not in excluded)
            images = sorted(f for f in filenames if is_image(f))
            if images:
                rel = _rel(root, dirpath)
                found.add(rel)
                self._scan_folder(root_id, root, rel, Path(dirpath), images)
            for name in sorted(filenames):
                fmt = novel_format(name)
                if is_archive_name(name) or (fmt == FORMAT_EPUB or (fmt and not images)):
                    rel = _rel(root, os.path.join(dirpath, name))
                    found.add(rel)
                    if fmt:
                        self._scan_novel(root_id, rel, Path(dirpath) / name, fmt)
                    else:
                        self._scan_archive(root_id, rel, Path(dirpath) / name)

        def under_error_dir(rel: str) -> bool:
            return any(e == "" or rel == e or rel.startswith(e + "/") for e in error_dirs)

        with db.transaction(self.conn) as c:
            for sid, rel in c.execute("SELECT id, rel_path FROM sources WHERE root_id = ?", (root_id,)).fetchall():
                if rel not in found and not under_error_dir(rel):
                    c.execute("UPDATE sources SET missing = 1, offline = 0 WHERE id = ?", (sid,))
            now = db.now()
            c.execute("UPDATE library_roots SET online = 1, last_checked_at = ?, last_online_at = ? WHERE id = ?",
                      (now, now, root_id))

    # --- ソース -------------------------------------------------------------

    def _upsert_source(self, c, root_id: int, rel: str, kind: str) -> sqlite3.Row:
        c.execute("INSERT OR IGNORE INTO sources (root_id, rel_path, kind) VALUES (?, ?, ?)", (root_id, rel, kind))
        c.execute("UPDATE sources SET offline = 0, missing = 0, kind = ? WHERE root_id = ? AND rel_path = ?",
                  (kind, root_id, rel))
        return c.execute("SELECT * FROM sources WHERE root_id = ? AND rel_path = ?", (root_id, rel)).fetchone()

    def _scan_folder(self, root_id: int, root: Path, rel: str, path: Path, images: list[str]) -> None:
        try:
            st = path.stat()
            size = sum((path / f).stat().st_size for f in images)
        except OSError:
            return
        name = path.name if rel else (root.name or str(root))
        spec = VolumeSpec(None, "", name, _fs_name_bytes(name), "filesystem", len(images))
        with db.transaction(self.conn) as c:
            src = self._upsert_source(c, root_id, rel, "folder")
            self._replace_volumes(c, src["id"], [spec])
            c.execute("UPDATE sources SET size = ?, mtime_ns = ?, scan_version = ?, scanned_at = ? WHERE id = ?",
                      (size, st.st_mtime_ns, SCAN_VERSION, db.now(), src["id"]))

    def _scan_archive(self, root_id: int, rel: str, path: Path) -> None:
        try:
            st = path.stat()
        except OSError:
            return
        with db.transaction(self.conn) as c:
            src = self._upsert_source(c, root_id, rel, "archive")
        if (src["size"] == st.st_size and src["mtime_ns"] == st.st_mtime_ns
                and src["scan_version"] == SCAN_VERSION):
            return  # 前回から変わっていない。開かない
        issues: list[Skip] = []
        specs: list[VolumeSpec] | None = None
        try:
            with open_archive(path, sevenzip_path=self.settings.sevenzip_path) as reader:
                listing = list_entries(reader, self.settings.limits)
            issues.extend(listing.skipped)
            specs, more = archive_volume_specs(listing, path.name, self.settings.limits)
            issues.extend(more)
        except UnsupportedArchive as e:
            issues.append(Skip(None, str(e)))
        except LimitExceeded as e:
            issues.append(Skip(None, f"展開上限によりスキップ: {e}"))
        except ArchiveError as e:
            issues.append(Skip(None, f"読めないアーカイブ: {e}"))
        with db.transaction(self.conn) as c:
            record_issues(c, src["id"], None, issues)
            if specs is not None:
                self._replace_volumes(c, src["id"], specs)
                c.execute("UPDATE sources SET size = ?, mtime_ns = ?, scan_version = ?, scanned_at = ? WHERE id = ?",
                          (st.st_size, st.st_mtime_ns, SCAN_VERSION, db.now(), src["id"]))
            # 読めなかったときはサイズ・mtime を更新しない(次のスキャンで再挑戦する。既存の巻はそのまま)

    def _scan_novel(self, root_id: int, rel: str, path: Path, fmt: str) -> None:
        """小説のファイルを 1 巻として登録する。EPUB は目次だけ読み、テキストは中身を読まない。"""
        try:
            st = path.stat()
        except OSError:
            return
        with db.transaction(self.conn) as c:
            src = self._upsert_source(c, root_id, rel, "archive")
        if (src["size"] == st.st_size and src["mtime_ns"] == st.st_mtime_ns
                and src["scan_version"] == SCAN_VERSION):
            return
        issues: list[Skip] = []
        specs: list[VolumeSpec] | None = None
        spec = VolumeSpec(None, "", path.stem, _fs_name_bytes(path.name), "filesystem", None, fmt)
        if fmt == FORMAT_EPUB:
            try:
                with open_archive(path, sevenzip_path=self.settings.sevenzip_path) as reader:
                    listing = list_entries(reader, self.settings.limits)
                issues = epub_scan_problems(listing.entries, listing.skipped)
                specs = [] if issues else [spec]  # 危険な名前を含む・container.xml の無い EPUB は巻にしない
            except LimitExceeded as e:
                issues.append(Skip(None, f"展開上限によりスキップ: {e}"))
            except ArchiveError as e:
                issues.append(Skip(None, f"読めない EPUB: {e}"))
        else:
            specs = [spec]
        with db.transaction(self.conn) as c:
            record_issues(c, src["id"], None, issues)
            if specs is not None:
                self._replace_volumes(c, src["id"], specs)
                c.execute("UPDATE sources SET size = ?, mtime_ns = ?, scan_version = ?, scanned_at = ? WHERE id = ?",
                          (st.st_size, st.st_mtime_ns, SCAN_VERSION, db.now(), src["id"]))

    def _replace_volumes(self, c, source_id: int, specs: list[VolumeSpec]) -> None:
        """巻を (container_path, entry_prefix) で突き合わせて更新する。消えた巻は missing にするだけ。

        突き合わせるのはスキャンが切り出す巻(親を持たない巻)だけ。解析で作った子の巻はここでは触らない
        (親の解析をやり直したときに突き合わせる。spinebox/analysis.py)。ソースを読み直したので、
        内側アーカイブの巻は未解析に戻す(子の巻は残し、次に開いたときに同じ単位へ突き合わせ直す)。
        """
        now = db.now()
        existing = {(r["container_path"], r["entry_prefix"]): r["id"]
                    for r in c.execute("""SELECT id, container_path, entry_prefix FROM volumes
                                          WHERE source_id = ? AND parent_id IS NULL""", (source_id,))}
        seen = set()
        for s in specs:
            key = (s.container_path, s.entry_prefix)
            seen.add(key)
            num = parse_number(s.title)
            numbers = (num.kind, num.start, num.end, num.evidence) if num else (None, None, None, None)
            if key in existing:
                c.execute("""UPDATE volumes SET title = ?, name_raw = ?, name_encoding = ?, page_count = ?,
                             number_kind = ?, number_start = ?, number_end = ?, number_evidence = ?,
                             analyzed_version = NULL, analysis_error = NULL, format = ?,
                             missing = 0, updated_at = ? WHERE id = ?""",
                          (s.title, s.name_raw, s.name_encoding, s.page_count, *numbers, s.format, now,
                           existing[key]))
            else:
                c.execute("""INSERT INTO volumes (source_id, container_path, entry_prefix, title, name_raw,
                             name_encoding, page_count, number_kind, number_start, number_end, number_evidence,
                             format, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                          (source_id, s.container_path, s.entry_prefix, s.title, s.name_raw, s.name_encoding,
                           s.page_count, *numbers, s.format, now, now))
        for key, vid in existing.items():
            if key not in seen:
                c.execute("UPDATE volumes SET missing = 1, updated_at = ? WHERE id = ?", (now, vid))


def record_issues(c, source_id: int, container_path: str | None, issues: list[Skip]) -> None:
    """そのソース(と内側アーカイブ)の記録を今回の結果で置き換える。scan_issues は再導出できる記録なので消してよい。"""
    c.execute("DELETE FROM scan_issues WHERE source_id = ? AND container_path IS ?", (source_id, container_path))
    now = db.now()
    c.executemany(
        "INSERT INTO scan_issues (source_id, container_path, entry_name, reason, recorded_at) VALUES (?, ?, ?, ?, ?)",
        [(source_id, container_path, s.entry_name, s.reason, now) for s in issues])
