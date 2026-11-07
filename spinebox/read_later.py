"""後で読む(#173)。作品に対する印と一覧の絞り込み。ブックマーク(#86)とは別物。

利用者が付けた記録なので、蔵書フォルダがオフラインでも作品が見えなくなっても行を消さない
(一覧に出すかどうかを参照側で決める)。作品の統合では吸収先へ移す。
"""
from __future__ import annotations

import sqlite3

from . import db, works


def _alive_work(c: sqlite3.Connection, work_id: int) -> int:
    root = works.root_of(c, work_id)
    if root is None:
        raise works.WorkNotFound(work_id)
    return root


def _state(row) -> dict:
    return {"read_later": row is not None, "created_at": row["created_at"] if row else None}


def set_work(conn: sqlite3.Connection, work_id: int, on: bool) -> dict:
    """作品の印を付ける(on)・外す。統合された作品の id なら吸収先に付ける。付け直しても日時は変えない。"""
    with db.transaction(conn) as c:
        wid = _alive_work(c, work_id)
        if on:
            c.execute("INSERT INTO read_later (work_id, created_at) SELECT ?, ? "
                      "WHERE NOT EXISTS (SELECT 1 FROM read_later WHERE work_id = ?)", (wid, db.now(), wid))
        else:
            c.execute("DELETE FROM read_later WHERE work_id = ?", (wid,))
        row = c.execute("SELECT created_at FROM read_later WHERE work_id = ?", (wid,)).fetchone()
        return {"work_id": wid, **_state(row)}


def ids(conn: sqlite3.Connection) -> set[int]:
    return {r[0] for r in conn.execute("SELECT work_id FROM read_later")}


def work_ids(conn: sqlite3.Connection) -> set[int]:
    """`ids` の別名(設計時の名前)。"""
    return ids(conn)


def is_marked(conn: sqlite3.Connection, work_id: int) -> bool:
    return conn.execute("SELECT 1 FROM read_later WHERE work_id = ?", (work_id,)).fetchone() is not None


def is_read_later(conn: sqlite3.Connection, work_id: int) -> bool:
    """`is_marked` の別名(設計時の名前)。"""
    return is_marked(conn, work_id)


def merge(c: sqlite3.Connection, keep: int, drop: int) -> None:
    """吸収される作品(#173)の印を残る作品へ移す。両方にあれば早い方の日時で 1 つにする。"""
    dropped = c.execute("SELECT work_id, created_at FROM read_later WHERE work_id = ?", (drop,)).fetchone()
    if dropped is None:
        return
    kept = c.execute("SELECT work_id, created_at FROM read_later WHERE work_id = ?", (keep,)).fetchone()
    if kept is None:
        c.execute("UPDATE read_later SET work_id = ? WHERE work_id = ?", (keep, drop))
        return
    c.execute("DELETE FROM read_later WHERE work_id = ?", (drop,))
    if dropped["created_at"] < kept["created_at"]:
        c.execute("UPDATE read_later SET created_at = ? WHERE work_id = ?", (dropped["created_at"], keep))
