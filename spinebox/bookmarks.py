"""ブックマーク(#86)。作品か巻に付ける印と、その一覧。

印は利用者が付けた記録なので、蔵書フォルダがオフラインでも・巻が見えなくなっても行を消さない
(一覧に出すかどうかを参照側で決める)。巻が整理で置き換わるときは `transfer_volume` で新しい巻へ移し、
作品の統合では `spinebox/works.py _merge_bookmarks()` が吸収先へ移す。
"""
from __future__ import annotations

import sqlite3

from . import db, works
from .library import VolumeNotFound, is_offline
from .pages import natural_key

SORTS = ("added", "title")

# 一覧に出す巻。LISTED_VOLUMES_WHERE と違い「中の巻に分解済みの親」も出す(分解の前に付けた印が見えなくならない。
# 親はそのまま開ける)。オフラインの巻は出す(offline の印つき)。整理で置き換わった巻・missing は出さない
_VOLUME_SQL = """
SELECT b.created_at, b.id AS bookmark_id, v.id, v.title, v.page_count, v.format, v.number_kind, v.number_start,
       v.number_end, v.number_evidence, pv.title AS parent_title, s.offline AS source_offline, r.online AS root_online,
       p.page, p.finished, p.position, wv.work_id
FROM bookmarks b JOIN volumes v ON v.id = b.volume_id JOIN sources s ON s.id = v.source_id
JOIN library_roots r ON r.id = s.root_id LEFT JOIN volumes pv ON pv.id = v.parent_id
LEFT JOIN reading_progress p ON p.volume_id = v.id LEFT JOIN work_volumes wv ON wv.volume_id = v.id
WHERE r.enabled = 1 AND r.inbox = 0 AND v.missing = 0 AND s.missing = 0
  AND (v.parent_id IS NULL OR pv.missing = 0) AND v.organized_op_id IS NULL
"""


class InvalidSort(ValueError):
    pass


def _alive_work(c: sqlite3.Connection, work_id: int) -> int:
    root = works.root_of(c, work_id)
    if root is None:
        raise works.WorkNotFound(work_id)
    return root


def _check_volume(c: sqlite3.Connection, volume_id: int) -> None:
    if c.execute("SELECT 1 FROM volumes WHERE id = ?", (volume_id,)).fetchone() is None:
        raise VolumeNotFound(volume_id)


def _state(row) -> dict:
    return {"bookmarked": row is not None, "created_at": row["created_at"] if row else None}


def set_work(conn: sqlite3.Connection, work_id: int, on: bool) -> dict:
    """作品の印を付ける(on)・外す。統合された作品の id なら吸収先に付ける。付け直しても日時は変えない。"""
    with db.transaction(conn) as c:
        wid = _alive_work(c, work_id)
        if on:
            c.execute("INSERT INTO bookmarks (work_id, created_at) SELECT ?, ? "
                      "WHERE NOT EXISTS (SELECT 1 FROM bookmarks WHERE work_id = ?)", (wid, db.now(), wid))
        else:
            c.execute("DELETE FROM bookmarks WHERE work_id = ?", (wid,))
        row = c.execute("SELECT created_at FROM bookmarks WHERE work_id = ?", (wid,)).fetchone()
        return {"work_id": wid, **_state(row)}


def set_volume(conn: sqlite3.Connection, volume_id: int, on: bool) -> dict:
    """巻の印を付ける(on)・外す。オフラインの巻にも付け外しできる(ファイルは開かない)。"""
    with db.transaction(conn) as c:
        _check_volume(c, volume_id)
        if on:
            c.execute("INSERT INTO bookmarks (volume_id, created_at) SELECT ?, ? "
                      "WHERE NOT EXISTS (SELECT 1 FROM bookmarks WHERE volume_id = ?)", (volume_id, db.now(), volume_id))
        else:
            c.execute("DELETE FROM bookmarks WHERE volume_id = ?", (volume_id,))
        row = c.execute("SELECT created_at FROM bookmarks WHERE volume_id = ?", (volume_id,)).fetchone()
        return {"volume_id": volume_id, **_state(row)}


def volume_ids(conn: sqlite3.Connection) -> set[int]:
    return {r[0] for r in conn.execute("SELECT volume_id FROM bookmarks WHERE volume_id IS NOT NULL")}


def is_volume_bookmarked(conn: sqlite3.Connection, volume_id: int) -> bool:
    return conn.execute("SELECT 1 FROM bookmarks WHERE volume_id = ?", (volume_id,)).fetchone() is not None


def transfer_volume(c: sqlite3.Connection, from_id: int, to_id: int) -> None:
    """巻の印を別の巻へ移す(整理で巻が置き換わるとき・元に戻すとき)。付けた日時はそのまま。

    写すのではなく移す: 整理の後で新しい巻から外した印が、元に戻したときに元の巻へ復活しないようにするため。
    """
    row = c.execute("SELECT id FROM bookmarks WHERE volume_id = ?", (from_id,)).fetchone()
    if row is None:
        return
    c.execute("DELETE FROM bookmarks WHERE volume_id = ?", (to_id,))
    c.execute("UPDATE bookmarks SET volume_id = ? WHERE id = ?", (to_id, row["id"]))


def list_bookmarks(conn: sqlite3.Connection, sort: str = "added") -> dict:
    """印を付けた作品・巻の一覧。added = 付けた新しい順、title = 作品名の順(巻は作品名 → 巻の名前)。

    作品は生きている作品(統合されていれば吸収先)ごとに 1 件。巻は見えている巻だけ(オフラインは offline つきで出す)。
    """
    if sort not in SORTS:
        raise InvalidSort(f"未対応の並び順: {sort}")
    rows = works._work_rows(conn)
    roots = works._roots(conn)
    items: list[dict] = []
    work_rows = conn.execute("SELECT id, work_id, created_at FROM bookmarks WHERE work_id IS NOT NULL").fetchall()
    if work_rows:
        vols = works._listed_volumes(conn)
        for b in work_rows:
            wid = roots.get(b["work_id"], b["work_id"])
            w = rows.get(wid)
            if w is None:
                continue
            items.append({"kind": "work", "created_at": b["created_at"], "_id": b["id"], "_sort": w["title"],
                          "work": works._summary(w, vols.get(wid, []), 0)})
    for r in conn.execute(_VOLUME_SQL):
        wid = roots.get(r["work_id"], r["work_id"]) if r["work_id"] is not None else None
        work_title = rows[wid]["title"] if wid in rows else None
        number = None
        if r["number_kind"] is not None:
            number = {"kind": r["number_kind"], "start": r["number_start"], "end": r["number_end"],
                      "evidence": r["number_evidence"]}
        progress = None
        if r["page"] is not None:
            progress = {"page": r["page"], "finished": bool(r["finished"])}
            if r["position"] is not None:
                progress["position"] = r["position"]
        items.append({"kind": "volume", "created_at": r["created_at"], "_id": r["bookmark_id"],
                      "_sort": work_title or r["title"],
                      "volume": {"id": r["id"], "title": r["title"], "parent_title": r["parent_title"],
                                 "format": "images" if r["format"] in (None, "pdf") else r["format"], "page_count": r["page_count"],
                                 "offline": is_offline(r), "progress": progress, "volume_number": number,
                                 "work_id": wid if work_title is not None else None, "work_title": work_title}})
    if sort == "added":
        items.sort(key=lambda i: (i["created_at"], i["_id"]), reverse=True)
    else:
        # 同じ作品の中では作品の印を先に、巻は番号 → 名前の順
        def key(i):
            v = i.get("volume")
            num = (v["volume_number"] or {}).get("start") if v else None
            return (natural_key(i["_sort"]), v is not None, num is None, num or 0,
                    natural_key(v["title"]) if v else natural_key(""), i["_id"])
        items.sort(key=key)
    for i in items:
        del i["_id"], i["_sort"]
    return {"sort": sort, "items": items}
