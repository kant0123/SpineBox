"""作品の名寄せ(GOAL.md フェーズ 3)。巻を作品にまとめ、別名・著者・統合の根拠を持つ。

- 作品(`works`)は**派生**で、巻の名前から作る。名前は候補なので(CLAUDE.md「蔵書を扱う不変条件」)、
  作品の項目(`work_fields`)・別名(`work_aliases`)・巻の割り当て(`work_volumes`)は、出所・確信度・
  根拠・「手動か」を 1 件ごとに持つ。手動のものは再計算(`sync`)で上書きしない。外部ソース(公開 DB)は
  空欄と別名だけを足す
- 割り当て: 巻の**自分の名前**(巻の名前 → 入れ子の名前 → 外側のアーカイブ・フォルダの名前の順で最初に作品名と読めたもの)
  の正規化が、既存の作品の名前(作品名か別名)と一致すればその作品へ、無ければ新しい作品へ
- 統合: 別々の作品が同じ本と言える強い根拠があれば**統合の提案**を出す。確信度が高く、手動の決定が絡まないものだけ自動で統合し、
  残りは確認待ち(`open`)。根拠は (1) 別スキャンの重複候補(spinebox/duplicates.py。却下されたものは使わない)、
  (2) 別名と作品名の一致。却下した提案・利用者が分けた組は再計算で戻さない
- 蔵書には触れない。スキャンも内側アーカイブを取り出さない(名前の解析と DB の更新だけ)
"""
from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field

from . import db
from .duplicates import candidate_counts
from .library import VolumeNotFound, is_offline
from .naming import normalize_title, parse_work_name
from .pages import natural_key
from .scanner import LISTED_VOLUMES_JOIN, LISTED_VOLUMES_WHERE

FIELD_TITLE = "title"
FIELD_AUTHOR = "author"
FIELD_KIND = "kind"                # manga / novel
FIELD_PUBLISHER = "publisher"
FIELD_GENRES = "genres"            # JSON の文字列配列
FIELD_TAGS = "tags"                # JSON の文字列配列
FIELD_SYNOPSIS = "synopsis"
FIELD_TOTAL_VOLUMES = "total_volumes"  # 全何巻か(公開 DB だけが持つ。手で編集する項目ではない)
EDITABLE_FIELDS = (FIELD_TITLE, FIELD_AUTHOR, FIELD_KIND, FIELD_PUBLISHER, FIELD_GENRES, FIELD_TAGS, FIELD_SYNOPSIS)
LIST_FIELDS = (FIELD_GENRES, FIELD_TAGS)
KINDS = ("manga", "novel")

# 巻単位の書誌(`volume_fields`)。手で直せるのは publisher / label / released / isbn
VOLUME_EDITABLE_FIELDS = ("publisher", "label", "released", "isbn")
VOLUME_FIELDS = (*VOLUME_EDITABLE_FIELDS, "volume_label", "subtitle")

# 取得元の表示名(取得元の順は結線の検索の順)
SOURCE_LABELS = {"ndl": "国立国会図書館サーチ", "anilist": "AniList", "mangaupdates": "MangaUpdates"}

# 出所(source)。work_fields / work_aliases / work_volumes / volume_fields で共通。公開 DB は `public_db`(結線は spinebox/linking.py)
SOURCE_FILENAME = "filename"      # 巻自身の名前から
SOURCE_CONTAINER = "container"    # 外側のアーカイブ・フォルダの名前から
SOURCE_DUPLICATE = "duplicate"    # 重複候補の証拠で統合した
SOURCE_ALIAS = "alias"            # 別名と作品名の一致で統合した
SOURCE_MERGE = "merge"            # 統合された作品の名前(別名になった)
SOURCE_MANUAL = "manual"          # 利用者が決めた
SOURCE_PREVIOUS = "previous_title"  # 手で直す前の作品名(別名に残す)
SOURCE_PUBLIC_DB = "public_db"    # 公開 DB(国立国会図書館サーチ・AniList・MangaUpdates)から

# 確信度(0〜1)。しきい値の根拠は wiki/concepts/work-grouping.md
CONF_OWN_NAME = 0.9          # 巻自身の名前から作品名を読めた
CONF_ENCLOSING_NAME = 0.6    # 巻自身の名前に作品名が無く、外側の名前から読んだ
CONF_CONTAINER_ALIAS = 0.5   # 作品の全部の巻を含む外側のアーカイブの名前(別名・著者の候補)
AUTO_MERGE_MIN = 0.9         # この確信度以上で、手動の決定が絡まなければ自動で統合する
PROPOSE_MIN = 0.5            # これ未満は提案にも出さない
VISUAL_ONLY_CAP = 0.85       # 見た目だけ(巻番号が不明)の重複候補は自動統合しない
MAX_MISSING_SPAN = 500       # 巻番号の最大がこれを超えたら欠けを数えない(名前の誤読で何千件も出さない)
SAMPLE_VOLUMES = 6           # 提案の画面に出す、作品ごとの巻の例

AUTHOR_SEPARATOR = "、"


class WorkNotFound(LookupError):
    pass


class ProposalNotFound(LookupError):
    pass


class Conflict(RuntimeError):
    """今の状態では行えない操作(統合済み・同じ作品など)。メッセージは利用者向け。"""


class InvalidValue(ValueError):
    """値が使えない(空の作品名など)。メッセージは利用者向け。"""


# --- 名前の連鎖 ------------------------------------------------------------------------


def _segments(path: str | None) -> list[str]:
    return [s for s in (path or "").replace("\\", "/").split("/") if s]


def name_chain(row) -> list[str]:
    """巻の名前を内側から外側へ並べる。先頭が巻自身の名前。

    巻の名前 → アーカイブ内のフォルダ → 入れ子のアーカイブ(分解した子の親側)→ 内側アーカイブの名前 →
    外側のアーカイブ・フォルダと、その上のフォルダ。
    """
    names = [row["title"]]
    names += reversed(_segments(row["entry_prefix"]))
    try:
        inner = json.loads(row["inner_path"]) if row["inner_path"] else []
    except ValueError:
        inner = []
    names += reversed([str(x) for x in inner])
    names += reversed(_segments(row["container_path"]))
    names += reversed(_segments(row["rel_path"]))
    return names


@dataclass(frozen=True)
class OwnName:
    """巻の作品名の候補。level 0 = 巻自身の名前、1 以上 = 外側の名前から読んだ。"""
    name: object  # WorkName
    level: int
    text: str

    @property
    def confidence(self) -> float:
        return CONF_OWN_NAME if self.level == 0 else CONF_ENCLOSING_NAME

    def evidence(self) -> dict:
        return {"name": self.text, "level": "volume" if self.level == 0 else "enclosing", **self.name.as_dict()}


def own_name(row) -> OwnName | None:
    for level, text in enumerate(name_chain(row)):
        wn = parse_work_name(text)
        if wn is not None:
            return OwnName(wn, level, text)
    return None


def _has_cjk(text: str) -> bool:
    return any("぀" <= ch <= "ヿ" or "一" <= ch <= "鿿" for ch in text)


def _dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True)


# --- メモリ上の状態(sync 用) ---------------------------------------------------------------


@dataclass
class _Name:
    display: str
    source: str
    confidence: float
    is_title: bool


@dataclass
class _Work:
    id: int
    title: str = ""
    title_manual: bool = False
    title_source: str = SOURCE_FILENAME
    author: str | None = None
    author_manual: bool = False
    names: dict[str, _Name] = field(default_factory=dict)
    manual_volumes: int = 0


class _State:
    def __init__(self, c: sqlite3.Connection):
        self.vols = c.execute(
            "SELECT v.id, v.title, v.container_path, v.entry_prefix, v.inner_path, v.parent_id, "
            "s.id AS source_id, s.rel_path " + LISTED_VOLUMES_JOIN + LISTED_VOLUMES_WHERE + " ORDER BY v.id").fetchall()
        self.listed = {r["id"] for r in self.vols}
        self.works: dict[int, _Work] = {}
        for r in c.execute("SELECT id FROM works WHERE merged_into IS NULL"):
            self.works[r["id"]] = _Work(r["id"])
        for r in c.execute("SELECT * FROM work_fields"):
            w = self.works.get(r["work_id"])
            if w is None:
                continue
            if r["field"] == FIELD_TITLE:
                w.title, w.title_manual, w.title_source = r["value"] or "", bool(r["manual"]), r["source"]
                norm = normalize_title(w.title)
                if norm:
                    w.names[norm] = _Name(w.title, r["source"], r["confidence"] or CONF_OWN_NAME, True)
            elif r["field"] == FIELD_AUTHOR:
                w.author, w.author_manual = r["value"] or None, bool(r["manual"])
        for r in c.execute("SELECT * FROM work_aliases WHERE dismissed_at IS NULL ORDER BY id"):
            w = self.works.get(r["work_id"])
            if w is not None and r["norm"] not in w.names:
                w.names[r["norm"]] = _Name(r["alias"], r["source"], r["confidence"] or CONF_CONTAINER_ALIAS, False)
        self.assign: dict[int, dict] = {}
        for r in c.execute("SELECT * FROM work_volumes"):
            self.assign[r["volume_id"]] = dict(r)
            w = self.works.get(r["work_id"])
            if w is not None and r["manual"]:
                w.manual_volumes += 1
        self.by_norm: dict[str, list[int]] = defaultdict(list)
        for w in self.works.values():
            for norm, n in w.names.items():
                self._add_index(w.id, norm, n)

    def _add_index(self, work_id: int, norm: str, n: _Name) -> None:
        # 外側のアーカイブ名から付けた別名は、巻の割り当てには使わない(提案の根拠にだけ使う)
        if n.source != SOURCE_CONTAINER and work_id not in self.by_norm[norm]:
            self.by_norm[norm].append(work_id)

    def find_work(self, norm: str) -> int | None:
        cands = [i for i in self.by_norm.get(norm, ()) if i in self.works]
        if not cands:
            return None
        cands.sort(key=lambda i: (not self.works[i].names[norm].is_title, i))
        return cands[0]


@dataclass
class SyncSummary:
    works_created: int = 0
    volumes_assigned: int = 0
    merged: int = 0
    proposals_open: int = 0


# --- 書き込みの部品 --------------------------------------------------------------------------


def _set_field(c, work_id: int, name: str, value: str | None, source: str, manual: bool, confidence: float | None,
               evidence: dict | None) -> None:
    c.execute(
        """INSERT INTO work_fields (work_id, field, value, source, manual, confidence, evidence, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT (work_id, field) DO UPDATE SET value = excluded.value, source = excluded.source,
               manual = excluded.manual, confidence = excluded.confidence, evidence = excluded.evidence,
               updated_at = excluded.updated_at""",
        (work_id, name, value, source, int(manual), confidence, _dumps(evidence) if evidence else None, db.now()))
    c.execute("UPDATE works SET updated_at = ? WHERE id = ?", (db.now(), work_id))


def _new_work(c, title: str, *, source: str, manual: bool, confidence: float, evidence: dict | None,
              author: str | None = None, author_source: str = SOURCE_FILENAME, author_confidence: float | None = None,
              author_evidence: dict | None = None) -> int:
    now = db.now()
    work_id = c.execute("INSERT INTO works (created_at, updated_at) VALUES (?, ?)", (now, now)).lastrowid
    _set_field(c, work_id, FIELD_TITLE, title, source, manual, confidence, evidence)
    if author:
        _set_field(c, work_id, FIELD_AUTHOR, author, author_source, False, author_confidence, author_evidence)
    return work_id


def _insert_alias(c, work_id: int, alias: str, source: str, *, manual: bool, confidence: float | None,
                  evidence: dict | None, revive: bool = False) -> bool:
    """別名を足す。同じ正規化の別名が既にあれば何もしない(外された別名は、revive でなければ戻さない)。"""
    norm = normalize_title(alias)
    if not norm:
        return False
    cur = c.execute(
        """INSERT INTO work_aliases (work_id, alias, norm, source, manual, confidence, evidence, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (work_id, norm) DO NOTHING""",
        (work_id, alias, norm, source, int(manual), confidence, _dumps(evidence) if evidence else None, db.now()))
    if cur.rowcount == 0 and revive:
        c.execute("""UPDATE work_aliases SET dismissed_at = NULL, alias = ?, source = ?, manual = ?, confidence = ?,
                     evidence = ? WHERE work_id = ? AND norm = ? AND dismissed_at IS NOT NULL""",
                  (alias, source, int(manual), confidence, _dumps(evidence) if evidence else None, work_id, norm))
        return True
    return cur.rowcount > 0


def _put_assignment(c, volume_id: int, work_id: int, source: str, manual: bool, confidence: float | None,
                    evidence: dict | None) -> None:
    c.execute(
        """INSERT INTO work_volumes (volume_id, work_id, source, manual, confidence, evidence, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT (volume_id) DO UPDATE SET work_id = excluded.work_id, source = excluded.source,
               manual = excluded.manual, confidence = excluded.confidence, evidence = excluded.evidence,
               updated_at = excluded.updated_at""",
        (volume_id, work_id, source, int(manual), confidence, _dumps(evidence) if evidence else None, db.now()))


def author_text(authors) -> str | None:
    return AUTHOR_SEPARATOR.join(authors) if authors else None


def transfer_assignment(c: sqlite3.Connection, from_id: int, to_id: int) -> None:
    """巻の作品の割り当て(手動の決定を含む)を別の巻へ写す。整理で巻が CBZ の新しい巻に置き換わるときに使う。"""
    row = c.execute("SELECT * FROM work_volumes WHERE volume_id = ?", (from_id,)).fetchone()
    if row is None:
        return
    _put_assignment(c, to_id, row["work_id"], row["source"], bool(row["manual"]), row["confidence"],
                    json.loads(row["evidence"]) if row["evidence"] else None)


# --- 統合 ------------------------------------------------------------------------------------


def _roots(c) -> dict[int, int]:
    return {r["id"]: r["merged_into"] for r in c.execute("SELECT id, merged_into FROM works WHERE merged_into IS NOT NULL")}


def root_of(c, work_id: int) -> int | None:
    """統合されていれば吸収先、そうでなければ自分。作品が無ければ None。"""
    row = c.execute("SELECT id, merged_into FROM works WHERE id = ?", (work_id,)).fetchone()
    return None if row is None else (row["merged_into"] or row["id"])


def _title_of(c, work_id: int) -> tuple[str, bool]:
    r = c.execute("SELECT value, manual FROM work_fields WHERE work_id = ? AND field = ?",
                  (work_id, FIELD_TITLE)).fetchone()
    return (r["value"] or "", bool(r["manual"])) if r else ("", False)


def choose_survivor(c, a: int, b: int) -> tuple[int, int]:
    """統合で残す作品と吸収される作品。手動の作品名 > 日本語の作品名 > 巻の多い方 > id の若い方。"""
    def key(i: int):
        title, manual = _title_of(c, i)
        n = c.execute("SELECT COUNT(*) FROM work_volumes WHERE work_id = ?", (i,)).fetchone()[0]
        return (manual, _has_cjk(title), n, -i)
    keep, drop = (a, b) if key(a) >= key(b) else (b, a)
    return keep, drop


def merge_works(c, keep: int, drop: int, *, rule: str, confidence: float, manual: bool) -> None:
    """drop を keep に統合する(巻・別名・空欄の著者を移す)。drop の行は消さず merged_into を立てる。

    手動の統合・承認(manual)で動かした巻は手動の割り当てにする。drop の作品名は keep の別名に残す
    (その名前の巻がまた見つかっても、keep に入る)。
    """
    source = {"duplicate": SOURCE_DUPLICATE, "alias": SOURCE_ALIAS}.get(rule, SOURCE_MANUAL)
    merge_info = {"merged_from": drop, "merged_into": keep, "rule": rule, "confidence": confidence}
    for m in c.execute("SELECT * FROM work_volumes WHERE work_id = ?", (drop,)).fetchall():
        was_manual = bool(m["manual"])
        ev = {"merge": merge_info, "previous": {"source": m["source"], "manual": was_manual,
                                                "confidence": m["confidence"],
                                                "evidence": json.loads(m["evidence"]) if m["evidence"] else None}}
        _put_assignment(c, m["volume_id"], keep, m["source"] if was_manual else source, was_manual or manual,
                        m["confidence"] if was_manual else confidence, ev)
    keep_norm = normalize_title(_title_of(c, keep)[0])
    drop_title, _ = _title_of(c, drop)
    if normalize_title(drop_title) != keep_norm:
        _insert_alias(c, keep, drop_title, SOURCE_MERGE, manual=manual, confidence=confidence, evidence=merge_info,
                      revive=True)
    for a in c.execute("SELECT * FROM work_aliases WHERE work_id = ? AND dismissed_at IS NULL", (drop,)).fetchall():
        if a["norm"] != keep_norm:
            _insert_alias(c, keep, a["alias"], a["source"], manual=bool(a["manual"]), confidence=a["confidence"],
                          evidence=json.loads(a["evidence"]) if a["evidence"] else None, revive=True)
    for name in (FIELD_AUTHOR, FIELD_KIND, FIELD_PUBLISHER, FIELD_GENRES, FIELD_TAGS, FIELD_SYNOPSIS,
                 FIELD_TOTAL_VOLUMES):
        kept = c.execute("SELECT value, manual FROM work_fields WHERE work_id = ? AND field = ?",
                         (keep, name)).fetchone()
        dropped = c.execute("SELECT * FROM work_fields WHERE work_id = ? AND field = ?", (drop, name)).fetchone()
        if dropped and dropped["value"] and (
                kept is None or (not kept["manual"] and (not kept["value"] or dropped["manual"]))):
            # 手動の値は、名前や公開 DB から入った値より優先して残す(吸収される側が手動なら、その値を移す)
            _set_field(c, keep, name, dropped["value"], dropped["source"], bool(dropped["manual"]),
                       dropped["confidence"], json.loads(dropped["evidence"]) if dropped["evidence"] else None)
    _merge_links(c, keep, drop)
    now = db.now()
    c.execute("UPDATE works SET merged_into = ?, updated_at = ? WHERE id = ?", (keep, now, drop))
    c.execute("UPDATE works SET merged_into = ?, updated_at = ? WHERE merged_into = ?", (keep, now, drop))
    c.execute("UPDATE works SET updated_at = ? WHERE id = ?", (now, keep))


def _merge_links(c, keep: int, drop: int) -> None:
    """吸収される作品の公開 DB との連携を、残る作品へ移す。残る側に同じ取得元の連携が無いとき、
    または吸収される側が手動で選んだものを残る側が手動で選んでいないときだけ(手動の決定を守る)。"""
    for dl in c.execute("SELECT * FROM work_links WHERE work_id = ?", (drop,)).fetchall():
        kl = c.execute("SELECT * FROM work_links WHERE work_id = ? AND source = ?", (keep, dl["source"])).fetchone()
        if kl is not None:
            better = (dl["manual"] and not kl["manual"]) or (
                not kl["manual"] and kl["status"] != "linked" and dl["status"] == "linked")
            if not better:
                continue
            c.execute("DELETE FROM work_links WHERE work_id = ? AND source = ?", (keep, dl["source"]))
        c.execute("UPDATE work_links SET work_id = ? WHERE work_id = ? AND source = ?", (keep, drop, dl["source"]))


def _manual_involved(c, work_id: int) -> bool:
    if c.execute("SELECT 1 FROM work_fields WHERE work_id = ? AND manual = 1 LIMIT 1", (work_id,)).fetchone():
        return True  # 手で直した作品名・著者
    if c.execute("SELECT 1 FROM work_volumes WHERE work_id = ? AND manual = 1 LIMIT 1", (work_id,)).fetchone():
        return True  # 手動の巻の割り当て
    # 利用者が選んだ公開 DB の連携(「どれでもない」も)。統合で片方が消えると、その決定が失われうる
    return c.execute("SELECT 1 FROM work_links WHERE work_id = ? AND manual = 1 LIMIT 1", (work_id,)).fetchone() is not None


def _pair(a: int, b: int) -> tuple[int, int]:
    return (a, b) if a < b else (b, a)


# --- sync ------------------------------------------------------------------------------------


def sync(conn: sqlite3.Connection) -> SyncSummary:
    """一覧に見えている巻を作品に割り当て、統合の提案を作り直す。冪等で、変化が無ければ書かない。

    手動の項目・割り当て・別名、却下した提案は触らない。1 つのトランザクションで行う(同時に 2 つ走っても
    同じ名前の作品を二重に作らない)。
    """
    summary = SyncSummary()
    with db.transaction(conn) as c:
        st = _State(c)
        _assign_volumes(c, st, summary)
        _container_aliases(c, _State(c))  # 提案の根拠になるので、統合の前に別名を足す
        _propose_and_merge(c, _State(c), summary)
        summary.proposals_open = c.execute(
            "SELECT COUNT(*) FROM work_merge_proposals WHERE status = 'open'").fetchone()[0]
    return summary


def _assign_volumes(c, st: _State, summary: SyncSummary) -> None:
    for r in st.vols:
        vid = r["id"]
        cur = st.assign.get(vid)
        if cur is not None and cur["manual"]:
            continue
        if cur is None and r["parent_id"] is not None:
            parent = st.assign.get(r["parent_id"])
            if parent is not None and parent["manual"]:
                # 分解前のまとめに付けた手動の決定は、中の巻にも及ぶ
                ev = {"inherited_from_volume": r["parent_id"]}
                _put_assignment(c, vid, parent["work_id"], SOURCE_MANUAL, True, parent["confidence"], ev)
                st.assign[vid] = {"work_id": parent["work_id"], "manual": 1, "source": SOURCE_MANUAL}
                summary.volumes_assigned += 1
                continue
        own = own_name(r)
        if own is None:
            continue
        norm = own.name.norm
        work = st.works.get(cur["work_id"]) if cur is not None else None
        if work is not None and norm in work.names:
            if cur["source"] == SOURCE_FILENAME:
                _refresh_assignment(c, vid, cur, own)
            _fill_author(c, work, own.name.authors, SOURCE_FILENAME, own.confidence, own.evidence())
            continue
        target = st.find_work(norm)
        if target is None:
            author = author_text(own.name.authors)
            target = _new_work(c, own.name.title, source=SOURCE_FILENAME, manual=False, confidence=own.confidence,
                               evidence=own.evidence(), author=author, author_source=SOURCE_FILENAME,
                               author_confidence=own.confidence, author_evidence=own.evidence())
            w = _Work(target, own.name.title, False, SOURCE_FILENAME, author, False)
            w.names[norm] = _Name(own.name.title, SOURCE_FILENAME, own.confidence, True)
            st.works[target] = w
            st._add_index(target, norm, w.names[norm])
            summary.works_created += 1
        else:
            _fill_author(c, st.works[target], own.name.authors, SOURCE_FILENAME, own.confidence, own.evidence())
        _put_assignment(c, vid, target, SOURCE_FILENAME, False, own.confidence, own.evidence())
        st.assign[vid] = {"work_id": target, "manual": 0, "source": SOURCE_FILENAME}
        summary.volumes_assigned += 1


def _refresh_assignment(c, vid: int, cur: dict, own: OwnName) -> None:
    """名前から付けた割り当てを、今の名前の読み(確信度・根拠)に合わせる。変化が無ければ書かない。"""
    evidence = _dumps(own.evidence())
    if cur.get("evidence") != evidence or cur.get("confidence") != own.confidence:
        _put_assignment(c, vid, cur["work_id"], SOURCE_FILENAME, False, own.confidence, own.evidence())
        cur["evidence"], cur["confidence"] = evidence, own.confidence


def _fill_author(c, work: _Work, authors, source: str, confidence: float, evidence: dict) -> None:
    """著者が空欄で、手で空にしたのでもなければ、名前から読んだ著者で埋める(あるものは上書きしない)。"""
    text = author_text(authors)
    if text and not work.author and not work.author_manual:
        _set_field(c, work.id, FIELD_AUTHOR, text, source, False, confidence, evidence)
        work.author = text


# --- 統合の提案 ------------------------------------------------------------------------------


def _duplicate_evidence(c, st: _State) -> dict[tuple[int, int], list[dict]]:
    """却下されていない重複候補のうち、別々の作品に割り当てられた巻どうしの組を、作品の組ごとに集める。"""
    titles = {r["id"]: r["title"] for r in st.vols}
    out: dict[tuple[int, int], list[dict]] = defaultdict(list)
    for r in c.execute("SELECT * FROM duplicate_candidates WHERE dismissed_at IS NULL ORDER BY id"):
        a, b = r["volume_a"], r["volume_b"]
        if a not in st.listed or b not in st.listed:
            continue
        wa, wb = st.assign.get(a), st.assign.get(b)
        if wa is None or wb is None or wa["work_id"] == wb["work_id"]:
            continue
        ratio = r["matched_pages"] / r["compared_pages"] if r["compared_pages"] else 0.0
        conf = ratio if r["rule"] == "same_number" else min(ratio, VISUAL_ONLY_CAP)
        a_first = wa["work_id"] < wb["work_id"]
        out[_pair(wa["work_id"], wb["work_id"])].append({
            "candidate_id": r["id"], "rule": r["rule"], "matched_pages": r["matched_pages"],
            "compared_pages": r["compared_pages"], "ratio": round(ratio, 3), "confidence": round(conf, 3),
            "volume_a": {"id": a if a_first else b, "title": titles[a if a_first else b]},
            "volume_b": {"id": b if a_first else a, "title": titles[b if a_first else a]},
        })
    return out


def _alias_evidence(st: _State) -> dict[tuple[int, int], dict]:
    """同じ名前(正規化)を持つ別々の作品の組。名前の確信度の低い方がその組の確信度。

    作品名どうし・作品名と別名の一致、または確かな出所(外側の名前以外)の別名どうしの一致を拾う。
    外側のアーカイブ名から付けた別名どうし(`Collection` など)の一致は拾わない。
    """
    holders: dict[str, list[int]] = defaultdict(list)
    for w in st.works.values():
        for norm in w.names:
            holders[norm].append(w.id)
    out: dict[tuple[int, int], dict] = {}
    for norm, ids in holders.items():
        if len(ids) < 2:
            continue
        for i, x in enumerate(sorted(ids)):
            for y in sorted(ids)[i + 1:]:
                nx, ny = st.works[x].names[norm], st.works[y].names[norm]
                if not (nx.is_title or ny.is_title) and SOURCE_CONTAINER in (nx.source, ny.source):
                    continue
                conf = min(nx.confidence, ny.confidence)
                key = (x, y)
                if key not in out or conf > out[key]["confidence"]:
                    out[key] = {"norm": norm, "confidence": round(conf, 3),
                                "a": {"work": x, "name": nx.display, "source": nx.source, "is_title": nx.is_title},
                                "b": {"work": y, "name": ny.display, "source": ny.source, "is_title": ny.is_title}}
    return out


def _propose_and_merge(c, st: _State, summary: SyncSummary) -> None:
    found: dict[tuple[int, int], dict] = {}
    for pair, items in _duplicate_evidence(c, st).items():
        best = max(items, key=lambda i: i["confidence"])
        found[pair] = {"rule": "duplicate", "confidence": best["confidence"],
                       "evidence": {"duplicate": items, "best": best["confidence"], "pairs": len(items)}}
    for pair, ev in _alias_evidence(st).items():
        if pair not in found or ev["confidence"] > found[pair]["confidence"]:
            found[pair] = {"rule": "alias", "confidence": ev["confidence"], "evidence": {"alias": ev}}

    def existing_index() -> tuple[dict[int, int], dict[tuple[int, int], dict]]:
        roots = _roots(c)
        index: dict[tuple[int, int], dict] = {}
        for r in c.execute("SELECT * FROM work_merge_proposals WHERE status IN ('open', 'dismissed') ORDER BY id"):
            pair = _pair(roots.get(r["work_a"], r["work_a"]), roots.get(r["work_b"], r["work_b"]))
            if pair[0] != pair[1] and (pair not in index or r["status"] == "dismissed"):
                index[pair] = dict(r)
        return roots, index

    roots, index = existing_index()
    kept_open: set[int] = set()
    for pair, cand in sorted(found.items(), key=lambda kv: (-kv[1]["confidence"], kv[0])):
        a, b = _pair(roots.get(pair[0], pair[0]), roots.get(pair[1], pair[1]))
        if a == b or cand["confidence"] < PROPOSE_MIN:
            continue
        ex = index.get((a, b))
        payload = _dumps(cand["evidence"])
        if ex is not None:
            if ex["status"] == "open":  # 確認待ちの提案は根拠だけ最新にする(自動では統合しない)
                c.execute("""UPDATE work_merge_proposals SET work_a = ?, work_b = ?, rule = ?, confidence = ?,
                             evidence = ? WHERE id = ?""", (a, b, cand["rule"], cand["confidence"], payload, ex["id"]))
                kept_open.add(ex["id"])
            continue  # 却下・分離した組は戻さない
        auto = (cand["confidence"] >= AUTO_MERGE_MIN and not _manual_involved(c, a) and not _manual_involved(c, b))
        now = db.now()
        pid = c.execute(
            """INSERT INTO work_merge_proposals (work_a, work_b, rule, confidence, evidence, status, created_at, decided_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (a, b, cand["rule"], cand["confidence"], payload, "auto" if auto else "open", now,
             now if auto else None)).lastrowid
        if auto:
            keep, drop = choose_survivor(c, a, b)
            merge_works(c, keep, drop, rule=cand["rule"], confidence=cand["confidence"], manual=False)
            summary.merged += 1
            roots, index = existing_index()
        else:
            kept_open.add(pid)
            index[(a, b)] = {"id": pid, "status": "open"}
    # 根拠が無くなった(重複候補が却下された・巻が見えなくなった)確認待ちの提案は消す。派生の記録で、利用者の決定ではない
    for r in c.execute("SELECT id FROM work_merge_proposals WHERE status = 'open'").fetchall():
        if r["id"] not in kept_open:
            c.execute("DELETE FROM work_merge_proposals WHERE id = ?", (r["id"],))


def _container_aliases(c, st: _State) -> None:
    """外側のアーカイブ・フォルダの名前を、その中の巻がすべて同じ作品なら、その作品の別名・著者の候補にする。

    いろいろな作品を含む入れ物(`漫画/` や詰め合わせのアーカイブ)の名前は、どの作品の別名にもしない。
    """
    by_source: dict[int, tuple[set[int], str]] = {}
    for r in st.vols:
        a = st.assign.get(r["id"])
        if a is None:
            continue
        works, rel = by_source.setdefault(r["source_id"], (set(), r["rel_path"]))
        works.add(a["work_id"])
    for works, rel in by_source.values():
        if len(works) != 1 or not rel:
            continue
        work = st.works.get(next(iter(works)))
        if work is None:
            continue
        wn = parse_work_name(_segments(rel)[-1])
        if wn is None:
            continue
        evidence = {"source": rel, **wn.as_dict()}
        if wn.norm not in work.names:
            _insert_alias(c, work.id, wn.title, SOURCE_CONTAINER, manual=False, confidence=CONF_CONTAINER_ALIAS,
                          evidence=evidence)
        _fill_author(c, work, wn.authors, SOURCE_CONTAINER, CONF_CONTAINER_ALIAS, evidence)


# --- 手動の操作 ------------------------------------------------------------------------------


def _alive(c, work_id: int) -> int:
    root = root_of(c, work_id)
    if root is None or root != work_id:
        raise WorkNotFound(work_id)
    return root


def _row(c, table: str, key_col: str, key: int, field: str):
    return c.execute(f"SELECT * FROM {table} WHERE {key_col} = ? AND field = ?", (key, field)).fetchone()


def _stash(row) -> dict | None:
    """手で直す前の値(自動に戻すときに復元する)。すでに手動なら、その前の値を引き継ぐ。"""
    if row is None:
        return None
    ev = json.loads(row["evidence"]) if row["evidence"] else None
    if row["manual"]:
        return (ev or {}).get("previous")
    return {"value": row["value"], "source": row["source"], "confidence": row["confidence"], "evidence": ev}


def decode_value(field: str, text: str | None):
    """work_fields.value を API の形にする(genres / tags は配列、total_volumes は整数)。"""
    if text is None:
        return [] if field in LIST_FIELDS else None
    if field in LIST_FIELDS:
        try:
            value = json.loads(text)
        except ValueError:
            return []
        return [str(x) for x in value] if isinstance(value, list) else []
    if field == FIELD_TOTAL_VOLUMES:
        return int(text) if text.isdigit() else None
    return text


def _clean_field(field: str, raw) -> str | None:
    """手で入力された値を保存する形にする。空は None(手動で空にした)。"""
    if field in LIST_FIELDS:
        if raw is None:
            return None
        if not isinstance(raw, (list, tuple)):
            raise InvalidValue("一覧は配列で指定してください")
        items = list(dict.fromkeys(str(x).strip() for x in raw if str(x).strip()))
        return _dumps(items) if items else None
    if raw is not None and not isinstance(raw, str):
        raise InvalidValue("値は文字列で指定してください")
    text = (raw or "").strip()
    if field == FIELD_KIND and text and text not in KINDS:
        raise InvalidValue("種別は manga(漫画)か novel(小説)です")
    return text or None


def edit_fields(conn: sqlite3.Connection, work_id: int, changes: dict[str, object]) -> None:
    """作品の項目を手で直す。手動の項目は再計算でも公開 DB の取得でも上書きしない(空にした場合も)。

    直す前の値は `evidence.previous` に残し、「自動に戻す」で復元する。直す前の作品名は別名にも残す
    (その名前の巻が、また新しい作品として現れないように)。
    """
    unknown = set(changes) - set(EDITABLE_FIELDS)
    if unknown:
        raise InvalidValue(f"直せない項目: {', '.join(sorted(unknown))}")
    with db.transaction(conn) as c:
        _alive(c, work_id)
        for name, raw in changes.items():
            old = _row(c, "work_fields", "work_id", work_id, name)
            previous = _stash(old)
            evidence = {"edited": True, **({"previous": previous} if previous else {})}
            if name == FIELD_TITLE:
                text = (raw or "").strip() if isinstance(raw, str) else ""
                if not normalize_title(text):
                    raise InvalidValue("作品名を入力してください")
                before, _ = _title_of(c, work_id)
                if normalize_title(before) != normalize_title(text):
                    _insert_alias(c, work_id, before, SOURCE_PREVIOUS, manual=True, confidence=1.0,
                                  evidence={"replaced_by": text}, revive=True)
                _set_field(c, work_id, FIELD_TITLE, text, SOURCE_MANUAL, True, 1.0, evidence)
            else:
                _set_field(c, work_id, name, _clean_field(name, raw), SOURCE_MANUAL, True, 1.0, evidence)


def update_fields(conn: sqlite3.Connection, work_id: int, *, title: str | None = None, author: str | None = None,
                  set_title: bool = False, set_author: bool = False) -> None:
    """作品名・著者を手で直す(`edit_fields` の旧い呼び方)。"""
    changes: dict[str, object] = {}
    if set_title:
        changes[FIELD_TITLE] = title
    if set_author:
        changes[FIELD_AUTHOR] = author
    edit_fields(conn, work_id, changes)


def reset_field(conn: sqlite3.Connection, work_id: int, field: str) -> None:
    """手動の項目を自動に戻す。直す前の値があれば復元し、無ければ項目を空に戻す(公開 DB の値は
    呼び出し側が `linking.apply_work` で入れ直す)。作品名は直す前の名前が無いと戻せない。"""
    if field not in EDITABLE_FIELDS:
        raise InvalidValue(f"直せない項目: {field}")
    with db.transaction(conn) as c:
        _alive(c, work_id)
        row = _row(c, "work_fields", "work_id", work_id, field)
        if row is None or not row["manual"]:
            raise Conflict("この項目は手動で編集されていません")
        previous = _stash(row)
        if previous is not None:
            _set_field(c, work_id, field, previous["value"], previous["source"], False, previous["confidence"],
                       previous["evidence"])
            if field == FIELD_TITLE:
                c.execute("UPDATE work_aliases SET dismissed_at = ? WHERE work_id = ? AND norm = ? AND source = ? "
                          "AND dismissed_at IS NULL",
                          (db.now(), work_id, normalize_title(previous["value"] or ""), SOURCE_PREVIOUS))
        elif field == FIELD_TITLE:
            raise Conflict("元の作品名が残っていないため、自動には戻せません")
        else:
            c.execute("DELETE FROM work_fields WHERE work_id = ? AND field = ?", (work_id, field))
            c.execute("UPDATE works SET updated_at = ? WHERE id = ?", (db.now(), work_id))


# --- 巻単位の書誌(volume_fields) --------------------------------------------------------------


def _clean_volume_value(field: str, raw) -> str | None:
    if raw is not None and not isinstance(raw, str):
        raise InvalidValue("値は文字列で指定してください")
    text = unicodedata.normalize("NFKC", raw or "").strip()
    if not text:
        return None
    if field == "isbn":
        digits = re.sub(r"[\s-]", "", text).upper()
        if not re.fullmatch(r"\d{13}|\d{9}[\dX]", digits):
            raise InvalidValue("ISBN は 10 桁か 13 桁で入力してください")
        return digits
    if field == "released" and not re.fullmatch(r"\d{4}(-\d{2}(-\d{2})?)?", text):
        raise InvalidValue("発売日は YYYY、YYYY-MM、YYYY-MM-DD のいずれかで入力してください")
    return text


def _set_volume_field(c, volume_id: int, field: str, value: str | None, source: str, manual: bool,
                      confidence: float | None, evidence: dict | None) -> None:
    c.execute(
        """INSERT INTO volume_fields (volume_id, field, value, source, manual, confidence, evidence, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT (volume_id, field) DO UPDATE SET value = excluded.value, source = excluded.source,
               manual = excluded.manual, confidence = excluded.confidence, evidence = excluded.evidence,
               updated_at = excluded.updated_at""",
        (volume_id, field, value, source, int(manual), confidence, _dumps(evidence) if evidence else None, db.now()))


def edit_volume_fields(conn: sqlite3.Connection, volume_id: int, changes: dict[str, object]) -> None:
    """巻の書誌(出版社・レーベル・発売日・ISBN)を手で直す。再取得・再計算で上書きしない。"""
    unknown = set(changes) - set(VOLUME_EDITABLE_FIELDS)
    if unknown:
        raise InvalidValue(f"直せない項目: {', '.join(sorted(unknown))}")
    cleaned = {name: _clean_volume_value(name, raw) for name, raw in changes.items()}
    with db.transaction(conn) as c:
        if c.execute("SELECT 1 FROM volumes WHERE id = ?", (volume_id,)).fetchone() is None:
            raise VolumeNotFound(volume_id)
        for name, value in cleaned.items():
            previous = _stash(_row(c, "volume_fields", "volume_id", volume_id, name))
            _set_volume_field(c, volume_id, name, value, SOURCE_MANUAL, True, 1.0,
                              {"edited": True, **({"previous": previous} if previous else {})})


def reset_volume_field(conn: sqlite3.Connection, volume_id: int, field: str) -> None:
    """巻の手動の項目を自動に戻す(直す前の値を復元、無ければ空。公開 DB の値は `linking.apply_work` が入れ直す)。"""
    if field not in VOLUME_EDITABLE_FIELDS:
        raise InvalidValue(f"直せない項目: {field}")
    with db.transaction(conn) as c:
        if c.execute("SELECT 1 FROM volumes WHERE id = ?", (volume_id,)).fetchone() is None:
            raise VolumeNotFound(volume_id)
        row = _row(c, "volume_fields", "volume_id", volume_id, field)
        if row is None or not row["manual"]:
            raise Conflict("この項目は手動で編集されていません")
        previous = _stash(row)
        if previous is not None:
            _set_volume_field(c, volume_id, field, previous["value"], previous["source"], False,
                              previous["confidence"], previous["evidence"])
        else:
            c.execute("DELETE FROM volume_fields WHERE volume_id = ? AND field = ?", (volume_id, field))


def transfer_volume_fields(c: sqlite3.Connection, from_id: int, to_id: int) -> None:
    """巻の書誌(手動の編集を含む)を別の巻へ写す。整理で巻が CBZ の新しい巻に置き換わるときに使う。
    写し先で手動の項目は、写し元の自動の値で上書きしない。"""
    for r in c.execute("SELECT * FROM volume_fields WHERE volume_id = ?", (from_id,)).fetchall():
        cur = _row(c, "volume_fields", "volume_id", to_id, r["field"])
        if cur is not None and cur["manual"] and not r["manual"]:
            continue
        _set_volume_field(c, to_id, r["field"], r["value"], r["source"], bool(r["manual"]), r["confidence"],
                          json.loads(r["evidence"]) if r["evidence"] else None)


def add_alias(conn: sqlite3.Connection, work_id: int, alias: str) -> None:
    with db.transaction(conn) as c:
        _alive(c, work_id)
        text = alias.strip()
        norm = normalize_title(text)
        if not norm:
            raise InvalidValue("別名を入力してください")
        if norm == normalize_title(_title_of(c, work_id)[0]):
            raise Conflict("作品名と同じです")
        _insert_alias(c, work_id, text, SOURCE_MANUAL, manual=True, confidence=1.0, evidence={"edited": True},
                      revive=True)


def dismiss_alias(conn: sqlite3.Connection, work_id: int, alias_id: int) -> None:
    """別名を外す。行は残し(dismissed_at)、再計算で同じ別名を戻さない。"""
    with db.transaction(conn) as c:
        _alive(c, work_id)
        cur = c.execute("UPDATE work_aliases SET dismissed_at = ? WHERE id = ? AND work_id = ?",
                        (db.now(), alias_id, work_id))
        if cur.rowcount == 0:
            raise WorkNotFound(alias_id)


def merge_manual(conn: sqlite3.Connection, work_id: int, other_id: int) -> None:
    """work_id に other_id を統合する(手動)。巻は手動の割り当てになる。"""
    with db.transaction(conn) as c:
        keep = _alive(c, work_id)
        drop = _alive(c, other_id)
        if keep == drop:
            raise Conflict("同じ作品です")
        a, b = _pair(keep, drop)
        now = db.now()
        evidence = {"manual": True}
        c.execute("""INSERT INTO work_merge_proposals (work_a, work_b, rule, confidence, evidence, status, created_at,
                     decided_at) VALUES (?, ?, 'manual', 1.0, ?, 'approved', ?, ?)""", (a, b, _dumps(evidence), now, now))
        merge_works(c, keep, drop, rule="manual", confidence=1.0, manual=True)


def assign_volumes(conn: sqlite3.Connection, volume_ids: list[int], work_id: int | None, title: str | None = None) -> int:
    """巻を別の作品へ付け替える(work_id が None なら新しい作品に分ける)。手動の割り当てになり、再計算で動かさない。

    新しい作品に分けたときは、元の作品との組を「分離済み」(却下)にして、重複候補の証拠で自動的に戻されないようにする。
    付け替えた作品の id を返す。
    """
    if not volume_ids:
        raise InvalidValue("巻を選んでください")
    with db.transaction(conn) as c:
        rows = {}
        for vid in dict.fromkeys(volume_ids):
            r = c.execute("""SELECT v.id, v.title, v.container_path, v.entry_prefix, v.inner_path, v.parent_id,
                                    s.id AS source_id, s.rel_path
                             FROM volumes v JOIN sources s ON s.id = v.source_id WHERE v.id = ?""", (vid,)).fetchone()
            if r is None:
                raise VolumeNotFound(vid)
            rows[vid] = r
        created = work_id is None
        if created:
            text = (title or "").strip()
            first = next(iter(rows.values()))
            if text:
                if not normalize_title(text):
                    raise InvalidValue("作品名を入力してください")
                target = _new_work(c, text, source=SOURCE_MANUAL, manual=True, confidence=1.0, evidence={"edited": True})
            else:
                own = own_name(first)
                name = own.name.title if own else first["title"]
                target = _new_work(c, name, source=SOURCE_FILENAME, manual=False,
                                   confidence=own.confidence if own else CONF_ENCLOSING_NAME,
                                   evidence=own.evidence() if own else {"name": first["title"]})
        else:
            target = _alive(c, work_id)
        olds: set[int] = set()
        for vid in rows:
            prev = c.execute("SELECT work_id FROM work_volumes WHERE volume_id = ?", (vid,)).fetchone()
            if prev is not None and prev["work_id"] != target:
                olds.add(prev["work_id"])
            _put_assignment(c, vid, target, SOURCE_MANUAL, True, 1.0,
                            {"moved_from": prev["work_id"] if prev else None})
        if created:
            for old in olds:
                _mark_separated(c, old, target)
        return target


def _mark_separated(c, a: int, b: int) -> None:
    """2 作品を別々のものとして記録する(却下済みの提案)。自動の統合・提案の対象から外す。"""
    roots = _roots(c)
    pair = _pair(roots.get(a, a), roots.get(b, b))
    now = db.now()
    for r in c.execute("SELECT * FROM work_merge_proposals WHERE status IN ('open', 'dismissed')").fetchall():
        if _pair(roots.get(r["work_a"], r["work_a"]), roots.get(r["work_b"], r["work_b"])) == pair:
            c.execute("UPDATE work_merge_proposals SET status = 'dismissed', decided_at = ? WHERE id = ?", (now, r["id"]))
            return
    c.execute("""INSERT INTO work_merge_proposals (work_a, work_b, rule, confidence, evidence, status, created_at,
                 decided_at) VALUES (?, ?, 'manual', 1.0, ?, 'dismissed', ?, ?)""",
              (pair[0], pair[1], _dumps({"separated": True}), now, now))


def decide_proposal(conn: sqlite3.Connection, proposal_id: int, action: str) -> dict:
    """提案を承認(統合)・却下・却下の取り消しにする。action は approve / dismiss / restore。"""
    with db.transaction(conn) as c:
        row = c.execute("SELECT * FROM work_merge_proposals WHERE id = ?", (proposal_id,)).fetchone()
        if row is None:
            raise ProposalNotFound(proposal_id)
        roots = _roots(c)
        a, b = roots.get(row["work_a"], row["work_a"]), roots.get(row["work_b"], row["work_b"])
        now = db.now()
        if action == "approve":
            if row["status"] != "open":
                raise Conflict("この提案は確認待ちではありません")
            if a == b:
                raise Conflict("すでに同じ作品です")
            keep, drop = choose_survivor(c, a, b)
            merge_works(c, keep, drop, rule=row["rule"], confidence=row["confidence"], manual=True)
            c.execute("UPDATE work_merge_proposals SET status = 'approved', decided_at = ? WHERE id = ?", (now, proposal_id))
        elif action == "dismiss":
            if row["status"] != "open":
                raise Conflict("この提案は確認待ちではありません。統合済みの作品は、巻を別の作品へ移して分けてください")
            c.execute("UPDATE work_merge_proposals SET status = 'dismissed', decided_at = ? WHERE id = ?", (now, proposal_id))
        elif action == "restore":
            if row["status"] != "dismissed":
                raise Conflict("この提案は却下されていません")
            c.execute("UPDATE work_merge_proposals SET status = 'open', decided_at = NULL WHERE id = ?", (proposal_id,))
        else:
            raise InvalidValue(f"未対応の操作: {action}")
        return {"id": proposal_id, "status": c.execute("SELECT status FROM work_merge_proposals WHERE id = ?",
                                                      (proposal_id,)).fetchone()["status"]}


# --- 参照(API 用) ----------------------------------------------------------------------------

_VOLUME_SELECT = """SELECT v.id, v.title, v.page_count, v.number_kind, v.number_start, v.number_end, v.number_evidence,
       pv.title AS parent_title, s.rel_path, s.offline AS source_offline, r.online AS root_online, p.page, p.finished,
       wv.work_id, wv.source AS a_source, wv.manual AS a_manual, wv.confidence AS a_confidence,
       wv.evidence AS a_evidence"""
_VOLUME_FROM = (LISTED_VOLUMES_JOIN + " JOIN work_volumes wv ON wv.volume_id = v.id"
                " LEFT JOIN reading_progress p ON p.volume_id = v.id ")


def _volume_json(r, dup_counts: dict[int, int], meta: dict[int, dict] | None = None) -> dict:
    number = None
    if r["number_kind"] is not None:
        number = {"kind": r["number_kind"], "start": r["number_start"], "end": r["number_end"],
                  "evidence": r["number_evidence"]}
    return {
        "id": r["id"], "title": r["title"], "parent_title": r["parent_title"], "page_count": r["page_count"],
        "offline": is_offline(r),
        "progress": None if r["page"] is None else {"page": r["page"], "finished": bool(r["finished"])},
        "volume_number": number, "duplicate_count": dup_counts.get(r["id"], 0),
        "assignment": {"source": r["a_source"], "manual": bool(r["a_manual"]), "confidence": r["a_confidence"],
                       "evidence": json.loads(r["a_evidence"]) if r["a_evidence"] else None},
        **({"metadata": meta.get(r["id"], {})} if meta is not None else {}),
    }


def number_summary(vols: list[dict]) -> dict:
    """巻・話ごとの最大番号と、1 から最大までで見つからない番号(欠け)。"""
    out: dict[str, dict] = {}
    for kind in ("volume", "episode"):
        ranges = [(v["volume_number"]["start"], v["volume_number"]["end"]) for v in vols
                  if v["volume_number"] and v["volume_number"]["kind"] == kind]
        if not ranges:
            out[kind] = {"max": None, "missing": [], "limited": False}
            continue
        top = max(e for _, e in ranges)
        if top > MAX_MISSING_SPAN:
            out[kind] = {"max": top, "missing": [], "limited": True}
            continue
        covered: set[int] = set()
        for s, e in ranges:
            covered.update(range(s, e + 1))
        out[kind] = {"max": top, "missing": [n for n in range(1, top + 1) if n not in covered], "limited": False}
    return out


def build_slots(vols: list[dict]) -> tuple[list[dict], list[dict]]:
    """巻を番号ごとにまとめる(同じ番号の巻は重複としてまとめて表示)。番号の無い巻は別に返す。"""
    groups: dict[tuple, list[dict]] = defaultdict(list)
    unnumbered = []
    for v in vols:
        n = v["volume_number"]
        if n is None:
            unnumbered.append(v)
        else:
            groups[(0 if n["kind"] == "volume" else 1, n["start"], n["end"])].append(v)
    slots = []
    for (k, start, end), items in sorted(groups.items()):
        items.sort(key=lambda v: natural_key(v["title"]))
        unit = "巻" if k == 0 else "話"
        label = f"{start}{unit}" if start == end else f"{start}〜{end}{unit}"
        slots.append({"kind": "volume" if k == 0 else "episode", "start": start, "end": end, "label": label,
                      "duplicate": len(items) > 1, "volumes": items})
    unnumbered.sort(key=lambda v: natural_key(v["title"]))
    return slots, unnumbered


def _work_rows(c) -> dict[int, dict]:
    """生きている作品の項目・別名。"""
    works: dict[int, dict] = {}
    for r in c.execute("SELECT id FROM works WHERE merged_into IS NULL"):
        works[r["id"]] = {"id": r["id"], "title": "", "title_source": SOURCE_FILENAME, "title_manual": False,
                          "title_confidence": None, "title_evidence": None, "author": None,
                          "author_source": None, "author_manual": False, "author_confidence": None,
                          "aliases": [], "fields": {}}
    for r in c.execute("SELECT * FROM work_fields"):
        w = works.get(r["work_id"])
        if w is None:
            continue
        ev = json.loads(r["evidence"]) if r["evidence"] else {}
        w["fields"][r["field"]] = {"value": decode_value(r["field"], r["value"]), "source": r["source"],
                                   "manual": bool(r["manual"]), "confidence": r["confidence"],
                                   "sources": (ev.get("link") or {}).get("sources") or []}
        key = "title" if r["field"] == FIELD_TITLE else "author" if r["field"] == FIELD_AUTHOR else None
        if key:
            w[key] = r["value"] if key == "author" else (r["value"] or "")
            w[f"{key}_source"], w[f"{key}_manual"] = r["source"], bool(r["manual"])
            w[f"{key}_confidence"] = r["confidence"]
            if key == "title":
                w["title_evidence"] = json.loads(r["evidence"]) if r["evidence"] else None
    for r in c.execute("SELECT * FROM work_aliases WHERE dismissed_at IS NULL ORDER BY id"):
        w = works.get(r["work_id"])
        if w is not None:
            w["aliases"].append({"id": r["id"], "alias": r["alias"], "source": r["source"], "manual": bool(r["manual"]),
                                 "confidence": r["confidence"],
                                 "evidence": json.loads(r["evidence"]) if r["evidence"] else None})
    return works


def _volume_meta(c) -> dict[int, dict]:
    meta: dict[int, dict] = defaultdict(dict)
    for r in c.execute("SELECT * FROM volume_fields"):
        ev = json.loads(r["evidence"]) if r["evidence"] else {}
        meta[r["volume_id"]][r["field"]] = {"value": r["value"], "source": r["source"], "manual": bool(r["manual"]),
                                            "confidence": r["confidence"], "from": ev.get("source"), "url": ev.get("url")}
    return meta


def _listed_volumes(c, with_meta: bool = False) -> dict[int, list[dict]]:
    dups = candidate_counts(c)
    meta = _volume_meta(c) if with_meta else None
    by_work: dict[int, list[dict]] = defaultdict(list)
    for r in c.execute(_VOLUME_SELECT + _VOLUME_FROM + LISTED_VOLUMES_WHERE):
        by_work[r["work_id"]].append(_volume_json(r, dups, meta))
    return by_work


def _open_proposal_counts(c) -> dict[int, int]:
    roots = _roots(c)
    counts: dict[int, int] = defaultdict(int)
    for r in c.execute("SELECT work_a, work_b FROM work_merge_proposals WHERE status = 'open'"):
        a, b = roots.get(r["work_a"], r["work_a"]), roots.get(r["work_b"], r["work_b"])
        if a != b:
            counts[a] += 1
            counts[b] += 1
    return dict(counts)


def _summary(w: dict, vols: list[dict], open_count: int, link_pending: int = 0) -> dict:
    nums = number_summary(vols)
    slots, _ = build_slots(vols)
    return {
        **{k: w[k] for k in ("id", "title", "title_source", "title_manual", "author", "author_source", "author_manual")},
        "aliases": [{k: a[k] for k in ("id", "alias", "source", "manual", "confidence")} for a in w["aliases"]],
        "volume_count": len(vols),
        "offline_count": sum(1 for v in vols if v["offline"]),
        "finished_count": sum(1 for v in vols if v["progress"] and v["progress"]["finished"]),
        "duplicate_slots": sum(1 for s in slots if s["duplicate"]),
        "max_volume": nums["volume"]["max"],
        "missing_volumes": nums["volume"]["missing"],
        "missing_episodes": nums["episode"]["missing"],
        "pending_proposals": open_count,
        "merged": False,
        "kind": (w["fields"].get(FIELD_KIND) or {}).get("value"),
        "publisher": (w["fields"].get(FIELD_PUBLISHER) or {}).get("value"),
        "genres": (w["fields"].get(FIELD_GENRES) or {}).get("value") or [],
        "tags": (w["fields"].get(FIELD_TAGS) or {}).get("value") or [],
        "link_pending": link_pending,
    }


def list_works(conn: sqlite3.Connection) -> dict:
    """見えている巻を持つ作品の一覧と、作品に割り当てられていない巻の数。"""
    works = _work_rows(conn)
    vols = _listed_volumes(conn)
    opens = _open_proposal_counts(conn)
    pending_links = {r["work_id"]: r["n"] for r in conn.execute(
        "SELECT work_id, COUNT(*) AS n FROM work_links WHERE status = 'pending' GROUP BY work_id")}
    roots = _roots(conn)
    merged_roots = set()
    for r in conn.execute("SELECT work_a, work_b FROM work_merge_proposals WHERE status IN ('auto', 'approved')"):
        a, b = roots.get(r["work_a"], r["work_a"]), roots.get(r["work_b"], r["work_b"])
        if a == b:
            merged_roots.add(a)
    out = []
    for wid, w in works.items():
        if wid not in vols:
            continue
        s = _summary(w, vols[wid], opens.get(wid, 0), pending_links.get(wid, 0))
        s["merged"] = wid in merged_roots
        out.append(s)
    out.sort(key=lambda s: natural_key(s["title"]))
    total = conn.execute("SELECT COUNT(*) " + LISTED_VOLUMES_JOIN + LISTED_VOLUMES_WHERE).fetchone()[0]
    assigned = sum(len(v) for v in vols.values())
    return {"works": out, "unassigned": total - assigned}


def _brief(w: dict, vols: list[dict]) -> dict:
    ordered = sorted(vols, key=lambda v: ((v["volume_number"] or {}).get("start", 10 ** 9), natural_key(v["title"])))
    return {
        **{k: w[k] for k in ("id", "title", "author")},
        "aliases": [a["alias"] for a in w["aliases"]],
        "volume_count": len(vols),
        "volumes": [{"id": v["id"], "title": v["title"], "volume_number": v["volume_number"]}
                    for v in ordered[:SAMPLE_VOLUMES]],
    }


def _proposal_json(r, works: dict[int, dict], vols: dict[int, list[dict]], roots: dict[int, int]) -> dict | None:
    a, b = roots.get(r["work_a"], r["work_a"]), roots.get(r["work_b"], r["work_b"])
    wa, wb = works.get(a), works.get(b)
    if wa is None or wb is None:
        return None
    return {"id": r["id"], "rule": r["rule"], "confidence": r["confidence"], "status": r["status"],
            "evidence": json.loads(r["evidence"]), "decided_at": r["decided_at"],
            "a": _brief(wa, vols.get(a, [])), "b": _brief(wb, vols.get(b, []))}


def _merge_json(conn, r, roots: dict[int, int]) -> dict:
    """統合済み(auto / approved)の提案。どの作品がどの作品に統合されたかを、作品名で示す。"""
    return {"id": r["id"], "rule": r["rule"], "confidence": r["confidence"], "status": r["status"],
            "evidence": json.loads(r["evidence"]), "decided_at": r["decided_at"],
            "work_id": roots.get(r["work_a"], r["work_a"]),
            "titles": [_title_of(conn, r["work_a"])[0], _title_of(conn, r["work_b"])[0]]}


def list_proposals(conn: sqlite3.Connection, status: str = "open") -> list[dict]:
    """統合の提案。status は open(確認待ち)/ dismissed(却下・分離)/ applied(統合済み: 自動と承認)。"""
    wanted = {"open": ("open",), "dismissed": ("dismissed",), "applied": ("auto", "approved")}.get(status)
    if wanted is None:
        raise InvalidValue("status は open / dismissed / applied")
    works = _work_rows(conn)
    vols = _listed_volumes(conn)
    roots = _roots(conn)
    marks = ",".join("?" * len(wanted))
    out = []
    for r in conn.execute(f"SELECT * FROM work_merge_proposals WHERE status IN ({marks}) ORDER BY confidence DESC, id",
                          wanted):
        a, b = roots.get(r["work_a"], r["work_a"]), roots.get(r["work_b"], r["work_b"])
        if status == "applied":
            if a == b and a in vols:
                out.append(_merge_json(conn, r, roots))
        elif a != b and a in vols and b in vols:
            item = _proposal_json(r, works, vols, roots)
            if item is not None:
                out.append(item)
    return out


def _link_json(row) -> dict:
    ev = json.loads(row["evidence"]) if row["evidence"] else {}
    candidates = json.loads(row["candidates"]) if row["candidates"] else []
    return {
        "source": row["source"], "label": SOURCE_LABELS.get(row["source"], row["source"]), "status": row["status"],
        "manual": bool(row["manual"]), "confidence": row["confidence"], "source_id": row["source_id"],
        "attempted_at": row["attempted_at"], "checked_at": row["checked_at"],
        "failure": json.loads(row["failure"]) if row["failure"] else None,
        "linked": ev.get("summary") if row["status"] == "linked" else None,
        "decision": ev.get("decision"), "margin": ev.get("margin"), "hold": ev.get("hold"),
        "responses": ev.get("responses", []),
        "candidates": [c["summary"] for c in candidates],
    }


def links_json(conn: sqlite3.Connection, work_id: int, enabled: tuple[str, ...] = ()) -> list[dict]:
    """作品の公開 DB との連携(取得元ごと)。取得元が有効なのにまだ取得していなければ status = None で出す。"""
    rows = {r["source"]: r for r in conn.execute("SELECT * FROM work_links WHERE work_id = ?", (work_id,))}
    names = [n for n in SOURCE_LABELS if n in enabled or n in rows] + [n for n in rows if n not in SOURCE_LABELS]
    out = []
    for name in names:
        if name in rows:
            out.append(_link_json(rows[name]))
        else:
            out.append({"source": name, "label": SOURCE_LABELS.get(name, name), "status": None, "manual": False,
                        "confidence": None, "source_id": None, "attempted_at": None, "checked_at": None,
                        "failure": None, "linked": None, "decision": None, "margin": None, "hold": None,
                        "responses": [],
                        "candidates": []})
    return out


def get_work(conn: sqlite3.Connection, work_id: int, sources: tuple[str, ...] = ()) -> dict:
    works = _work_rows(conn)
    if work_id not in works:
        raise WorkNotFound(work_id)
    w = works[work_id]
    all_vols = _listed_volumes(conn, with_meta=True)
    vols = all_vols.get(work_id, [])
    slots, unnumbered = build_slots(vols)
    roots = _roots(conn)
    proposals, merges = [], []
    for r in conn.execute("SELECT * FROM work_merge_proposals WHERE status IN ('open', 'auto', 'approved') "
                          "ORDER BY confidence DESC, id"):
        a, b = roots.get(r["work_a"], r["work_a"]), roots.get(r["work_b"], r["work_b"])
        if r["status"] == "open":
            if a != b and work_id in (a, b):
                item = _proposal_json(r, works, all_vols, roots)
                if item:
                    proposals.append(item)
        elif a == b == work_id:
            merges.append(_merge_json(conn, r, roots))
    links = links_json(conn, work_id, sources)
    return {
        **_summary(w, vols, len(proposals), sum(1 for x in links if x["status"] == "pending")),
        "title_confidence": w["title_confidence"], "title_evidence": w["title_evidence"],
        "author_confidence": w["author_confidence"],
        "fields": w["fields"], "links": links,
        "aliases": w["aliases"],
        "slots": slots, "unnumbered": unnumbered,
        "numbers": number_summary(vols),
        "proposals": proposals, "merges": merges, "merged": bool(merges),
    }


def assignments(conn: sqlite3.Connection) -> dict[int, int]:
    """巻 id → 作品 id(一覧の巻に作品へのリンクを付けるため)。"""
    return {r["volume_id"]: r["work_id"] for r in conn.execute("SELECT volume_id, work_id FROM work_volumes")}
