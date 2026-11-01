"""作品の全文検索と絞り込み(#56。GOAL.md「できるようになること」3)。

- 索引 `work_search` は作品 1 件につき 1 行(rowid = 作品 id)。本文は作品名・別名(外したものを除く)・著者・
  その他の項目(公開 DB の結線で足される種別・ジャンル・タグ・出版社・あらすじなど。`work_fields` の行を全部)・
  割り当てた巻の名前を、それぞれ `normalize_title` で正規化して改行でつないだもの。正規化は作品の名寄せと同じ
  (NFKC・大小文字・カタカナ → ひらがな・空白や記号を落とす)なので、検索語も同じ関数を通してから引く
- 索引は派生データ。実行時の SQLite が FTS5 の trigram を持てば FTS5(`tokenize='trigram'`)、持たなければ
  普通の表にして LIKE で引く(結果は同じで、遅いだけ)。どちらの形かは表の定義で見分ける。`rebuild` は作り直す
- 更新: 作品・項目・別名・巻の割り当て・巻の名前が変わると、トリガー(spinebox/db.py の版 7)が
  `work_search_dirty` に作品 id を入れる。検索・一覧の前に `prepare` がその作品だけ索引し直す
- 2 文字以下の語: trigram は 3 文字未満を索引から引けないので、その語だけ LIKE(全行を見る)にする。
  単一利用者の蔵書(作品が数千件程度)なら十分速い
"""
from __future__ import annotations

import re
import sqlite3
from collections import defaultdict

from . import db
from .naming import normalize_title

INDEX_TABLE = "work_search"

# 絞り込みに使う項目(work_fields.field)。値は区切り文字で分けた 1 つずつと、正規化して一致で比べる。
# kind / genre / tag は公開 DB の結線(#50)が足す項目の差し込み口で、行が無ければ絞り込みの候補も出ない
FILTER_FIELDS = ("author", "kind", "genre", "tag")
_VALUE_SEPARATORS = re.compile(r"[、,，;；]")

READING_STATES = ("unread", "reading", "finished")

_MIN_TRIGRAM = 3  # trigram で索引から引ける語の最短


class InvalidFilter(ValueError):
    """絞り込みの値が使えない。メッセージは利用者向け。"""


# --- 索引の形 --------------------------------------------------------------------------------


def _index_sql(c) -> str | None:
    row = c.execute("SELECT sql FROM sqlite_master WHERE name = ?", (INDEX_TABLE,)).fetchone()
    return None if row is None else row[0]


def index_exists(c) -> bool:
    return _index_sql(c) is not None


def uses_fts(c) -> bool:
    sql = _index_sql(c)
    return sql is not None and "fts5" in sql.lower()


def trigram_available(c) -> bool:
    """この SQLite が FTS5 の trigram トークナイザを持つか(SQLite 3.34 以上で FTS5 有効のビルド)。"""
    try:
        c.execute("CREATE VIRTUAL TABLE temp._spinebox_trigram_probe USING fts5(x, tokenize='trigram')")
    except sqlite3.OperationalError:
        return False
    c.execute("DROP TABLE temp._spinebox_trigram_probe")
    return True


def _create_index(c) -> None:
    if trigram_available(c):
        c.execute(f"CREATE VIRTUAL TABLE {INDEX_TABLE} USING fts5(body, tokenize='trigram')")
    else:
        c.execute(f"CREATE TABLE {INDEX_TABLE} (rowid INTEGER PRIMARY KEY, body TEXT NOT NULL)")


# --- 索引の中身 ------------------------------------------------------------------------------


def normalize(text: str | None) -> str:
    """検索用の正規化。作品の名寄せの突き合わせと同じ形(spinebox/naming.py normalize_title)。"""
    return normalize_title(text or "")


def _segments(c, work_id: int) -> list[str]:
    texts: list[str] = []
    texts += [r[0] for r in c.execute("SELECT value FROM work_fields WHERE work_id = ? AND value IS NOT NULL "
                                      "ORDER BY field = 'title' DESC, field", (work_id,))]
    texts += [r[0] for r in c.execute("SELECT alias FROM work_aliases WHERE work_id = ? AND dismissed_at IS NULL "
                                      "ORDER BY id", (work_id,))]
    texts += [r[0] for r in c.execute("SELECT v.title FROM work_volumes wv JOIN volumes v ON v.id = wv.volume_id "
                                      "WHERE wv.work_id = ? ORDER BY v.id", (work_id,))]
    out: list[str] = []
    for t in texts:
        n = normalize(t)
        if n and n not in out:
            out.append(n)
    return out


def _index_work(c, work_id: int) -> None:
    c.execute(f"DELETE FROM {INDEX_TABLE} WHERE rowid = ?", (work_id,))
    row = c.execute("SELECT merged_into FROM works WHERE id = ?", (work_id,)).fetchone()
    if row is None or row[0] is not None:
        return  # 消えた・統合された作品は索引に置かない(統合先の名前・別名は統合先の行にある)
    body = "\n".join(_segments(c, work_id))
    if body:
        c.execute(f"INSERT INTO {INDEX_TABLE} (rowid, body) VALUES (?, ?)", (work_id, body))


def rebuild(conn: sqlite3.Connection) -> int:
    """索引を作り直す(形も今の SQLite に合わせて選び直す)。索引した作品の数を返す。"""
    with db.transaction(conn) as c:
        c.execute(f"DROP TABLE IF EXISTS {INDEX_TABLE}")
        _create_index(c)
        c.execute("DELETE FROM work_search_dirty")
        for r in c.execute("SELECT id FROM works WHERE merged_into IS NULL").fetchall():
            _index_work(c, r[0])
        return c.execute(f"SELECT COUNT(*) FROM {INDEX_TABLE}").fetchone()[0]


def prepare(conn: sqlite3.Connection) -> None:
    """索引を使える状態にする。無ければ作り、古くなった作品(印のついたもの)だけ索引し直す。"""
    if _index_sql(conn) is None:
        rebuild(conn)
        return
    if conn.execute("SELECT 1 FROM work_search_dirty LIMIT 1").fetchone() is None:
        return  # 読むだけで済む(書き込みのトランザクションを取らない)
    with db.transaction(conn) as c:
        for r in c.execute("SELECT work_id FROM work_search_dirty").fetchall():
            _index_work(c, r[0])
        c.execute("DELETE FROM work_search_dirty")


# --- 引く ------------------------------------------------------------------------------------


def terms(q: str | None) -> list[str]:
    """検索語を空白で分けて正規化する(空になった語は捨てる)。語どうしは AND。"""
    out: list[str] = []
    for t in (q or "").split():
        n = normalize(t)
        if n and n not in out:
            out.append(n)
    return out


def match_ids(conn: sqlite3.Connection, q: str | None) -> set[int] | None:
    """検索語に当たる作品 id。検索語が無ければ None(絞らない)。`prepare` の後に呼ぶ。"""
    ts = terms(q)
    if not ts:
        return None
    fts = uses_fts(conn)
    long_terms = [t for t in ts if fts and len(t) >= _MIN_TRIGRAM]
    short_terms = [t for t in ts if t not in long_terms]
    where, args = [], []
    if long_terms:
        # 正規化後の語は英数字・かな・漢字だけなので、二重引用符で囲めば FTS5 の演算子として読まれない
        where.append(f"{INDEX_TABLE} MATCH ?")
        args.append(" AND ".join(f'"{t}"' for t in long_terms))
    for t in short_terms:
        where.append("body LIKE ?")  # 正規化で % と _ は残らないのでエスケープは要らない
        args.append(f"%{t}%")
    sql = f"SELECT rowid FROM {INDEX_TABLE} WHERE " + " AND ".join(where)
    return {r[0] for r in conn.execute(sql, args)}


# --- 絞り込み --------------------------------------------------------------------------------


def split_values(value: str | None) -> list[str]:
    return [v.strip() for v in _VALUE_SEPARATORS.split(value or "") if v.strip()]


def field_values(conn: sqlite3.Connection) -> dict[int, dict[str, list[str]]]:
    """作品 id → 絞り込みに使う項目 → 値(区切りで分けたもの)。"""
    marks = ",".join("?" * len(FILTER_FIELDS))
    out: dict[int, dict[str, list[str]]] = defaultdict(dict)
    for r in conn.execute(f"SELECT work_id, field, value FROM work_fields WHERE field IN ({marks})", FILTER_FIELDS):
        vals = split_values(r["value"])
        if vals:
            out[r["work_id"]][r["field"]] = vals
    return out


def reading_state(volume_count: int, finished_count: int, started_count: int) -> str:
    """作品の読書状態。全巻読み終えたら finished、1 巻も開いていなければ unread、それ以外は reading。"""
    if volume_count and finished_count >= volume_count:
        return "finished"
    if started_count == 0:
        return "unread"
    return "reading"


def check_filters(filters: dict[str, str | None]) -> dict[str, str]:
    """空の値を落とし、使えない値を弾く。"""
    out = {k: v.strip() for k, v in filters.items() if v is not None and v.strip()}
    unknown = set(out) - set(FILTER_FIELDS) - {"status"}
    if unknown:
        raise InvalidFilter(f"未対応の絞り込み: {', '.join(sorted(unknown))}")
    if "status" in out and out["status"] not in READING_STATES:
        raise InvalidFilter("status は unread / reading / finished")
    return out


def matches_filters(values: dict[str, list[str]], state: str, filters: dict[str, str]) -> bool:
    for name, wanted in filters.items():
        if name == "status":
            if state != wanted:
                return False
            continue
        target = normalize(wanted)
        if not any(normalize(v) == target for v in values.get(name, [])):
            return False
    return True


def facets(values_by_work: dict[int, dict[str, list[str]]], work_ids) -> dict[str, list[str]]:
    """絞り込みの候補(一覧に出る作品の値。正規化して同じものは最初の表記にまとめる)。"""
    out: dict[str, list[str]] = {}
    for name in FILTER_FIELDS:
        seen: dict[str, str] = {}
        for wid in work_ids:
            for v in values_by_work.get(wid, {}).get(name, []):
                seen.setdefault(normalize(v), v)
        out[name] = sorted(seen.values())
    return out
