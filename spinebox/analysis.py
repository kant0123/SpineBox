"""巻への分解と、名前の巻範囲と中身の食い違いの検出。

- 分解: 内側アーカイブの巻を開いたときに見つかった巻の単位(画像を直接含むフォルダ、またはアーカイブの直下)が
  2 つ以上なら、それぞれを子の巻として登録する。親は「まとめ」として残し(開けば全ページを読める)、一覧には子を出す。
  子は (親, inner_path, entry_prefix) で突き合わせるので、解析し直しても同じ単位は同じ巻 id のまま(読書位置が続く)。
  見えなくなった子は missing にするだけで消さない
- 食い違い: 名前の巻範囲(`v01-03` → 1〜3)と、中の巻の単位の巻番号(これも名前から読んだ候補)を比べ、
  欠け(missing)と余り(extra)を出す。同じ巻番号が複数ある(別スキャンの重複)は食い違いにしない。
  重複の判定は後続の重複検出の役目で、ここで騒ぐと実データのほとんどの複数巻まとめに印が付くため
"""
from __future__ import annotations

import json
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import PurePosixPath

from . import db
from .naming import NumberCandidate, parse_number

# 巻の単位の見分け方・登録の仕方を変えたら上げる(解析済みの巻を開いたときに解析し直させる)
ANALYSIS_VERSION = 1

# (内側アーカイブから単位のあるアーカイブまでの入れ子の名前, アーカイブ内のフォルダ(末尾 `/`。直下なら空))
UnitKey = tuple[tuple[str, ...], str]


@dataclass(frozen=True)
class Unit:
    chain: tuple[str, ...]
    prefix: str
    title: str
    page_names: list[str]

    @property
    def key(self) -> UnitKey:
        return (self.chain, self.prefix)

    @property
    def is_container_root(self) -> bool:
        """親の内側アーカイブ直下の画像。名前は親と同じなので、巻番号を名前から読まない。"""
        return not self.chain and not self.prefix


def analysis_version(source_version: str, max_depth: int) -> str:
    """解析の版。元ファイル・解析の仕方・入れ子の深さの上限(見える単位が変わる)のどれかが変われば変わる。"""
    return f"{ANALYSIS_VERSION}:{max_depth}:{source_version}"


def _number_columns(cand: NumberCandidate | None) -> tuple:
    return (cand.kind, cand.start, cand.end, cand.evidence) if cand else (None, None, None, None)


def register_units(conn: sqlite3.Connection, parent, units: list[Unit],
                   page_order: list[tuple[UnitKey, str]], version: str) -> list[int]:
    """parent(内側アーカイブの巻)の解析結果を登録し、見えている子の巻の id を返す。

    単位が 1 つ以下なら分解しない(親がそのまま 1 巻)。既にある子は missing にする。
    page_order は親を開いたときのページ順(各ページの単位と名前)で、親の読書位置を子へ引き継ぐのに使う。
    """
    now = db.now()
    parent_id = parent["id"]
    with db.transaction(conn) as c:
        existing = {(r["inner_path"], r["entry_prefix"]): r["id"] for r in c.execute(
            "SELECT id, inner_path, entry_prefix FROM volumes WHERE parent_id = ?", (parent_id,))}
        seen: dict[UnitKey, int] = {}
        if len(units) >= 2:
            for u in units:
                inner_path = json.dumps(list(u.chain), ensure_ascii=False)
                numbers = _number_columns(None if u.is_container_root else parse_number(u.title))
                vid = existing.get((inner_path, u.prefix))
                if vid is not None:
                    c.execute("""UPDATE volumes SET title = ?, page_count = ?, number_kind = ?, number_start = ?,
                                 number_end = ?, number_evidence = ?, missing = 0, updated_at = ? WHERE id = ?""",
                              (u.title, len(u.page_names), *numbers, now, vid))
                else:
                    vid = c.execute(
                        """INSERT INTO volumes (source_id, container_path, entry_prefix, inner_path, parent_id, title,
                           page_count, number_kind, number_start, number_end, number_evidence, created_at, updated_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (parent["source_id"], parent["container_path"], u.prefix, inner_path, parent_id, u.title,
                         len(u.page_names), *numbers, now, now)).lastrowid
                seen[u.key] = vid
            _carry_progress(c, parent_id, units, seen, page_order, now)
            _carry_page_marks(c, parent_id, units, seen, page_order, now)
        for vid in set(existing.values()) - set(seen.values()):
            c.execute("UPDATE volumes SET missing = 1, updated_at = ? WHERE id = ?", (now, vid))
        c.execute("UPDATE volumes SET analyzed_version = ?, analysis_error = NULL, updated_at = ? WHERE id = ?",
                  (version, now, parent_id))
    return list(seen.values())


def _carry_progress(c, parent_id: int, units: list[Unit], ids: dict[UnitKey, int],
                    page_order: list[tuple[UnitKey, str]], now: str) -> None:
    """分解前に親で付けた読書位置を子へ写す。親の記録は消さない。子に既に位置があれば触らない。

    親が既読なら全部の子を既読に。そうでなければ、親の位置のページを含む子はその位置から、
    親の順でそれより前のページしか持たない子は既読にする。
    """
    p = c.execute("SELECT page, finished FROM reading_progress WHERE volume_id = ?", (parent_id,)).fetchone()
    if p is None:
        return
    position = {item: i for i, item in enumerate(page_order)}
    current = page_order[p["page"]] if 0 <= p["page"] < len(page_order) else None
    for u in units:
        vid = ids[u.key]
        if c.execute("SELECT 1 FROM reading_progress WHERE volume_id = ?", (vid,)).fetchone():
            continue
        last = len(u.page_names) - 1
        indexes = [position.get((u.key, n), -1) for n in u.page_names]
        if p["finished"] or max(indexes) < p["page"]:
            page, finished = last, True
        elif current is not None and current[0] == u.key and current[1] in u.page_names:
            page, finished = u.page_names.index(current[1]), False
        else:
            continue
        c.execute("INSERT INTO reading_progress (volume_id, page, finished, updated_at) VALUES (?, ?, ?, ?)",
                  (vid, page, int(finished), now))


def _carry_page_marks(c, parent_id: int, units: list[Unit], ids: dict[UnitKey, int],
                      page_order: list[tuple[UnitKey, str]], now: str) -> None:
    """分解前に親で挟んだしおり(#136)を、そのページを持つ子へ写す。親の記録は消さない(読書位置と同じ)。

    親のページ p は `page_order[p]` で (単位, ページ名) に引け、子ではそのページ名の位置にしおりを置く。
    子に既にあるしおりは触らない(INSERT OR IGNORE)。範囲外のページは飛ばす。
    """
    by_key = {u.key: u for u in units}
    for r in c.execute("SELECT page FROM page_marks WHERE volume_id = ?", (parent_id,)).fetchall():
        if not 0 <= r["page"] < len(page_order):
            continue
        key, name = page_order[r["page"]]
        u = by_key.get(key)
        if u is None or name not in u.page_names:
            continue
        c.execute("INSERT OR IGNORE INTO page_marks (volume_id, page, created_at) VALUES (?, ?, ?)",
                  (ids[key], u.page_names.index(name), now))


def record_error(conn: sqlite3.Connection, volume_id: int, reason: str) -> None:
    """解析できなかった理由を残す。版は 'error' にして、裏の解析が同じ巻を繰り返し試さないようにする。"""
    with db.transaction(conn) as c:
        c.execute("UPDATE volumes SET analyzed_version = 'error', analysis_error = ? WHERE id = ?", (reason, volume_id))


# --- 食い違いの検出 ---------------------------------------------------------------


def _candidate(row) -> NumberCandidate | None:
    if row["number_kind"] is None:
        return None
    return NumberCandidate(row["number_kind"], row["number_start"], row["number_end"], row["number_evidence"] or "",
                           "stored")


def _fmt(numbers: list[int]) -> str:
    return "・".join(str(n) for n in numbers)


def _range_text(cand: NumberCandidate) -> str:
    return f"{cand.start}" if cand.start == cand.end else f"{cand.start}〜{cand.end}"


def compare_range(scope: str, subject: str, name: NumberCandidate | None,
                  units: list[NumberCandidate | None]) -> dict | None:
    """名前の巻範囲と中の単位の巻番号を比べ、欠けか余りがあれば finding を返す。

    比べるのは巻(volume)どうしだけ。話数の名前・番号の読めない単位は数えるだけ(unnumbered)。
    """
    if name is None or name.kind != "volume" or not units:
        return None
    counts: Counter[int] = Counter()
    unnumbered = 0
    for u in units:
        if u is None or u.kind != "volume":
            unnumbered += 1
            continue
        counts.update(u.numbers)
    expected = set(name.numbers)
    missing = sorted(expected - set(counts))
    extra = sorted(set(counts) - expected)
    if not missing and not extra:
        return None
    parts = []
    if missing:
        parts.append(f"{_fmt(missing)} 巻が見当たらない")
    if extra:
        parts.append(f"名前の範囲に無い {_fmt(extra)} 巻が入っている")
    message = f"名前「{name.evidence}」は {_range_text(name)} 巻だが、" + "。".join(parts)
    if unnumbered:
        message += f"(巻番号の分からない中身が {unnumbered} 個ある)"
    return {
        "scope": scope,
        "subject": subject,
        "name_range": name.as_dict(),
        "found": sorted(counts),
        "missing": missing,
        "extra": extra,
        "duplicates": sorted(n for n, k in counts.items() if k > 1),
        "unnumbered": unnumbered,
        "message": message,
    }


def compute_findings(conn: sqlite3.Connection) -> dict[int, list[dict]]:
    """見えている巻(親を含む)ごとの食い違い。保存しない(巻番号は DB にあるので毎回ここから導出する)。

    - volume: 分解した親の名前の範囲 ⇔ 子の巻番号。親と各子に付ける
    - source: 外側アーカイブの名前の範囲 ⇔ その中の巻(分解済みなら子、未解析の内側アーカイブはその名前の範囲)。
      そのソースの巻すべてに付ける。外側アーカイブの直下の画像だけの巻(名前が同じ)は比べない
    """
    rows = conn.execute(
        """SELECT v.id, v.parent_id, v.source_id, v.title, v.container_path, v.entry_prefix,
                  v.number_kind, v.number_start, v.number_end, v.number_evidence, s.rel_path, s.kind
           FROM volumes v JOIN sources s ON s.id = v.source_id JOIN library_roots r ON r.id = s.root_id
           WHERE r.enabled = 1 AND v.missing = 0 AND s.missing = 0""").fetchall()
    by_id = {r["id"]: r for r in rows}
    children: dict[int, list] = defaultdict(list)
    tops: dict[int, list] = defaultdict(list)
    for r in rows:
        if r["parent_id"] is None:
            tops[r["source_id"]].append(r)
        elif r["parent_id"] in by_id:
            children[r["parent_id"]].append(r)
    result: dict[int, list[dict]] = defaultdict(list)

    for parent_id, kids in children.items():
        parent = by_id[parent_id]
        f = compare_range("volume", parent["title"], _candidate(parent), [_candidate(k) for k in kids])
        if f:
            for vid in [parent_id] + [k["id"] for k in kids]:
                result[vid].append(f)

    for source_id, top in tops.items():
        first = top[0]
        if first["kind"] != "archive":
            continue
        if all(t["container_path"] is None and t["entry_prefix"] == "" for t in top):
            continue  # 直下の画像だけ = アーカイブ自体が 1 巻。名前どうしを比べることになる
        units: list[NumberCandidate | None] = []
        members: list[int] = []
        for t in top:
            kids = children.get(t["id"])
            members.append(t["id"])
            if kids:
                units += [_candidate(k) for k in kids]
                members += [k["id"] for k in kids]
            else:
                units.append(_candidate(t))
        subject = PurePosixPath(first["rel_path"]).name
        f = compare_range("source", subject, parse_number(subject), units)
        if f:
            for vid in members:
                same = any(g["missing"] == f["missing"] and g["extra"] == f["extra"] for g in result[vid])
                if not same:
                    result[vid].append(f)
    return dict(result)
