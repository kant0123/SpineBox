"""蔵書フォルダのスキャン。

各ルートを走査して、次を 1 巻の候補として volumes に登録する。

- (a) 画像を直接含むフォルダ
- (b) zip/cbz の中で画像を直接含むフォルダごと(1 アーカイブに複数巻フォルダがあればフォルダ単位)
- (c) アーカイブの中の内側アーカイブ。**取り出さない・読まない**(目次に名前が出るだけ)。
  ページ数は開くまで NULL

蔵書には読み取りしかしない。サイズ・mtime・SCAN_VERSION が前回と同じアーカイブは開き直さない。
ソース(ファイル・フォルダ)1 つで想定外の例外が出ても、そのソースの問題を scan_issues に記録して
スキャンを最後まで進める(トレースバックはログへ。#48)。DB そのものが使えない sqlite3.OperationalError だけは止める。
ルートが見えない・読めないときはオフラインにするだけで、レコードは消さない。見つからなくなった
ソース・巻も missing の印を付けるだけで消さない(読書位置ごと失わないため)。
整理処理の作業場・退避フォルダ(`Settings.organize_base`。既定 `<ルート>/.spinebox`)は走査しない。
"""
from __future__ import annotations

import logging
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
from .pages import is_image

log = logging.getLogger(__name__)

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


# 画面・API に返す「読めなかったもの」の最大件数(超えた分は issues_total にだけ数える)
MAX_REPORTED_ISSUES = 200


@dataclass
class ScanResult:
    volumes: int
    offline_roots: list[str] = field(default_factory=list)
    # 読めなかったもの(scan_issues の現在の内容)。{"path", "container_path", "entry_name", "reason"}
    issues: list[dict] = field(default_factory=list)
    issues_total: int = 0


@dataclass(frozen=True)
class VolumeSpec:
    container_path: str | None
    entry_prefix: str
    title: str
    name_raw: bytes
    name_encoding: str
    page_count: int | None


def _fs_name_bytes(name: str) -> bytes:
    return name.encode("utf-8", "surrogateescape")


def _rel(root: Path, path: str) -> str:
    rel = os.path.relpath(path, root)
    return "" if rel == "." else rel.replace(os.sep, "/")


def _norm(path: str) -> str:
    return os.path.normcase(os.path.normpath(os.path.abspath(path)))


def safe_text(value) -> str:
    """表示・ログ・JSON に出して安全な文字列にする。

    Windows のファイル名は孤立サロゲート、Linux の名前は不正な UTF-8 バイト(surrogateescape)を含みうる。
    そのまま SQLite・JSON・ログへ渡すと UnicodeEncodeError になるので、エスケープした文字に置き換える。
    """
    return str(value).encode("utf-8", "backslashreplace").decode("utf-8")


def describe_exception(exc: BaseException) -> str:
    return safe_text(f"{type(exc).__name__}: {exc}")


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
    seen_inner: set[str] = set()
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
        if e.name in seen_inner:
            # zip は同名のエントリを複数持てる。巻の突き合わせ(source, container_path, entry_prefix)が一意なので最初の 1 つだけ
            skipped.append(Skip(e.name, "同じ名前の内側アーカイブが複数ある(最初の 1 つだけを巻にした)"))
            continue
        seen_inner.add(e.name)
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
        with db.transaction(self.conn) as c:
            # ソースに結び付かない問題(読めなかったフォルダなど)は毎回の走査で作り直す
            c.execute("DELETE FROM scan_issues WHERE source_id IS NULL")
        for root_id, root in self._sync_roots():
            try:
                if root_is_accessible(root):
                    self._scan_root(root_id, root)
                else:
                    self._mark_root_offline(root_id)
                    result.offline_roots.append(str(root))
            except Exception as e:  # noqa: BLE001 (1 つのルートの想定外で、ほかのルートを止めない)
                self._unexpected(e, f"蔵書フォルダ {safe_text(root)} のスキャンを中断")
                self._record_pathless(str(root), f"スキャンを中断した: {describe_exception(e)}")
        result.volumes = self.conn.execute(
            "SELECT COUNT(*) " + LISTED_VOLUMES_JOIN + LISTED_VOLUMES_WHERE).fetchone()[0]
        result.issues, result.issues_total = collect_issues(self.conn)
        return result

    # --- 想定外の例外 ---------------------------------------------------------
    # 想定した失敗(ArchiveError など)は読み込む側が理由つきで捕まえる。ここに来るのは「想定していなかった」例外で、
    # プログラムの誤りの可能性があるので、必ずログにトレースバックを出し、scan_issues に型とメッセージを残す。
    # DB そのものが使えない(sqlite3.OperationalError: ロック・ディスク満杯・SQL の誤り)ときは、
    # 全ソースが同じ理由で失敗するだけなので握りつぶさず、スキャンを止める。

    @staticmethod
    def _unexpected(exc: Exception, what: str) -> None:
        if isinstance(exc, sqlite3.OperationalError):
            raise exc
        log.error("%s: %s", what, describe_exception(exc), exc_info=exc)

    def _record_pathless(self, path: str, reason: str) -> None:
        """どのソースにも結び付かない問題を記録する。entry_name にはルートも含めた走査中のパスを入れる。"""
        try:
            with db.transaction(self.conn) as c:
                c.execute("INSERT INTO scan_issues (source_id, container_path, entry_name, reason, recorded_at) "
                          "VALUES (NULL, NULL, ?, ?, ?)", (safe_text(str(path).replace("\\", "/")), safe_text(reason), db.now()))
        except Exception as e:  # noqa: BLE001
            self._unexpected(e, f"問題を記録できなかった: {safe_text(path)}")

    def _source_failed(self, root_id: int, path: str, rel: str, kind: str, reason: str,
                       exc: Exception | None = None) -> None:
        """ソース 1 つを読めなかったことを記録する。既存の巻・サイズ・mtime には触れない(次回また試す)。"""
        path = str(path).replace("\\", "/")
        shown = safe_text(path)
        if exc is not None:
            self._unexpected(exc, f"{shown} のスキャンに失敗")
            reason = f"{reason}: {describe_exception(exc)}"
        try:
            with db.transaction(self.conn) as c:
                src = self._upsert_source(c, root_id, rel, kind)
                record_issues(c, src["id"], None, [Skip(None, safe_text(reason))])
        except Exception as e:  # noqa: BLE001 (名前が DB に入らない等。ソースに結び付けずに残す)
            if isinstance(e, sqlite3.OperationalError):
                raise
            log.warning("%s をソースとして記録できない(%s)。ソースに結び付けずに残す", shown, describe_exception(e))
            self._record_pathless(path, reason)

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
        complete = True  # 見つからないソースを missing にしてよいほど、全体を見終えたか

        def onerror(err: OSError) -> None:
            nonlocal complete
            try:
                if err.filename:
                    error_dirs.append(_rel(root, err.filename))
                else:
                    complete = False
                self._record_pathless(str(err.filename or root), f"読めないフォルダ: {describe_exception(err)}")
            except Exception as e:  # noqa: BLE001 (onerror の中の例外は走査全体を止めてしまう)
                complete = False
                self._unexpected(e, "読めないフォルダの記録に失敗")

        def visit(kind: str, path: str, action) -> None:
            nonlocal complete
            try:
                rel = _rel(root, path)
            except Exception as e:  # noqa: BLE001
                complete = False
                self._unexpected(e, f"{safe_text(path)} の相対パスを求められない")
                self._record_pathless(path, f"相対パスを求められない: {describe_exception(e)}")
                return
            found.add(rel)  # 例外で読めなくても、見つかったものとして扱う(missing にしない)
            try:
                action(rel)
            except Exception as e:  # noqa: BLE001 (ソース 1 つの想定外で、スキャン全体を止めない)
                self._source_failed(root_id, path, rel, kind, "想定外のエラー", e)

        excluded = self._excluded_dirs(root)
        try:
            for dirpath, dirnames, filenames in os.walk(root, onerror=onerror, followlinks=False):
                dirnames[:] = self._walkable_dirs(dirpath, dirnames, excluded)
                images = sorted(f for f in filenames if is_image(f))
                if images:
                    visit("folder", dirpath,
                          lambda rel, dirpath=dirpath, images=images:
                          self._scan_folder(root_id, root, rel, Path(dirpath), images))
                for name in sorted(filenames):
                    if is_archive_name(name):
                        full = os.path.join(dirpath, name)
                        visit("archive", full, lambda rel, full=full: self._scan_archive(root_id, rel, Path(full)))
        except Exception as e:  # noqa: BLE001 (走査そのものの想定外。途中までの結果は残す)
            complete = False
            self._unexpected(e, f"蔵書フォルダ {safe_text(root)} の走査を中断")
            self._record_pathless(str(root), f"走査を途中で中断した(以降は読んでいない): {describe_exception(e)}")

        def under_error_dir(rel: str) -> bool:
            return any(e == "" or rel == e or rel.startswith(e + "/") for e in error_dirs)

        with db.transaction(self.conn) as c:
            if complete:
                for sid, rel in c.execute("SELECT id, rel_path FROM sources WHERE root_id = ?", (root_id,)).fetchall():
                    if rel not in found and not under_error_dir(rel):
                        c.execute("UPDATE sources SET missing = 1, offline = 0 WHERE id = ?", (sid,))
            now = db.now()
            c.execute("UPDATE library_roots SET online = 1, last_checked_at = ?, last_online_at = ? WHERE id = ?",
                      (now, now, root_id))

    def _walkable_dirs(self, dirpath: str, dirnames: list[str], excluded: set[str]) -> list[str]:
        """辿るサブフォルダ。整理処理の置き場と、祖先へ戻るリンク(ジャンクションの循環)は辿らない。"""
        keep = []
        for d in sorted(dirnames):
            full = os.path.join(dirpath, d)
            try:
                if _norm(full) in excluded:
                    continue
                if _links_to_ancestor(dirpath, full):
                    self._record_pathless(full, "祖先のフォルダを指すリンク(ジャンクション)なので辿らない")
                    continue
            except Exception as e:  # noqa: BLE001 (判定できないフォルダは辿る。辿れなければ onerror に出る)
                self._unexpected(e, f"{safe_text(full)} を辿るか判定できない")
            keep.append(d)
        return keep

    # --- ソース -------------------------------------------------------------

    def _upsert_source(self, c, root_id: int, rel: str, kind: str) -> sqlite3.Row:
        c.execute("INSERT OR IGNORE INTO sources (root_id, rel_path, kind) VALUES (?, ?, ?)", (root_id, rel, kind))
        c.execute("UPDATE sources SET offline = 0, missing = 0, kind = ? WHERE root_id = ? AND rel_path = ?",
                  (kind, root_id, rel))
        return c.execute("SELECT * FROM sources WHERE root_id = ? AND rel_path = ?", (root_id, rel)).fetchone()

    def _scan_folder(self, root_id: int, root: Path, rel: str, path: Path, images: list[str]) -> None:
        try:
            st = path.stat()
        except OSError as e:
            self._source_failed(root_id, str(path), rel, "folder", f"読めないフォルダ: {describe_exception(e)}")
            return
        size = 0
        readable: list[str] = []
        unreadable: list[Skip] = []
        for f in images:
            try:
                size += (path / f).stat().st_size
                readable.append(f)
            except OSError as e:  # 壊れたリンクなど。そのファイルだけ数えない
                unreadable.append(Skip(safe_text(f), f"読めない画像: {describe_exception(e)}"))
        if not readable:
            self._source_failed(root_id, str(path), rel, "folder",
                                "読める画像が 1 枚も無い: " + "; ".join(s.reason for s in unreadable[:3]))
            return
        name = path.name if rel else (root.name or str(root))
        spec = VolumeSpec(None, "", name, _fs_name_bytes(name), "filesystem", len(readable))
        with db.transaction(self.conn) as c:
            src = self._upsert_source(c, root_id, rel, "folder")
            record_issues(c, src["id"], None, unreadable)
            self._replace_volumes(c, src["id"], [spec])
            c.execute("UPDATE sources SET size = ?, mtime_ns = ?, scan_version = ?, scanned_at = ? WHERE id = ?",
                      (size, st.st_mtime_ns, SCAN_VERSION, db.now(), src["id"]))

    def _scan_archive(self, root_id: int, rel: str, path: Path) -> None:
        try:
            st = path.stat()
        except OSError as e:
            self._source_failed(root_id, str(path), rel, "archive", f"読めないファイル: {describe_exception(e)}")
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
            if not specs:
                issues.append(Skip(None, "巻にできるもの(画像のあるフォルダ・内側のアーカイブ)が見つからない"))
        except UnsupportedArchive as e:
            issues.append(Skip(None, safe_text(e)))
        except LimitExceeded as e:
            issues.append(Skip(None, safe_text(f"展開上限によりスキップ: {e}")))
        except ArchiveError as e:
            issues.append(Skip(None, safe_text(f"読めないアーカイブ: {e}")))
        except Exception as e:  # noqa: BLE001 (想定外の形のアーカイブ。サイズ・mtime は更新せず次回また試す)
            specs = None
            self._unexpected(e, f"アーカイブ {safe_text(path)} を読めなかった")
            issues.append(Skip(None, f"読めないアーカイブ(想定外のエラー): {describe_exception(e)}"))
        with db.transaction(self.conn) as c:
            record_issues(c, src["id"], None, issues)
            if specs is not None:
                self._replace_volumes(c, src["id"], specs)
                c.execute("UPDATE sources SET size = ?, mtime_ns = ?, scan_version = ?, scanned_at = ? WHERE id = ?",
                          (st.st_size, st.st_mtime_ns, SCAN_VERSION, db.now(), src["id"]))
            # 読めなかったときはサイズ・mtime を更新しない(次のスキャンで再挑戦する。既存の巻はそのまま)

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
                             analyzed_version = NULL, analysis_error = NULL,
                             missing = 0, updated_at = ? WHERE id = ?""",
                          (s.title, s.name_raw, s.name_encoding, s.page_count, *numbers, now, existing[key]))
            else:
                c.execute("""INSERT INTO volumes (source_id, container_path, entry_prefix, title, name_raw,
                             name_encoding, page_count, number_kind, number_start, number_end, number_evidence,
                             created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                          (source_id, s.container_path, s.entry_prefix, s.title, s.name_raw, s.name_encoding,
                           s.page_count, *numbers, now, now))
        for key, vid in existing.items():
            if key not in seen:
                c.execute("UPDATE volumes SET missing = 1, updated_at = ? WHERE id = ?", (now, vid))


def _links_to_ancestor(dirpath: str, child: str) -> bool:
    """child がシンボリックリンク/ジャンクションで、dirpath(とその祖先)を指しているか。

    os.walk(followlinks=False)は Windows のジャンクションを辿ってしまうので、祖先を指すものだけ止める
    (辿ると同じ中身を、パスが長すぎて読めなくなるまで何重にも登録しにいく)。祖先を指さないリンクは今までどおり辿る。
    """
    isjunction = getattr(os.path, "isjunction", lambda p: False)
    if not (os.path.islink(child) or isjunction(child)):
        return False
    target = os.path.normcase(os.path.realpath(child))
    here = os.path.normcase(os.path.realpath(dirpath))
    return here == target or here.startswith(target.rstrip(os.sep) + os.sep)


def collect_issues(conn: sqlite3.Connection, limit: int = MAX_REPORTED_ISSUES) -> tuple[list[dict], int]:
    """画面に出す「読めなかったもの」(scan_issues の現在の内容)と総数。

    見えなくなったソース・オフラインのルート・設定から外したルートの記録は出さない(DB には残る)。
    ソースに結び付かない記録(source_id NULL)は entry_name が走査中のパス。
    """
    where = """FROM scan_issues i LEFT JOIN sources s ON s.id = i.source_id
               LEFT JOIN library_roots r ON r.id = s.root_id
               WHERE i.source_id IS NULL OR (s.missing = 0 AND s.offline = 0 AND r.enabled = 1)"""
    total = conn.execute("SELECT COUNT(*) " + where).fetchone()[0]
    rows = conn.execute("SELECT i.source_id, i.container_path, i.entry_name, i.reason, s.rel_path, r.path AS root_path "
                        + where + " ORDER BY i.id LIMIT ?", (limit,)).fetchall()
    issues = []
    for r in rows:
        if r["source_id"] is None:
            path, entry = r["entry_name"], None
        else:
            root_path = r["root_path"].replace("\\", "/").rstrip("/")
            path = f"{root_path}/{r['rel_path']}" if r["rel_path"] else root_path
            entry = r["entry_name"]
        issues.append({"path": path, "container_path": r["container_path"], "entry_name": entry,
                       "reason": r["reason"]})
    return issues, total


def record_issues(c, source_id: int, container_path: str | None, issues: list[Skip]) -> None:
    """そのソース(と内側アーカイブ)の記録を今回の結果で置き換える。scan_issues は再導出できる記録なので消してよい。"""
    c.execute("DELETE FROM scan_issues WHERE source_id = ? AND container_path IS ?", (source_id, container_path))
    now = db.now()
    c.executemany(
        "INSERT INTO scan_issues (source_id, container_path, entry_name, reason, recorded_at) VALUES (?, ?, ?, ?, ?)",
        [(source_id, container_path, s.entry_name, s.reason, now) for s in issues])
