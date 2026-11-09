"""フォロー(#87)。フォローした作品の続刊(持っていない新しい巻)と欠けた巻を、公開 DB で調べて知らせる。

知らせるだけで、本の入手(ダウンロード・購入サービスとの連携)はしない(GOAL.md「作らないもの」)。

- 調べる(`check_work`): 作品と国立国会図書館サーチの連携(#50 の `work_links`)を、続刊用の短い有効期限で
  取り直し(`linking.fetch_work`。保存済みの応答が新しければネットワークに出ない)、連携した組の巻ごとの書誌のうち
  蔵書に無い巻番号を `follow_releases` に記録する。取得に失敗しても例外は出さず、前回までの連携の中身で数え、
  失敗を `follows.failure` に残す(フォロー一覧・閲覧は止めない)
- 見る(`list_follows` / `list_releases`): DB だけ。続刊か欠けかは、表示のときの手元の巻番号で決める
  (蔵書に入った巻は出さない)。「確認済み」(`seen_at`)は再確認でも消さない

規則と理由は wiki/concepts/follow.md。
"""
from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable

from . import db, linking, works
from .metadata import MetadataService

log = logging.getLogger(__name__)

SOURCE = "ndl"  # 巻ごとの書誌を持つ取得元。メディア芸術データベース / Bangumi は作品単位で、巻の発売を持たない

# 調べた結果(follows.status)
STATUS_OK = "ok"                    # 連携した組の書誌で数えた
STATUS_UNLINKED = "unlinked"        # 国立国会図書館サーチの連携が無い(確認待ち・該当なし)。作品ページで候補を選ぶ
STATUS_NO_NUMBERS = "no_numbers"    # 手元の巻に巻番号が読める巻が無く、何が欠けているか数えられない
STATUS_KIND_MISMATCH = "kind_mismatch"  # 連携した組の種別が作品の種別と違う(数えない)
STATUS_DISABLED = "disabled"        # 国立国会図書館サーチが設定で無効
STATUS_FAILED = "failed"            # 取得に失敗し、前回までの連携の中身も無い


class NotFollowed(works.Conflict):
    pass


# --- フォローする・外す --------------------------------------------------------------------


def _root(c, work_id: int) -> int:
    if works.root_of(c, work_id) != work_id:
        raise works.WorkNotFound(work_id)
    return work_id


def follow(conn: sqlite3.Connection, work_id: int) -> None:
    with db.transaction(conn) as c:
        _root(c, work_id)
        c.execute("INSERT OR IGNORE INTO follows (work_id, followed_at) VALUES (?, ?)", (work_id, db.now()))


def unfollow(conn: sqlite3.Connection, work_id: int) -> None:
    """フォローを外す。見つけた巻の記録(確認済みの印)は残す(フォローし直したときに同じ巻を再び出さない)。"""
    with db.transaction(conn) as c:
        _root(c, work_id)
        c.execute("DELETE FROM follows WHERE work_id = ?", (work_id,))


def resolve_merged(conn: sqlite3.Connection) -> None:
    """統合で吸収された作品のフォローと見つけた巻を、吸収先へ移す(確認済みの印はどちらかにあれば残す)。"""
    with db.transaction(conn) as c:
        rows = c.execute("SELECT f.work_id FROM follows f JOIN works w ON w.id = f.work_id "
                         "WHERE w.merged_into IS NOT NULL").fetchall()
        rows += c.execute("SELECT DISTINCT r.work_id FROM follow_releases r JOIN works w ON w.id = r.work_id "
                          "WHERE w.merged_into IS NOT NULL").fetchall()
        for old in {r["work_id"] for r in rows}:
            new = works.root_of(c, old)
            if new is None or new == old:
                continue
            f = c.execute("SELECT * FROM follows WHERE work_id = ?", (old,)).fetchone()
            if f is not None:
                c.execute("INSERT OR IGNORE INTO follows (work_id, followed_at) VALUES (?, ?)", (new, f["followed_at"]))
                c.execute("DELETE FROM follows WHERE work_id = ?", (old,))
            c.execute("""UPDATE follow_releases SET seen_at = COALESCE(seen_at, (SELECT o.seen_at FROM follow_releases o
                         WHERE o.work_id = ? AND o.volume = follow_releases.volume)) WHERE work_id = ?""", (old, new))
            c.execute("""INSERT OR IGNORE INTO follow_releases (work_id, volume, source, record_id, title, volume_label,
                         released, isbn, url, found_at, updated_at, seen_at)
                         SELECT ?, volume, source, record_id, title, volume_label, released, isbn, url, found_at,
                         updated_at, seen_at FROM follow_releases WHERE work_id = ?""", (new, old))
            c.execute("DELETE FROM follow_releases WHERE work_id = ?", (old,))


# --- 調べる ----------------------------------------------------------------------------


@dataclass
class CheckResult:
    work_id: int
    status: str
    found: list[int] = field(default_factory=list)      # 蔵書に無い巻番号(確認済みも含む)
    new_unseen: list[int] = field(default_factory=list)  # 今回はじめて見つかった巻番号
    failure: dict | None = None


def _owned_numbers(c, work_id: int) -> set[int]:
    # 一覧に見えている巻(オフラインのルートの巻も含む)の巻番号。範囲の巻(v01-03)は範囲すべてを持っているとみなす
    return set(linking._context(c, work_id).numbers)


def _records(item: dict) -> dict[int, dict]:
    out: dict[int, dict] = {}
    for rec in item.get("records") or []:
        v = rec.get("volume")
        if isinstance(v, int) and v >= 0:
            out.setdefault(v, rec)  # 同じ巻次の別の書誌(新装版の同じ組など)は最初のもの
    return out


def check_work(conn: sqlite3.Connection, service: MetadataService, work_id: int) -> CheckResult:
    """フォロー中の作品 1 つの続刊・欠けを調べる。取得元の失敗は記録して返す(例外にしない)。

    service は続刊用の短い有効期限の設定で作ったもの(`spinebox/app.py follow_service`)。
    """
    with db.transaction(conn) as c:
        _root(c, work_id)
        if c.execute("SELECT 1 FROM follows WHERE work_id = ?", (work_id,)).fetchone() is None:
            raise NotFollowed("この作品はフォローしていません")
        if not _owned_numbers(c, work_id):
            # 何が欠けているか数えられない。取得元にも問い合わせない
            return _finish(conn, CheckResult(work_id, STATUS_NO_NUMBERS), c)
    if SOURCE not in service.sources:
        return _finish(conn, CheckResult(work_id, STATUS_DISABLED))
    try:
        linking.fetch_work(conn, service, work_id, sources=(SOURCE,))
    except works.WorkNotFound:
        raise
    except Exception:  # noqa: BLE001 (fetch_work は raise しないが、念のため。前回までの連携で数える)
        log.exception("続刊の確認で公開 DB の取得に失敗しました: work=%s", work_id)
    with db.transaction(conn) as c:
        link = linking._link_row(c, work_id, SOURCE)
        failure = json.loads(link["failure"]) if link is not None and link["failure"] else None
        if link is None or link["status"] != "linked" or not link["item"]:
            status = STATUS_FAILED if failure and (link is None or link["status"] is None) else STATUS_UNLINKED
            return _finish(conn, CheckResult(work_id, status, failure=failure), c)
        item = json.loads(link["item"])
        ctx = linking._context(c, work_id)
        if not works.kind_compatible(ctx.kind, item.get("kind")):
            # 漫画と小説が同名で並ぶ作品。作品の種別と違う組の巻は数えない(#50 の判定と同じ)
            return _finish(conn, CheckResult(work_id, STATUS_KIND_MISMATCH, failure=failure), c)
        owned = set(ctx.numbers)
        if not owned:  # 取得の間に巻が外れた
            return _finish(conn, CheckResult(work_id, STATUS_NO_NUMBERS, failure=failure), c)
        records = _records(item)
        now = db.now()
        result = CheckResult(work_id, STATUS_OK, failure=failure)
        existing = {r["volume"]: r for r in c.execute("SELECT * FROM follow_releases WHERE work_id = ?", (work_id,))}
        for volume, rec in sorted(records.items()):
            if volume in owned:
                continue
            result.found.append(volume)
            values = (SOURCE, rec.get("source_id"), rec.get("title"), rec.get("volume_label"), rec.get("released"),
                      rec.get("isbn"), rec.get("url"))
            if volume in existing:
                c.execute("""UPDATE follow_releases SET source = ?, record_id = ?, title = ?, volume_label = ?,
                             released = ?, isbn = ?, url = ?, updated_at = ? WHERE work_id = ? AND volume = ?""",
                          (*values, now, work_id, volume))
            else:
                c.execute("""INSERT INTO follow_releases (work_id, volume, source, record_id, title, volume_label,
                             released, isbn, url, found_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                          (work_id, volume, *values, now, now))
                result.new_unseen.append(volume)
        # 書誌から消えた巻(連携の組が変わったなど)は、まだ確認していないものだけ外す。確認済みの印は残す
        for volume, r in existing.items():
            if volume not in records and r["seen_at"] is None:
                c.execute("DELETE FROM follow_releases WHERE work_id = ? AND volume = ?", (work_id, volume))
        return _finish(conn, result, c)


def _finish(conn, result: CheckResult, c=None) -> CheckResult:
    def write(cc):
        cc.execute("UPDATE follows SET checked_at = ?, status = ?, failure = ? WHERE work_id = ?",
                   (db.now(), result.status, works._dumps(result.failure) if result.failure else None, result.work_id))
    if c is not None:
        write(c)
    else:
        with db.transaction(conn) as cc:
            write(cc)
    return result


def check_many(conn: sqlite3.Connection, service: MetadataService, ids: list[int],
               progress: Callable[[int, int, int | None], None] | None = None) -> dict[str, int]:
    """作品を 1 つずつ調べる(取得元の最小間隔は共有の関門が守る)。結果の状態ごとの件数を返す。

    明示操作「続刊を調べる」と定期実行の入口。#83 のジョブに載せるときもこれを呼ぶ(progress は done, total, 現在の作品)。
    """
    counts: dict[str, int] = {}
    for i, work_id in enumerate(ids):
        if progress:
            progress(i, len(ids), work_id)
        try:
            status = check_work(conn, service, work_id).status
        except (works.WorkNotFound, NotFollowed):
            continue  # 途中で統合された・フォローを外した
        except Exception:  # noqa: BLE001 (1 作品の不具合で残りを止めない)
            log.exception("続刊の確認に失敗しました: work=%s", work_id)
            status = STATUS_FAILED
        counts[status] = counts.get(status, 0) + 1
    if progress:
        progress(len(ids), len(ids), None)
    return counts


def followed_ids(conn: sqlite3.Connection) -> list[int]:
    resolve_merged(conn)
    return [r["work_id"] for r in conn.execute("SELECT work_id FROM follows ORDER BY work_id")]


def works_due(conn: sqlite3.Connection, interval_hours: float, now: datetime | None = None) -> list[int]:
    """定期実行で調べる作品。一度も調べていない作品が先、次に調べたのが古い順。"""
    now = now or datetime.now(timezone.utc)
    resolve_merged(conn)
    due = []
    for r in conn.execute("SELECT work_id, checked_at FROM follows"):
        if r["checked_at"] is None:
            due.append(("", r["work_id"]))
        elif now - datetime.fromisoformat(r["checked_at"]) >= timedelta(hours=interval_hours):
            due.append((r["checked_at"], r["work_id"]))
    return [wid for _, wid in sorted(due)]


# --- 見る(DB だけ) -----------------------------------------------------------------------


def _release_json(r, owned: set[int]) -> dict:
    top = max(owned) if owned else None
    return {"volume": r["volume"], "kind": "new" if top is None or r["volume"] > top else "gap",
            "title": r["title"], "volume_label": r["volume_label"], "released": r["released"], "isbn": r["isbn"],
            "url": r["url"], "source": r["source"], "found_at": r["found_at"], "seen": r["seen_at"] is not None,
            "seen_at": r["seen_at"]}


def _releases_of(c, work_id: int, owned: set[int]) -> list[dict]:
    rows = c.execute("SELECT * FROM follow_releases WHERE work_id = ? ORDER BY volume", (work_id,)).fetchall()
    return [_release_json(r, owned) for r in rows if r["volume"] not in owned]


def work_follow(conn: sqlite3.Connection, work_id: int) -> dict:
    """作品ページに出すフォローの状態。"""
    r = conn.execute("SELECT * FROM follows WHERE work_id = ?", (work_id,)).fetchone()
    if r is None:
        return {"followed": False}
    owned = _owned_numbers(conn, work_id)
    rels = _releases_of(conn, work_id, owned)
    return {"followed": True, "followed_at": r["followed_at"], "checked_at": r["checked_at"], "status": r["status"],
            "failure": json.loads(r["failure"]) if r["failure"] else None, "releases": rels}


def list_follows(conn: sqlite3.Connection) -> list[dict]:
    """フォロー管理の一覧(作品名順)。"""
    resolve_merged(conn)
    out = []
    for r in conn.execute("SELECT * FROM follows").fetchall():
        wid = r["work_id"]
        owned = _owned_numbers(conn, wid)
        rels = _releases_of(conn, wid, owned)
        out.append({
            "work_id": wid, "title": works._title_of(conn, wid)[0], "followed_at": r["followed_at"],
            "checked_at": r["checked_at"], "status": r["status"],
            "failure": json.loads(r["failure"]) if r["failure"] else None,
            "unseen": sum(1 for x in rels if not x["seen"]), "found": len(rels),
        })
    out.sort(key=lambda x: (x["title"], x["work_id"]))
    return out


def list_releases(conn: sqlite3.Connection, include_seen: bool = False) -> list[dict]:
    """サイドバーの「フォロー」: 続刊・欠けのあるフォロー中の作品。最も新しい発売日の作品が先(発売日不明は後ろ)。"""
    resolve_merged(conn)
    out = []
    for r in conn.execute("SELECT * FROM follows").fetchall():
        wid = r["work_id"]
        owned = _owned_numbers(conn, wid)
        rels = [x for x in _releases_of(conn, wid, owned) if include_seen or not x["seen"]]
        if not rels:
            continue
        latest = max((x["released"] for x in rels if x["released"]), default=None)
        out.append({"work_id": wid, "title": works._title_of(conn, wid)[0], "checked_at": r["checked_at"],
                    "status": r["status"], "failure": json.loads(r["failure"]) if r["failure"] else None,
                    "latest_released": latest, "owned_max": max(owned) if owned else None,
                    "releases": sorted(rels, key=lambda x: (x["released"] is None, _desc(x["released"]), -x["volume"]))})
    out.sort(key=lambda x: (x["latest_released"] is None, _desc(x["latest_released"]), x["title"]))
    return out


def _desc(text: str | None) -> tuple:
    # 発売日の新しい順に並べるための鍵。`2015` / `2015-03` / `2015-03-15` を `YYYY-MM-DD` に足してから比べる。
    # 足す月・日は 00 なので、同じ年・月の中では月・日が分かるものより後ろになる(足さないと `2015` が `2015-03` より先)
    if not text:
        return ()
    parts = [p.zfill(2) for p in text.split("-")[:3]]
    parts += ["00"] * (3 - len(parts))
    return tuple(-ord(ch) for ch in "-".join(parts))


def mark_seen(conn: sqlite3.Connection, work_id: int, volumes: list[int] | None = None) -> int:
    """見つけた巻を「確認済み」にする(volumes が None なら、その作品のまだ確認していない巻すべて)。"""
    with db.transaction(conn) as c:
        _root(c, work_id)
        now = db.now()
        if volumes is None:
            cur = c.execute("UPDATE follow_releases SET seen_at = ? WHERE work_id = ? AND seen_at IS NULL",
                            (now, work_id))
        else:
            marks = ",".join("?" * len(volumes)) or "NULL"
            cur = c.execute(f"UPDATE follow_releases SET seen_at = ? WHERE work_id = ? AND seen_at IS NULL "
                            f"AND volume IN ({marks})", (now, work_id, *volumes))
        return cur.rowcount
