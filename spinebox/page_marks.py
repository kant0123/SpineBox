"""しおり(#136)。漫画リーダー(manga-viewer)で巻のページに挟む印。

ブックマーク(#86。作品・巻に付ける印)とは別物で、1 巻の中のページ位置を指す。端末間で共有するためサーバーに置く。
ページ番号は読書位置と同じく 0 始まりで持ち、API の入出力(ビューワーの `bookmarkApi` の形)だけ 1 始まりにする。

題はビューワーが常に既定の「ページN」を送ってくる(利用者が書き換える手段が無い)ので保存せず、返すときに作る。
巻への分解・整理でページ番号が変わっても題が古い番号のまま残らない。

利用者の記録なので、蔵書フォルダがオフラインでも・巻が見えなくなっても行を消さない。巻が整理で置き換わるときは
`transfer_volume` で新しい巻へ移し(元に戻すと戻る)、巻への分解では `spinebox/analysis.py _carry_page_marks()` が子の巻へ写す。
"""
from __future__ import annotations

import sqlite3

from . import db
from .library import VolumeNotFound

MAX_MARKS = 20  # ビューワーの上限と同じ。API を直接叩いても増やしすぎない


class MarkOutOfRange(ValueError):
    pass


class TooManyMarks(ValueError):
    pass


def _title(page: int) -> str:
    return f"ページ{page + 1}"


def _page_count(c: sqlite3.Connection, volume_id: int) -> int | None:
    row = c.execute("SELECT page_count FROM volumes WHERE id = ?", (volume_id,)).fetchone()
    if row is None:
        raise VolumeNotFound(volume_id)
    return row["page_count"]


def list_marks(conn: sqlite3.Connection, volume_id: int) -> list[dict]:
    """巻のしおりをページ順に(ビューワーの形 `{page_number(1 始まり), title}`)。"""
    _page_count(conn, volume_id)
    rows = conn.execute("SELECT page FROM page_marks WHERE volume_id = ? ORDER BY page", (volume_id,))
    return [{"page_number": r["page"] + 1, "title": _title(r["page"])} for r in rows]


def add(conn: sqlite3.Connection, volume_id: int, page_number: int) -> None:
    """しおりを挟む。既にあれば何もしない。ページ数が分かっている巻は範囲外を断る。"""
    page = page_number - 1
    with db.transaction(conn) as c:
        count = _page_count(c, volume_id)
        if page < 0 or (count is not None and page >= count):
            raise MarkOutOfRange(page_number)
        if c.execute("SELECT 1 FROM page_marks WHERE volume_id = ? AND page = ?", (volume_id, page)).fetchone():
            return
        n = c.execute("SELECT COUNT(*) FROM page_marks WHERE volume_id = ?", (volume_id,)).fetchone()[0]
        if n >= MAX_MARKS:
            raise TooManyMarks(MAX_MARKS)
        c.execute("INSERT INTO page_marks (volume_id, page, created_at) VALUES (?, ?, ?)", (volume_id, page, db.now()))


def remove(conn: sqlite3.Connection, volume_id: int, page_number: int) -> None:
    with db.transaction(conn) as c:
        _page_count(c, volume_id)
        c.execute("DELETE FROM page_marks WHERE volume_id = ? AND page = ?", (volume_id, page_number - 1))


def transfer_volume(c: sqlite3.Connection, from_id: int, to_id: int, page_count: int | None) -> None:
    """整理で巻が置き換わるとき、しおりを新しい巻へ移す(写さない。元に戻すと逆向きに呼ばれて戻る)。

    ページの並びは CBZ 化の前後で同じなのでページ番号はそのまま使う。ページ数が減っていれば最後のページへ寄せる。
    """
    rows = c.execute("SELECT page, created_at FROM page_marks WHERE volume_id = ?", (from_id,)).fetchall()
    for r in rows:
        page = r["page"] if page_count is None else min(r["page"], max(page_count - 1, 0))
        c.execute("INSERT OR IGNORE INTO page_marks (volume_id, page, created_at) VALUES (?, ?, ?)",
                  (to_id, page, r["created_at"]))
    c.execute("DELETE FROM page_marks WHERE volume_id = ?", (from_id,))
