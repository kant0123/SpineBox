"""別スキャンの重複候補の検出(GOAL.md フェーズ 2)。

- 指紋: 巻の全ページ(上限あり)の見た目のハッシュ(spinebox/fingerprint.py)を `volume_fingerprints` に持つ。
  元ファイルが変わらない限り作り直さない。**計算は明示操作(`DuplicateScanner.run`)だけ**で、スキャンでは行わない
  (内側アーカイブの取り出しが要るため)。内側アーカイブの巻は開いて中の巻に分解し、分解後の巻(子)を対象にする
- 候補: 巻番号の候補が同じで、見た目の近いページが十分ある 2 巻。巻番号が片方でも不明なら、見た目だけで強い一致を要求する。
  根拠(一致したページ数・巻番号の根拠)を `duplicate_candidates` に保存する。**自動では消さない・動かさない**
- 却下: `dismissed_at` を立てる。再計算しても復活せず、戻す(restore)こともできる
"""
from __future__ import annotations

import json
import logging
import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

from . import db
from .cache import CacheTooLarge
from .fingerprint import FINGERPRINT_VERSION, PageHash, fingerprint_pages, pack, unpack
from .library import Library, VolumeNotFound, VolumeOffline, VolumeUnreadable
from .scanner import LISTED_VOLUMES_JOIN, LISTED_VOLUMES_WHERE, resolve_source_path

log = logging.getLogger(__name__)

# 2 ページを「見た目が近い」とみなすハミング距離の上限(64 ビット中)。
# 実データ(同じ本の別スキャン 2 組、約 190 ページ)では 8 ビット以内に 98% が入り、別の本では 5% 未満
NEAR_BITS = 8

# 判定の基準。比 = 一致ページ数 / 小さい方の巻の指紋ページ数
SAME_NUMBER_MIN_PAGES = 5
SAME_NUMBER_MIN_RATIO = 0.5
VISUAL_ONLY_MIN_PAGES = 20
VISUAL_ONLY_MIN_RATIO = 0.8

# 総当たりの手前で、小さい方の巻の等間隔サンプルがこの割合も近いページを持たなければ捨てる
PREFILTER_SAMPLES = 24
PREFILTER_MIN_RATIO = 0.2
PREFILTER_FIRST_DIVISOR = 3  # 最初に見るのはサンプルの 1/3 を等間隔に

SAMPLE_PAIRS = 3  # 根拠に残す一致ページの例の数

RULE_SAME_NUMBER = "same_number"
RULE_VISUAL_ONLY = "visual_only"


# --- 近いページの探索 -------------------------------------------------------------


class HashIndex:
    """ハミング距離 NEAR_BITS 以内の探索を速くする索引(鳩の巣原理)。

    64 ビットを NEAR_BITS + 1 個に分けると、距離が NEAR_BITS 以内なら少なくとも 1 個の断片が完全に一致する。
    断片が一致したものだけ距離を測る。
    """

    _CHUNKS = NEAR_BITS + 1
    _WIDTH = 64 // _CHUNKS  # 7

    def __init__(self, hashes: list[PageHash]):
        self.hashes = hashes
        self._buckets: list[dict[int, list[int]]] = [defaultdict(list) for _ in range(self._CHUNKS)]
        for pos, (_, h) in enumerate(hashes):
            for i in range(self._CHUNKS):
                self._buckets[i][self._chunk(h, i)].append(pos)

    @classmethod
    def _chunk(cls, h: int, i: int) -> int:
        shift = i * cls._WIDTH
        # 最後の断片は余りのビットも含める
        return h >> shift if i == cls._CHUNKS - 1 else (h >> shift) & ((1 << cls._WIDTH) - 1)

    def near(self, h: int) -> list[tuple[int, int]]:
        """h に近い (自分の中の位置, 距離) の列。"""
        found: dict[int, int] = {}
        for i in range(self._CHUNKS):
            for pos in self._buckets[i].get(self._chunk(h, i), ()):
                if pos not in found:
                    d = (h ^ self.hashes[pos][1]).bit_count()
                    if d <= NEAR_BITS:
                        found[pos] = d
        return sorted(found.items())


@dataclass
class Match:
    matched_a: int  # b に近いページを持つ a のページ数
    matched_b: int
    samples: list[dict] = field(default_factory=list)

    @property
    def matched(self) -> int:
        return min(self.matched_a, self.matched_b)


def match_pages(a: list[PageHash], b: HashIndex) -> Match:
    """a の各ページに近い b のページを探し、重複を除いて数える(同じ図柄が何度も出ても 1 回)。"""
    pages_a: set[int] = set()
    pages_b: set[int] = set()
    first: list[tuple[int, int, int]] = []
    for page_a, h in a:
        hits = b.near(h)
        if not hits:
            continue
        pages_a.add(page_a)
        for pos, _ in hits:
            pages_b.add(b.hashes[pos][0])
        pos, d = min(hits, key=lambda t: t[1])
        first.append((page_a, b.hashes[pos][0], d))
    samples = [{"a_page": pa, "b_page": pb, "distance": d} for pa, pb, d in first[:SAMPLE_PAIRS]]
    return Match(len(pages_a), len(pages_b), samples)


def prefilter(small: list[PageHash], large: HashIndex) -> bool:
    """小さい方の等間隔サンプルが、大きい方に近いページをある程度持つか。持たなければ総当たりしない。

    2 段で絞る(無関係な組の大半は最初の数枚で捨てられる)。最初の PREFILTER_FIRST 枚に 1 枚も無ければ捨て、
    残りを含む PREFILTER_SAMPLES 枚で PREFILTER_MIN_RATIO 以上に近いページがあるかを見る。
    """
    if len(small) <= PREFILTER_SAMPLES:
        sample = small
    else:
        step = (len(small) - 1) / (PREFILTER_SAMPLES - 1)
        sample = [small[round(i * step)] for i in range(PREFILTER_SAMPLES)]
    first = max(1, len(sample) // PREFILTER_FIRST_DIVISOR)
    stride = max(1, len(sample) // first)
    head = sample[::stride][:first]
    if not any(large.near(h) for _, h in head):
        return False
    hit = sum(1 for _, h in sample if large.near(h))
    return hit >= max(1, PREFILTER_MIN_RATIO * len(sample))


# --- 候補の判定 -------------------------------------------------------------------


@dataclass
class Fingerprinted:
    id: int
    title: str
    hashes: list[PageHash]
    number: tuple[str, int, int] | None  # (kind, start, end)
    number_evidence: str | None
    index: HashIndex | None = None

    def get_index(self) -> HashIndex:
        if self.index is None:
            self.index = HashIndex(self.hashes)
        return self.index


def judge(a: Fingerprinted, b: Fingerprinted, rule: str) -> dict | None:
    """2 巻が重複候補なら、保存する根拠を返す。そうでなければ None。"""
    small, large = (a, b) if len(a.hashes) <= len(b.hashes) else (b, a)
    if not small.hashes:
        return None
    if not prefilter(small.hashes, large.get_index()):
        return None
    m = match_pages(a.hashes, b.get_index())
    compared = min(len(a.hashes), len(b.hashes))
    ratio = m.matched / compared
    if rule == RULE_SAME_NUMBER:
        ok = m.matched >= SAME_NUMBER_MIN_PAGES and ratio >= SAME_NUMBER_MIN_RATIO
    else:
        ok = m.matched >= VISUAL_ONLY_MIN_PAGES and ratio >= VISUAL_ONLY_MIN_RATIO
    if not ok:
        return None
    return {
        "rule": rule,
        "matched_pages": m.matched,
        "compared_pages": compared,
        "pages_a": len(a.hashes),
        "pages_b": len(b.hashes),
        "matched_a": m.matched_a,
        "matched_b": m.matched_b,
        "near_bits": NEAR_BITS,
        "number_a": a.number_evidence,
        "number_b": b.number_evidence,
        "samples": m.samples,
    }


def find_pairs(items: list[Fingerprinted]) -> Iterable[tuple[Fingerprinted, Fingerprinted, dict]]:
    """見えている巻の指紋から重複候補の組を探す。a.id < b.id で返す。

    - 巻番号が同じ(種類・開始・終了が一致): 同じ番号の巻どうしだけ比べ、緩めの基準
    - 番号の種類が同じで数が違う: 比べない(別の巻)
    - どちらかの番号が不明: 他のすべての巻と比べ、見た目だけで強い一致を要求する
    """
    known: dict[tuple, list[Fingerprinted]] = defaultdict(list)
    unknown: list[Fingerprinted] = []
    for it in items:
        if it.number is None:
            unknown.append(it)
        else:
            known[it.number].append(it)
    seen: set[tuple[int, int]] = set()

    def emit(x: Fingerprinted, y: Fingerprinted, rule: str):
        a, b = (x, y) if x.id < y.id else (y, x)
        if a.id == b.id or (a.id, b.id) in seen:
            return None
        seen.add((a.id, b.id))
        ev = judge(a, b, rule)
        return (a, b, ev) if ev else None

    for group in known.values():
        for i, x in enumerate(group):
            for y in group[i + 1:]:
                r = emit(x, y, RULE_SAME_NUMBER)
                if r:
                    yield r
    for u in unknown:
        for other in items:
            if other.id != u.id:
                r = emit(u, other, RULE_VISUAL_ONLY)
                if r:
                    yield r


def _number_tuple(row) -> tuple[str, int, int] | None:
    if row["number_kind"] is None:
        return None
    return (row["number_kind"], row["number_start"], row["number_end"])


def load_fingerprinted(conn: sqlite3.Connection) -> list[Fingerprinted]:
    """一覧に見えている巻のうち、指紋のあるもの。"""
    rows = conn.execute(
        "SELECT v.id, v.title, v.number_kind, v.number_start, v.number_end, v.number_evidence, f.hashes "
        + LISTED_VOLUMES_JOIN + " JOIN volume_fingerprints f ON f.volume_id = v.id " + LISTED_VOLUMES_WHERE
        + " AND f.error IS NULL").fetchall()
    return [Fingerprinted(r["id"], r["title"], unpack(r["hashes"]), _number_tuple(r), r["number_evidence"])
            for r in rows if r["hashes"]]


def refresh_candidates(conn: sqlite3.Connection) -> int:
    """保存済みの指紋から候補を計算し直して保存する。却下されていない候補の数を返す。

    却下済みの組は触らない(再計算で復活させない)。もう成り立たない未却下の候補は消す
    (候補は指紋から再導出できる記録。蔵書・読書位置とは違う)。
    """
    found = {(a.id, b.id): ev for a, b, ev in find_pairs(load_fingerprinted(conn))}
    now = db.now()
    with db.transaction(conn) as c:
        existing = {(r["volume_a"], r["volume_b"]): r["dismissed_at"] for r in c.execute(
            "SELECT volume_a, volume_b, dismissed_at FROM duplicate_candidates")}
        for (a, b), ev in found.items():
            payload = json.dumps(ev, ensure_ascii=False)
            if (a, b) in existing:
                c.execute("""UPDATE duplicate_candidates SET rule = ?, matched_pages = ?, compared_pages = ?,
                             evidence = ?, computed_at = ? WHERE volume_a = ? AND volume_b = ?""",
                          (ev["rule"], ev["matched_pages"], ev["compared_pages"], payload, now, a, b))
            else:
                c.execute("""INSERT INTO duplicate_candidates
                             (volume_a, volume_b, rule, matched_pages, compared_pages, evidence, computed_at)
                             VALUES (?, ?, ?, ?, ?, ?, ?)""",
                          (a, b, ev["rule"], ev["matched_pages"], ev["compared_pages"], payload, now))
        for (a, b), dismissed in existing.items():
            if (a, b) not in found and dismissed is None:
                c.execute("DELETE FROM duplicate_candidates WHERE volume_a = ? AND volume_b = ?", (a, b))
    return sum(1 for pair in found if existing.get(pair) is None)


# --- 指紋の計算(明示操作) -----------------------------------------------------------


def fingerprint_version(source_version: str, max_depth: int) -> str:
    """指紋の版。元ファイル・指紋の作り方・入れ子の深さの上限(見えるページが変わる)のどれかが変われば変わる。"""
    return f"{FINGERPRINT_VERSION}:{max_depth}:{source_version}"


@dataclass
class ScanSummary:
    fingerprinted: int = 0
    errors: int = 0
    candidates: int = 0


class DuplicateScanner:
    """未計算(または元ファイルが変わった)の巻の指紋を作り、候補を計算し直す。

    蔵書には読み取りしかしない。オフラインの巻は飛ばし、既存の指紋・候補は消さない。
    """

    def __init__(self, conn: sqlite3.Connection, library: Library,
                 progress: Callable[[int, int], None] | None = None):
        self.conn = conn
        self.library = library
        self.progress = progress

    def pending(self, done: set[int]) -> list[int]:
        """指紋を作る巻の id。見えている巻(分解済みの親を除く)で、指紋が無い・版が古い・前回失敗したもの。"""
        rows = self.conn.execute(
            """SELECT v.id, s.rel_path, r.path AS root_path, f.version AS fp_version, f.error AS fp_error
               """ + LISTED_VOLUMES_JOIN + " LEFT JOIN volume_fingerprints f ON f.volume_id = v.id "
            + LISTED_VOLUMES_WHERE + " AND s.offline = 0 AND r.online = 1 ORDER BY v.id").fetchall()
        max_depth = self.library.settings.limits.max_depth
        out = []
        for r in rows:
            if r["id"] in done:
                continue
            if r["fp_version"] is not None and r["fp_error"] is None:
                try:
                    st = resolve_source_path(Path(r["root_path"]), r["rel_path"]).stat()
                except (OSError, ValueError):
                    continue
                if r["fp_version"] == fingerprint_version(f"{st.st_size}-{st.st_mtime_ns}", max_depth):
                    continue
            out.append(r["id"])
        return out

    def run(self, limit: int | None = None) -> ScanSummary:
        """指紋を作る。1 巻ごとに全巻を引き直さず、まとめて拾って順に処理し、拾い直すのは分解で子が現れたときの 1 巡ごと。"""
        summary = ScanSummary()
        done: set[int] = set()
        while True:
            todo = self.pending(done)
            if not todo:
                break
            for n, vid in enumerate(todo):
                if limit is not None and summary.fingerprinted + summary.errors >= limit:
                    break
                if self.progress:
                    self.progress(len(done), len(done) + len(todo) - n)
                done.add(vid)
                try:
                    self._fingerprint(vid, summary)
                except (VolumeOffline, VolumeNotFound):
                    continue  # 見えなくなった巻は記録しない(再接続後にまた候補になる)
                except (VolumeUnreadable, CacheTooLarge) as e:
                    self._record_error(vid, str(e))
                    summary.errors += 1
                    log.info("巻 %s の指紋を作れません: %s", vid, e)
            else:
                continue
            break  # limit に達した
        if self.progress:
            self.progress(len(done), len(done))
        summary.candidates = refresh_candidates(self.conn)
        return summary

    def _fingerprint(self, volume_id: int, summary: ScanSummary) -> None:
        max_depth = self.library.settings.limits.max_depth
        with self.library.open(self.conn, volume_id) as vol:
            # 内側アーカイブの巻は、開いたついでに中の巻へ分解する。分解したら親ではなく子の指紋を作る
            self.library.register_analysis(self.conn, vol)
            if self.conn.execute("SELECT 1 FROM volumes WHERE parent_id = ? AND missing = 0",
                                 (volume_id,)).fetchone():
                return
            hashes, skipped = fingerprint_pages(len(vol.page_names), vol.read_page)
            version = fingerprint_version(vol.version, max_depth)
            page_count = len(vol.page_names)
        with db.transaction(self.conn) as c:
            c.execute(
                """INSERT INTO volume_fingerprints (volume_id, version, page_count, hashed_pages, skipped_pages,
                       hashes, error, computed_at) VALUES (?, ?, ?, ?, ?, ?, NULL, ?)
                   ON CONFLICT (volume_id) DO UPDATE SET version = excluded.version, page_count = excluded.page_count,
                       hashed_pages = excluded.hashed_pages, skipped_pages = excluded.skipped_pages,
                       hashes = excluded.hashes, error = NULL, computed_at = excluded.computed_at""",
                (volume_id, version, page_count, len(hashes), skipped, pack(hashes), db.now()))
        summary.fingerprinted += 1

    def _record_error(self, volume_id: int, reason: str) -> None:
        with db.transaction(self.conn) as c:
            c.execute(
                """INSERT INTO volume_fingerprints (volume_id, version, page_count, hashed_pages, skipped_pages,
                       hashes, error, computed_at) VALUES (?, '', 0, 0, 0, x'', ?, ?)
                   ON CONFLICT (volume_id) DO UPDATE SET error = excluded.error, computed_at = excluded.computed_at""",
                (volume_id, reason, db.now()))


# --- 候補の参照・却下 --------------------------------------------------------------


class CandidateNotFound(Exception):
    pass


def dismiss(conn: sqlite3.Connection, candidate_id: int, *, restore: bool = False) -> None:
    """候補を「重複ではない」と却下する(restore=True なら戻す)。蔵書には触れない。"""
    with db.transaction(conn) as c:
        cur = c.execute("UPDATE duplicate_candidates SET dismissed_at = ? WHERE id = ?",
                        (None if restore else db.now(), candidate_id))
        if cur.rowcount == 0:
            raise CandidateNotFound(candidate_id)


def candidate_counts(conn: sqlite3.Connection) -> dict[int, int]:
    """巻ごとの、両方が一覧に見えており却下されていない候補の数(一覧の印用)。

    画面(`GET /api/duplicates`)は一覧に見えている巻どうしの組だけを出すので、印もそれに揃える。
    片方が見えなくなった(missing・整理済み)組を数えると、開いても何も出ない印が残る。
    """
    listed = {r[0] for r in conn.execute("SELECT v.id " + LISTED_VOLUMES_JOIN + LISTED_VOLUMES_WHERE)}
    counts: dict[int, int] = defaultdict(int)
    for r in conn.execute("SELECT volume_a, volume_b FROM duplicate_candidates WHERE dismissed_at IS NULL"):
        if r["volume_a"] in listed and r["volume_b"] in listed:
            counts[r["volume_a"]] += 1
            counts[r["volume_b"]] += 1
    return dict(counts)
