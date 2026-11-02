"""管理の「統計」タブ(#85)の数値。どれも DB と展開キャッシュの今の状態から数えるだけで、何も書かない。

数え方:
- 巻は一覧に見えている巻(`LISTED_VOLUMES_WHERE`。入れ子を分解した親・整理済み・missing・受け入れフォルダを除く)。
  作品は見えている巻を持つ作品。蔵書の切り替え(root_id)を渡すと、どちらもその蔵書ルートの巻だけで数える
- ページ数は画像の巻だけ(小説の page_count は区切りの数なので足さない)。ページ数が未確定の巻は別に数える
- 重複候補は却下していない組のうち、両方の巻が一覧に見えているもの(重複候補の画面と同じ条件)
- 読めなかったものはスキャンの結果と同じ数え方(spinebox/scanner.py collect_issues())。蔵書の切り替えでは絞らない
"""
from __future__ import annotations

import sqlite3

from . import works
from .cache import ExtractionCache, MemoryCache
from .library import VolumeNotFound, VolumeOffline, is_offline
from .scanner import LISTED_VOLUMES_JOIN, LISTED_VOLUMES_WHERE, collect_issues
from .thumbs import Thumbnails


def _cache_json(used: int, limit: int) -> dict:
    return {"used_bytes": used, "limit_bytes": limit}


def collect(conn: sqlite3.Connection, *, root_id: int | None, thumbs: Thumbnails, disk: ExtractionCache,
            memory: MemoryCache, thumb_store: ExtractionCache, inbox_prefixes: tuple[str, ...]) -> dict:
    where = "" if root_id is None else " AND r.id = ?"
    params: tuple = () if root_id is None else (root_id,)
    rows = conn.execute(
        "SELECT v.id, v.format, v.page_count, s.offline AS source_offline, r.online AS root_online, "
        "p.page, p.finished " + LISTED_VOLUMES_JOIN + " LEFT JOIN reading_progress p ON p.volume_id = v.id "
        + LISTED_VOLUMES_WHERE + where, params).fetchall()
    images = [r for r in rows if r["format"] is None]
    volumes = {
        "total": len(rows),
        "images": len(images),
        "novels": len(rows) - len(images),
        "offline": sum(1 for r in rows if is_offline(r)),
        "finished": sum(1 for r in rows if r["page"] is not None and r["finished"]),
        "reading": sum(1 for r in rows if r["page"] is not None and not r["finished"]),
        "unread": sum(1 for r in rows if r["page"] is None),
    }
    pages = {"total": sum(r["page_count"] or 0 for r in images),
             "unknown_volumes": sum(1 for r in images if r["page_count"] is None)}

    listing = works.list_works(conn, root_id=root_id)
    ws = listing["works"]
    no_cover = 0
    for w in ws:
        vid = w["cover_volume_id"]
        if vid is None:
            continue  # 画像の巻が無い(小説だけ・オフラインだけ)作品は、表紙を作れないので数えない
        try:
            if not thumbs.has(conn, vid):
                no_cover += 1
        except (VolumeNotFound, VolumeOffline):
            no_cover += 1
    work_stats = {
        "total": len(ws),
        "manga": sum(1 for w in ws if w["kind"] == "manga"),
        "novel": sum(1 for w in ws if w["kind"] == "novel"),
        "kind_unknown": sum(1 for w in ws if w["kind"] not in ("manga", "novel")),
        "finished": sum(1 for w in ws if w["reading_state"] == "finished"),
        "reading": sum(1 for w in ws if w["reading_state"] == "reading"),
        "unread": sum(1 for w in ws if w["reading_state"] == "unread"),
        "link_pending": sum(1 for w in ws if w["link_pending"] > 0),
        "with_missing": sum(1 for w in ws if w["missing_volumes"] or w["missing_episodes"]),
        "no_cover": no_cover,
    }

    listed = {r["id"] for r in rows}
    duplicates = sum(1 for r in conn.execute(
        "SELECT volume_a, volume_b FROM duplicate_candidates WHERE dismissed_at IS NULL")
        if r["volume_a"] in listed and r["volume_b"] in listed)
    _, issues = collect_issues(conn, limit=0, inbox_prefixes=inbox_prefixes)

    return {
        "root_id": root_id,
        "works": work_stats,
        "volumes": volumes,
        "pages": pages,
        "unassigned_volumes": listing["unassigned"],
        "duplicate_candidates": duplicates,
        "scan_issues": issues,
        "cache": {
            "disk": _cache_json(disk.total_bytes, disk.limit_bytes),
            "memory": _cache_json(memory.total_bytes, memory.limit_bytes),
            "thumbnails": _cache_json(thumb_store.total_bytes, thumb_store.limit_bytes),
        },
    }
