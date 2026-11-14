"""蔵書(グループ)と蔵書フォルダの管理(#313)。

サイドバーで切り替える「蔵書」は `library_groups` の 1 行で、蔵書フォルダ(`library_roots` の inbox = 0 の行)を
複数まとめる。`sources`・整理・受け入れ・表紙のキーはフォルダ単位のまま(グループは一覧の絞り込みと表示名だけ)。

**DB が正。** 設定ファイルの `library_roots` は「まだ DB に無いパスを足す」だけの取り込み元で、設定から消しても
フォルダは外れない(外すのは画面から)。画面で外したフォルダは設定に残っていても戻さない。

フォルダを外しても `library_roots` の行は消さず `enabled = 0` にするだけ(`sources`・巻・読書位置がぶら下がっている)。
同じパスを足し直すと同じ行を有効に戻すので、巻と読書位置がそのまま戻る。

フォルダの一覧(`list_folders`)はサーバー機のフォルダ名を読むだけで、ファイルは返さず、何も書かない。
"""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

from . import db
from .config import Settings
from .linking import SEARCH_ORDER, parse_group_sources
from .organize.fs import is_within
from .pages import natural_key
from .scanner import LISTED_VOLUMES_JOIN, LISTED_VOLUMES_WHERE, root_is_accessible, safe_text

DEFAULT_GROUP_NAME = "デフォルト"
MAX_NAME_LENGTH = 100


class LibraryError(ValueError):
    """入力が受け付けられない(422)。"""


class LibraryNotFound(LookupError):
    """蔵書・蔵書フォルダが無い(404)。"""


class LibraryConflict(RuntimeError):
    """今の状態では行えない(409)。"""


def _path_key(path: Path | str) -> str:
    return os.path.normcase(os.path.normpath(os.path.abspath(path)))


def root_name(path: str) -> str:
    """蔵書フォルダの表示名: 最後のフォルダ名(ドライブ直下なら文字列のまま)。サーバー機の Windows のパスも区切る。"""
    name = path.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
    return name or path


def enabled_roots(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """有効な蔵書フォルダ(受け入れフォルダを除く)。"""
    return conn.execute("SELECT * FROM library_roots WHERE enabled = 1 AND inbox = 0 ORDER BY id").fetchall()


# --- 設定からの取り込みと、グループの割り当て ---------------------------------------------------

def _unique_name(c: sqlite3.Connection, base: str) -> str:
    names = {r[0] for r in c.execute("SELECT name FROM library_groups")}
    name, n = base, 2
    while name in names:
        name, n = f"{base} ({n})", n + 1
    return name


def _insert_group(c: sqlite3.Connection, name: str) -> int:
    return c.execute("INSERT INTO library_groups (name, created_at) VALUES (?, ?)", (name, db.now())).lastrowid


def _import_config_roots(c: sqlite3.Connection, settings: Settings) -> None:
    inbox_keys = {_path_key(i.path) for i in settings.inboxes}
    # 突き合わせは大文字小文字・区切りを正規化したキーで行う(SQLite の UNIQUE は完全一致のため、
    # 画面で足した実ディスクの綴りと設定の綴りが違うと同じフォルダが二重に登録される)
    by_key = {_path_key(r["path"]): r for r in c.execute("SELECT id, path, inbox FROM library_roots")}
    for p in settings.library_roots:
        path = str(Path(p))
        key = _path_key(path)
        existing = by_key.get(key)
        if existing is None:
            cur = c.execute("INSERT INTO library_roots (path) VALUES (?)", (path,))
            by_key[key] = {"id": cur.lastrowid, "path": path, "inbox": 0}
        # 受け入れフォルダだったパスを設定で蔵書へ付け替えた場合だけ、蔵書として有効にする
        # (受け入れフォルダは設定が正で、設定から外すと enabled = 0 のまま残る)
        elif existing["inbox"] and key not in inbox_keys:
            c.execute("UPDATE library_roots SET inbox = 0, enabled = 1 WHERE id = ?", (existing["id"],))


def _ensure_root_groups(c: sqlite3.Connection) -> None:
    """グループの無い有効な蔵書フォルダを、フォルダ名のグループへ 1 つずつ入れる。

    名前が重なったら連番を付ける(別のフォルダを黙って同じ蔵書へ合流させない。まとめるのは利用者が画面で行う)。
    """
    rows = c.execute("SELECT id, path FROM library_roots WHERE enabled = 1 AND inbox = 0 AND group_id IS NULL "
                     "ORDER BY id").fetchall()
    for r in rows:
        gid = _insert_group(c, _unique_name(c, safe_text(root_name(r["path"]))))
        c.execute("UPDATE library_roots SET group_id = ? WHERE id = ?", (gid, r["id"]))
    # 受け入れフォルダと、外したフォルダはグループに入れない
    c.execute("UPDATE library_roots SET group_id = NULL WHERE group_id IS NOT NULL AND (inbox = 1 OR enabled = 0)")


def sync(conn: sqlite3.Connection, settings: Settings) -> list[tuple[int, Path]]:
    """設定の蔵書フォルダのうち DB に無いものを足し、グループを割り当てて、有効な蔵書フォルダを返す。

    起動時とスキャンの前に呼ぶ。何度呼んでも同じ(変化が無ければ書かない)。
    """
    with db.transaction(conn) as c:
        _import_config_roots(c, settings)
        _ensure_root_groups(c)
    return [(r["id"], Path(r["path"])) for r in enabled_roots(conn)]


# --- 一覧 -------------------------------------------------------------------------------

def _folder_json(row: sqlite3.Row, counts: dict[int, int]) -> dict:
    return {"id": row["id"], "path": row["path"], "online": bool(row["online"]),
            "volume_count": counts.get(row["id"], 0)}


def list_groups(conn: sqlite3.Connection) -> list[dict]:
    """蔵書と、その蔵書フォルダ・一覧に見えている巻の数。フォルダの無い蔵書も出す。

    online はフォルダが 1 つでも見えていれば真(フォルダが無ければ真。オフラインと書かない)。
    """
    counts = {r[0]: r[1] for r in conn.execute(
        "SELECT r.id, COUNT(*) " + LISTED_VOLUMES_JOIN + LISTED_VOLUMES_WHERE + " GROUP BY r.id")}
    folders: dict[int, list[dict]] = {}
    for r in enabled_roots(conn):
        if r["group_id"] is not None:
            folders.setdefault(r["group_id"], []).append(_folder_json(r, counts))
    out = []
    for g in conn.execute("SELECT id, name, metadata_sources FROM library_groups ORDER BY id"):
        items = folders.get(g["id"], [])
        chosen = parse_group_sources(g["metadata_sources"])
        out.append({"id": g["id"], "name": g["name"],
                    # 公開 DB の取得元(#316)。None は設定の既定に従う、[] はこの蔵書では取得しない
                    "metadata_sources": None if chosen is None else list(chosen),
                    "online": not items or any(f["online"] for f in items),
                    "volume_count": sum(f["volume_count"] for f in items), "folders": items})
    return out


def get_group(conn: sqlite3.Connection, group_id: int) -> dict:
    found = next((g for g in list_groups(conn) if g["id"] == group_id), None)
    if found is None:
        raise LibraryNotFound("蔵書が見つかりません")
    return found


# --- 蔵書(グループ) -----------------------------------------------------------------------

def _clean_name(name: str) -> str:
    name = name.strip()
    if not name:
        raise LibraryError("蔵書の名前を入力してください")
    if len(name) > MAX_NAME_LENGTH:
        raise LibraryError(f"蔵書の名前は {MAX_NAME_LENGTH} 文字までにしてください")
    return name


def _require_group(c: sqlite3.Connection, group_id: int) -> None:
    if c.execute("SELECT 1 FROM library_groups WHERE id = ?", (group_id,)).fetchone() is None:
        raise LibraryNotFound("蔵書が見つかりません")


def create_group(conn: sqlite3.Connection, name: str) -> dict:
    name = _clean_name(name)
    with db.transaction(conn) as c:
        if c.execute("SELECT 1 FROM library_groups WHERE name = ?", (name,)).fetchone():
            raise LibraryConflict(f"同じ名前の蔵書があります: {name}")
        gid = _insert_group(c, name)
    return get_group(conn, gid)


def rename_group(conn: sqlite3.Connection, group_id: int, name: str) -> dict:
    name = _clean_name(name)
    with db.transaction(conn) as c:
        _require_group(c, group_id)
        if c.execute("SELECT 1 FROM library_groups WHERE name = ? AND id <> ?", (name, group_id)).fetchone():
            raise LibraryConflict(f"同じ名前の蔵書があります: {name}")
        c.execute("UPDATE library_groups SET name = ? WHERE id = ? AND name <> ?", (name, group_id, name))
    return get_group(conn, group_id)


def set_group_sources(conn: sqlite3.Connection, group_id: int, sources: list[str] | None) -> dict:
    """蔵書の公開 DB の取得元を決める(#316)。None は設定の既定に戻す、空の並びはこの蔵書では取得しない。

    引く順は選べない(`linking.SEARCH_ORDER` の順で保存する)。取得済みの連携・反映済みの値には触らない
    (外した取得元は、以後取り直さなくなるだけ)。
    """
    if sources is not None:
        unknown = sorted({s for s in sources if s not in SEARCH_ORDER})
        if unknown:
            raise LibraryError("知らない取得元です: " + ", ".join(safe_text(s) for s in unknown))
    value = None if sources is None else json.dumps([s for s in SEARCH_ORDER if s in sources])
    with db.transaction(conn) as c:
        _require_group(c, group_id)
        c.execute("UPDATE library_groups SET metadata_sources = ? WHERE id = ? AND metadata_sources IS NOT ?",
                  (value, group_id, value))
    return get_group(conn, group_id)


def delete_group(conn: sqlite3.Connection, group_id: int) -> None:
    """フォルダの無い蔵書を消す。フォルダが残っていれば消さない(先に外すか、ほかの蔵書へ移す)。"""
    with db.transaction(conn) as c:
        _require_group(c, group_id)
        if c.execute("SELECT 1 FROM library_roots WHERE group_id = ? AND enabled = 1 AND inbox = 0",
                     (group_id,)).fetchone():
            raise LibraryConflict("蔵書フォルダが残っています。先にフォルダを外すか、ほかの蔵書へ移してください")
        c.execute("UPDATE library_roots SET group_id = NULL WHERE group_id = ?", (group_id,))
        c.execute("DELETE FROM library_groups WHERE id = ?", (group_id,))


# --- 蔵書フォルダ --------------------------------------------------------------------------

def _disk_spelling(path: str) -> str:
    """利用者が打ったパスを、区切りごとにディスク上の綴り(大文字小文字)へ直す。

    Windows は綴りが違っても同じフォルダを指すので、打ったままだと画面と DB に違う綴りが残る。ぴったり同じ名前が
    あればそれを使う(大文字小文字を区別するファイルシステムで別のフォルダを取り違えない)。リンクは辿らない。
    """
    drive, tail = os.path.splitdrive(path)
    cur = (drive.upper() if len(drive) == 2 and drive.endswith(":") else drive) + (os.sep if tail[:1] in "\\/" else "")
    for part in [p for p in tail.replace("\\", "/").split("/") if p]:
        name = part
        try:
            with os.scandir(cur) as it:
                names = [e.name for e in it]
        except OSError:
            names = []
        if part not in names:
            name = next((n for n in sorted(names) if n.casefold() == part.casefold()), part)
        cur = os.path.join(cur, name)
    return cur


def _clean_path(path: str) -> str:
    text = path.strip()
    if not text:
        raise LibraryError("フォルダのパスを入力してください")
    if "\x00" in text:
        raise LibraryError("フォルダのパスに使えない文字が入っています")
    if not os.path.isabs(text):
        raise LibraryError("フォルダはサーバー機の絶対パスで指定してください(例: D:\\本)")
    try:
        full = _disk_spelling(os.path.normpath(text))
        accessible = root_is_accessible(Path(full))
    except (OSError, ValueError) as e:
        raise LibraryError(f"フォルダのパスを読めません: {safe_text(e)}") from None
    if not accessible:
        raise LibraryError("フォルダが見つからないか、読めません(サーバー機から見えるパスか確かめてください)")
    return full


def _overlap_problem(c: sqlite3.Connection, settings: Settings, path: str, own_id: int | None) -> str | None:
    """path を蔵書フォルダにできない理由(入れ子)。無ければ None。

    蔵書フォルダどうしが入れ子だと同じファイルを 2 回数える。受け入れフォルダと重なると受け入れ待ちの本を蔵書に数える。
    整理の作業場・退避フォルダ、データの置き場(DB・展開キャッシュ)を含むフォルダは、それらを本として読んでしまう。
    """
    target = Path(path)

    def overlaps(other: Path) -> bool:
        return is_within(target, other) or is_within(other, target)

    for r in c.execute("SELECT id, path, inbox FROM library_roots WHERE enabled = 1"):
        if r["id"] != own_id and overlaps(Path(r["path"])):
            kind = "受け入れフォルダ" if r["inbox"] else "登録済みの蔵書フォルダ"
            return f"{kind}と重なっています: {r['path']}"
    for i in settings.inboxes:
        if overlaps(Path(i.path)):
            return f"受け入れフォルダと重なっています: {i.path}"
    organize = Path(settings.organize_dir).expanduser()
    if organize.is_absolute() and overlaps(organize):
        return f"整理の作業場・退避フォルダと重なっています: {organize}"
    if overlaps(Path(settings.data_dir)):
        return f"SpineBox のデータの置き場と重なっています: {settings.data_dir}"
    return None


def add_folder(conn: sqlite3.Connection, settings: Settings, path: str, group_id: int | None = None) -> dict:
    """蔵書フォルダを蔵書へ足し、足した先の蔵書を返す。

    group_id を省くのは蔵書が 1 つも無いときだけで、「デフォルト」の蔵書を作ってそこへ入れる。
    以前に外した同じパスなら、その行を有効に戻す(巻・読書位置がそのまま戻る)。巻が現れるのは次のスキャン。
    """
    full = _clean_path(path)
    key = _path_key(full)
    with db.transaction(conn) as c:
        existing = next((r for r in c.execute("SELECT * FROM library_roots") if _path_key(r["path"]) == key), None)
        if existing is not None and existing["enabled"] and not existing["inbox"]:
            raise LibraryConflict(f"このフォルダは登録済みです: {existing['path']}")
        problem = _overlap_problem(c, settings, full, None)
        if problem:
            raise LibraryError(problem)
        if group_id is None:
            if c.execute("SELECT 1 FROM library_groups").fetchone():
                raise LibraryError("フォルダを入れる蔵書を選んでください")
            group_id = _insert_group(c, DEFAULT_GROUP_NAME)
        else:
            _require_group(c, group_id)
        stamp = db.now()
        if existing is None:
            c.execute("INSERT INTO library_roots (path, online, enabled, inbox, group_id, last_checked_at, "
                      "last_online_at) VALUES (?, 1, 1, 0, ?, ?, ?)", (full, group_id, stamp, stamp))
        else:
            c.execute("UPDATE library_roots SET enabled = 1, inbox = 0, online = 1, group_id = ?, "
                      "last_checked_at = ?, last_online_at = ? WHERE id = ?", (group_id, stamp, stamp, existing["id"]))
    return get_group(conn, group_id)


def _require_folder(c: sqlite3.Connection, folder_id: int) -> sqlite3.Row:
    row = c.execute("SELECT * FROM library_roots WHERE id = ? AND enabled = 1 AND inbox = 0", (folder_id,)).fetchone()
    if row is None:
        raise LibraryNotFound("蔵書フォルダが見つかりません")
    return row


def move_folder(conn: sqlite3.Connection, folder_id: int, group_id: int) -> dict:
    """蔵書フォルダを別の蔵書へ移し、移した先の蔵書を返す。"""
    with db.transaction(conn) as c:
        _require_folder(c, folder_id)
        _require_group(c, group_id)
        c.execute("UPDATE library_roots SET group_id = ? WHERE id = ? AND group_id IS NOT ?",
                  (group_id, folder_id, group_id))
    return get_group(conn, group_id)


def remove_folder(conn: sqlite3.Connection, folder_id: int) -> None:
    """蔵書フォルダを蔵書から外す。行・巻・読書位置は消さない(一覧に出なくなるだけ)。"""
    with db.transaction(conn) as c:
        _require_folder(c, folder_id)
        c.execute("UPDATE library_roots SET enabled = 0, group_id = NULL WHERE id = ?", (folder_id,))


# --- サーバー機のフォルダの一覧(蔵書フォルダを選ぶ画面) -----------------------------------------------

def _drives() -> list[str]:
    if os.name == "nt":
        try:
            return sorted(os.listdrives())
        except OSError:
            return []
    return ["/"]


def list_folders(path: str = "") -> dict:
    """path の直下のフォルダ名(読むだけ。ファイルは返さない)。path が空ならドライブ(Windows 以外は `/`)。

    parent は 1 つ上(ドライブの一覧なら ""、一覧そのものでは None)。読めないフォルダ・名前は飛ばす。
    """
    text = path.strip()
    if not text:
        return {"path": "", "parent": None, "folders": [{"name": d, "path": d} for d in _drives()]}
    if "\x00" in text or not os.path.isabs(text):
        raise LibraryError("フォルダはサーバー機の絶対パスで指定してください")
    full = os.path.normpath(text)
    names: list[str] = []
    try:
        with os.scandir(full) as it:
            for entry in it:
                try:
                    if entry.is_dir():
                        names.append(entry.name)
                except OSError:
                    continue
    except (OSError, ValueError):
        raise LibraryNotFound("フォルダが見つからないか、読めません") from None
    names.sort(key=natural_key)
    parent = os.path.dirname(full)
    return {"path": safe_text(full), "parent": "" if parent == full else safe_text(parent),
            "folders": [{"name": safe_text(n), "path": safe_text(os.path.join(full, n))} for n in names]}
