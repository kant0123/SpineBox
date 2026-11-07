"""公開 DB(国立国会図書館サーチ・AniList・MangaUpdates)の情報を作品・巻へ結線する(GOAL.md フェーズ 3、#50)。

取得(`fetch_work`)と反映(`apply_work`)を分けている。

- 取得: 取得元ごとに検索し、候補を作品に対応づける。確信度が高く単独で飛び抜けた候補だけ自動で連携し、
  拮抗・低確信度・漫画と小説の同名は**確認待ち**(`pending`)にして候補を残す。結果は `work_links` に
  取得元ごとに 1 行。取得に失敗しても例外は出さず、失敗を理由つきで記録する(スキャンと閲覧を止めない)
- 反映: 連携した候補(`work_links.item`)から、作品の項目(`work_fields`)・別名・巻の書誌(`volume_fields`)を埋める。
  **DB だけで完結し、ネットワークに出ない**(スキャン・手動の編集・連携の選び直しの後にも安全に呼べる)。
  空欄だけを埋め、手動の項目は何があっても変えない。名前(ファイル名)から入った値は、確信度が高いか利用者が
  連携を選んだときだけ公開 DB の値で置き換える(置き換えた値は `evidence.replaced` に残し、連携が外れたら戻す)

規則と理由は wiki/concepts/metadata-linking.md。
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import sqlite3
import unicodedata
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone

from . import db, ja_terms, works
from .analysis import compute_findings
from .config import MetadataSettings
from .metadata import MetadataService, SearchQuery
from .metadata.model import SearchOutcome
from .metadata.match import is_romaji_word
from .naming import normalize_title
from .scanner import LISTED_VOLUMES_JOIN, LISTED_VOLUMES_WHERE

log = logging.getLogger(__name__)

# 検索の順。国立国会図書館サーチで種別・著者を決めてから、作品単位の取得元の候補を絞る
SEARCH_ORDER = ("ndl", "anilist", "mangaupdates")

AUTO_LINK_MIN = 0.75      # 自動で連携する最小の確からしさ
LINK_MARGIN = 0.15        # 2 位との差がこれ以上あるときだけ自動で連携する(拮抗は確認待ち)
SHOW_MIN = 0.5            # これ未満の候補は利用者に見せない
SHOW_LIMIT = 5
REPLACE_MIN = 0.9         # 名前由来の値を公開 DB の値で置き換える最小の確信度(利用者が選んだ連携は無条件)
VOLUME_FIT_WEIGHT = 0.15  # 国立国会図書館サーチの候補の組を、手元の巻番号が覆う割合で補正する重み
NDL_LIMIT = 100           # 巻が多い作品でも巻を揃えるため、国立国会図書館サーチではこの件数まで取る
ALIAS_LIMIT = 6
GENRES_LIMIT = 15
TAGS_LIMIT = 30

PRIORITY = {
    works.FIELD_TITLE: ("ndl", "mangaupdates", "anilist"),
    works.FIELD_AUTHOR: ("ndl", "anilist", "mangaupdates"),
    works.FIELD_KIND: ("ndl", "mangaupdates", "anilist"),
    works.FIELD_PUBLISHER: ("ndl", "mangaupdates"),
    works.FIELD_TOTAL_VOLUMES: ("anilist", "mangaupdates"),
}
UNION_PRIORITY = {
    works.FIELD_GENRES: ("mangaupdates", "anilist"),
    works.FIELD_TAGS: ("mangaupdates", "anilist"),
}
UNION_LIMIT = {works.FIELD_GENRES: GENRES_LIMIT, works.FIELD_TAGS: TAGS_LIMIT}
MANAGED_FIELDS = (*PRIORITY, *UNION_PRIORITY)


@dataclass(frozen=True)
class Display:
    """日本語での表示の方針(#74。設定 `[metadata] untranslated_terms`)。"""
    keep_untranslated_terms: bool = False  # 対応表に無い英語のジャンル・タグを英語のまま入れるか


_display = Display()


def configure(settings: MetadataSettings) -> None:
    """設定の表示方針を反映に使う。アプリの起動時(最初の `apply_all` の前)に呼ぶ。"""
    global _display
    _display = Display(keep_untranslated_terms=settings.untranslated_terms == "keep")


# --- 候補 ------------------------------------------------------------------------------------
#
# 候補は {"summary": 利用者に見せる概要, "item": 反映に使う中身} の辞書で持つ(そのまま JSON になる)。


def _item_dict(item) -> dict:
    d = asdict(item)
    return {k: (list(v) if isinstance(v, tuple) else v) for k, v in d.items()}


def _work_candidate(c) -> dict:
    item = c.item
    summary = {
        "source_id": item.source_id, "title": item.title, "subtitle": item.subtitle, "romaji": item.romaji,
        "authors": list(item.authors), "kind": item.kind, "publisher": item.publisher, "released": item.released,
        "total_volumes": item.total_volumes, "genres": list(item.genres)[:6], "url": item.source_url,
        "score": c.match.score, "reasons": list(c.match.reasons), "response_key": c.response_key,
    }
    return {"summary": summary, "item": _item_dict(item)}


def _label_stem(label: str | None) -> str:
    """シリーズ名から巻次の付記(`集英社オレンジ文庫 ; 第1-10巻`)を除く。"""
    return normalize_title(re.split(r"\s*[;；]\s*", label or "")[0])


def _ndl_groups(candidates, numbers: list[int]) -> list[dict]:
    """国立国会図書館サーチの巻ごとの書誌を、同じ本(種別・作品名・シリーズ名・著者が同じ)ごとの組にする。

    同じ作品名で漫画と小説が別の巻として並ぶので、連携の単位は 1 冊ではなく組。組の確からしさは
    組の中の最良の巻の確からしさを、手元の巻番号が組の巻で覆われる割合で補正する(名前が同じでも
    手元の 1〜4 巻を持つ組と、巻次の無い映画ノベライズとを見分けるため)。
    """
    groups: dict[tuple, list] = defaultdict(list)
    for c in candidates:
        i = c.item
        key = (i.kind, normalize_title(i.title), _label_stem(i.label), tuple(sorted(normalize_title(a) for a in i.authors)))
        groups[key].append(c)
    out = []
    wanted = set(numbers)
    for key, members in groups.items():
        members.sort(key=lambda c: (c.item.volume is None, c.item.volume or 0, c.item.released or ""))
        best = max(members, key=lambda c: c.match.score)
        have = {c.item.volume for c in members if c.item.volume is not None}
        fit = len(wanted & have) / len(wanted) if wanted else None
        base = best.match.score
        score = round(base * (1 - VOLUME_FIT_WEIGHT) + fit * VOLUME_FIT_WEIGHT, 4) if fit is not None else base
        rep = best.item
        publishers = Counter(c.item.publisher for c in members if c.item.publisher)
        publisher = publishers.most_common(1)[0][0] if publishers else None
        authors = max((c.item.authors for c in members), key=len)
        source_id = "ndl:" + hashlib.sha1(json.dumps(key, ensure_ascii=False).encode("utf-8")).hexdigest()[:10]
        records = [{
            "source_id": c.item.source_id, "volume": c.item.volume, "volume_label": c.item.volume_label,
            "title": c.item.title, "subtitle": c.item.subtitle, "publisher": c.item.publisher, "label": c.item.label,
            "released": c.item.released, "isbn": c.item.isbn, "url": c.item.source_url, "score": c.match.score,
            "response_key": c.response_key,
        } for c in members]
        reasons = list(best.match.reasons) + ([f"volume_fit:{len(wanted & have)}/{len(wanted)}"] if wanted else [])
        summary = {
            "source_id": source_id, "title": rep.title, "subtitle": None, "romaji": None, "authors": list(authors),
            "kind": rep.kind, "publisher": publisher, "label": _clean_label(rep.label), "released": None,
            "total_volumes": None, "genres": [], "url": rep.source_url, "score": score, "reasons": reasons,
            "response_key": best.response_key, "record_count": len(members),
            "volumes": sorted(have), "base_score": base, "volume_fit": fit,
        }
        item = {"title": rep.title, "authors": list(authors), "kind": rep.kind, "publisher": publisher,
                "label": _clean_label(rep.label), "records": records}
        out.append({"summary": summary, "item": item})
    return out


def _clean_label(label: str | None) -> str | None:
    return re.split(r"\s*[;；]\s*", label or "")[0].strip() or None


def candidates_for(source: str, outcome: SearchOutcome, ctx: "_Context") -> list[dict]:
    if source == "ndl":
        return _ndl_groups(outcome.candidates, ctx.numbers)
    return [_work_candidate(c) for c in outcome.candidates]


def decide(cands: list[dict], known_kind: str | None, hold: bool = False,
           title_is_romaji: bool = False) -> tuple[str, dict | None, list[dict], float | None]:
    """候補から連携を決める。(状態, 連携する候補, 見せる候補, 2 位との差)。

    状態: linked(自動で連携)/ pending(確認待ち)/ none(見せられる候補が無い)。
    種別が分かっている作品では、種別の違う候補を除く(種別の分からない候補は残す)。自動で連携するのは、
    確からしさが AUTO_LINK_MIN 以上で、2 位との差が LINK_MARGIN 以上あるときだけ。
    hold が真なら(漫画か小説かが未決)、自動で連携せず確認待ちにする。
    """
    usable = [c for c in cands if not (known_kind and c["summary"]["kind"] and c["summary"]["kind"] != known_kind)]
    usable.sort(key=lambda c: c["summary"]["score"], reverse=True)
    shown = [c for c in usable if c["summary"]["score"] >= SHOW_MIN][:SHOW_LIMIT]
    if not shown:
        return "none", None, [], None
    top = shown[0]["summary"]["score"]
    second = shown[1]["summary"]["score"] if len(shown) > 1 else 0.0
    margin = round(top - second, 4)
    if top >= AUTO_LINK_MIN and margin >= LINK_MARGIN and not hold:
        return "linked", shown[0], shown, margin
    return "pending", None, shown, margin


def kind_unresolved(c: sqlite3.Connection, work_id: int) -> bool:
    """漫画か小説かが未決か。国立国会図書館サーチの候補が確認待ちで、種別の違う候補が並んでいるとき真。

    この間は、AniList / MangaUpdates が作品名の一致だけで種別を決めてしまわないよう、自動で連携しない
    (利用者が国立国会図書館サーチの候補か種別を選ぶと、次の取得で連携する)。
    """
    if _context(c, work_id).kind:
        return False
    row = _link_row(c, work_id, "ndl")
    if row is None or row["status"] != "pending" or not row["candidates"]:
        return False
    kinds = {x["summary"]["kind"] for x in json.loads(row["candidates"]) if x["summary"]["kind"]}
    return len(kinds) >= 2


# --- 作品の手がかり ----------------------------------------------------------------------------


@dataclass
class _Context:
    title: str
    author: str | None
    kind: str | None
    numbers: list[int] = field(default_factory=list)


def _context(c: sqlite3.Connection, work_id: int) -> _Context:
    fields = {r["field"]: r["value"] for r in c.execute("SELECT field, value FROM work_fields WHERE work_id = ?", (work_id,))}
    numbers: set[int] = set()
    for r in c.execute(
            "SELECT v.number_kind, v.number_start, v.number_end " + LISTED_VOLUMES_JOIN
            + " JOIN work_volumes wv ON wv.volume_id = v.id " + LISTED_VOLUMES_WHERE + " AND wv.work_id = ?", (work_id,)):
        if r["number_kind"] == "volume" and r["number_start"] is not None:
            end = r["number_end"] if r["number_end"] is not None else r["number_start"]
            if 0 <= end - r["number_start"] <= 50:
                numbers.update(range(r["number_start"], end + 1))
    return _Context(fields.get(works.FIELD_TITLE) or "", fields.get(works.FIELD_AUTHOR) or None,
                    fields.get(works.FIELD_KIND) or None, sorted(numbers))


def _query(source: str, ctx: _Context) -> SearchQuery:
    title = ctx.title
    if (title and not re.search(r"\s", title) and is_romaji_word(title)
            and re.match(r"[A-Z][a-z]", title)):
        split = re.sub(r"([a-z])([A-Z])", r"\1 \2", title)
        split = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", split)
        if split != title:
            title = split
    return SearchQuery(title, author=ctx.author, kind=ctx.kind, limit=NDL_LIMIT if source == "ndl" else None)


# --- 取得 ------------------------------------------------------------------------------------


@dataclass
class FetchResult:
    work_id: int
    sources: dict[str, str] = field(default_factory=dict)  # 取得元 → linked / pending / none / failed / skipped


def _link_row(c, work_id: int, source: str):
    return c.execute("SELECT * FROM work_links WHERE work_id = ? AND source = ?", (work_id, source)).fetchone()


def _failure_json(outcome: SearchOutcome, at: str) -> str:
    f = outcome.failure
    return works._dumps({"kind": f.kind, "message": f.message, "status": f.status, "retry_after": f.retry_after, "at": at})


def _responses(outcome: SearchOutcome) -> list[dict]:
    return [{"label": r.label, "fetched_at": r.fetched_at, "from_cache": r.from_cache, "stale": r.stale,
             "request_key": r.request_key} for r in outcome.responses]


def _record_failure(conn, work_id: int, source: str, failure_json: str) -> None:
    now = db.now()
    with db.transaction(conn) as c:
        if _link_row(c, work_id, source) is None:
            c.execute("INSERT INTO work_links (work_id, source, failure, attempted_at) VALUES (?, ?, ?, ?)",
                      (work_id, source, failure_json, now))
        else:
            c.execute("UPDATE work_links SET failure = ?, attempted_at = ? WHERE work_id = ? AND source = ?",
                      (failure_json, now, work_id, source))


def _store(conn, work_id: int, source: str, outcome: SearchOutcome, cands: list[dict], known_kind: str | None,
           hold: bool = False, title_is_romaji: bool = False) -> str:
    """成功した検索の結果を work_links に書く。利用者が選んだ連携は変えない(中身の更新だけ)。"""
    now = db.now()
    responses = _responses(outcome)
    with db.transaction(conn) as c:
        row = _link_row(c, work_id, source)
        by_id = {x["summary"]["source_id"]: x for x in cands}
        if row is not None and row["manual"] and row["status"] == "linked":
            # 利用者が選んだ連携。同じ候補が今回も見つかったときだけ中身を新しくする
            cand = by_id.get(row["source_id"])
            if cand is not None:
                ev = {"summary": cand["summary"], "decision": "manual", "margin": None, "responses": responses}
                c.execute("""UPDATE work_links SET confidence = ?, evidence = ?, item = ?, failure = NULL,
                             attempted_at = ?, checked_at = ? WHERE work_id = ? AND source = ?""",
                          (cand["summary"]["score"], works._dumps(ev), works._dumps(cand["item"]), now, now,
                           work_id, source))
            else:
                c.execute("UPDATE work_links SET failure = NULL, attempted_at = ?, checked_at = ? WHERE work_id = ? "
                          "AND source = ?", (now, now, work_id, source))
            return "linked"
        kept = by_id.get(row["source_id"]) if row is not None and row["status"] == "linked" else None
        kind_ok = kept is not None and not (known_kind and kept["summary"]["kind"] and kept["summary"]["kind"] != known_kind)
        if kept is not None and kind_ok and not hold:
            # 自動で連携した候補が今回も見つかった: 連携を保つ(順位が少し動いただけで外れて値が消えるのを避ける)。
            # ただし作品の種別と食い違う、または種別が未決になったものは保たない(通常の判定に戻す)
            status, chosen = "linked", kept
            shown = sorted(cands, key=lambda x: x["summary"]["score"], reverse=True)[:SHOW_LIMIT]
            if chosen not in shown:
                shown = [chosen, *shown[:SHOW_LIMIT - 1]]
            margin = None
        else:
            status, chosen, shown, margin = decide(cands, known_kind, hold, title_is_romaji=title_is_romaji)
        ev = {"summary": chosen["summary"] if chosen else None,
              "decision": "auto" if status == "linked" else status, "margin": margin, "responses": responses,
              "hold": "kind_unresolved" if hold and status == "pending" else None}
        values = (status, chosen["summary"]["source_id"] if chosen else None,
                  chosen["summary"]["score"] if chosen else (shown[0]["summary"]["score"] if shown else None),
                  works._dumps(ev), works._dumps(chosen["item"]) if chosen else None,
                  works._dumps(shown) if shown else None, now, now)
        if row is None:
            c.execute("""INSERT INTO work_links (work_id, source, status, source_id, manual, confidence, evidence, item,
                         candidates, failure, attempted_at, checked_at)
                         VALUES (?, ?, ?, ?, 0, ?, ?, ?, ?, NULL, ?, ?)""",
                      (work_id, source, values[0], values[1], values[2], values[3], values[4], values[5], values[6],
                       values[7]))
        else:
            c.execute("""UPDATE work_links SET status = ?, source_id = ?, manual = 0, confidence = ?, evidence = ?,
                         item = ?, candidates = ?, failure = NULL, attempted_at = ?, checked_at = ?
                         WHERE work_id = ? AND source = ?""", (*values, work_id, source))
        return status


def fetch_work(conn: sqlite3.Connection, service: MetadataService, work_id: int,
               sources: tuple[str, ...] | None = None) -> FetchResult:
    """作品 1 つについて、有効な取得元を順に検索し、結果を work_links に記録して項目に反映する。

    取得元の失敗(タイムアウト・5xx・接続不可・429)や想定外の例外は、取得元ごとに記録して次へ進む
    (例外は呼び出し元へ出さない)。利用者が「該当なし」にした取得元には要求を出さない。
    """
    if works.root_of(conn, work_id) != work_id:
        raise works.WorkNotFound(work_id)
    result = FetchResult(work_id)
    for name in SEARCH_ORDER:
        if name not in service.sources or (sources is not None and name not in sources):
            continue
        row = _link_row(conn, work_id, name)
        if row is not None and row["manual"] and row["status"] == "none":
            result.sources[name] = "none"
            continue
        try:
            ctx = _context(conn, work_id)
            title_is_romaji = not _has_cjk(ctx.title)
            outcome = service.search(_query(name, ctx), [name])[0]
            if not outcome.ok:
                _record_failure(conn, work_id, name, _failure_json(outcome, db.now()))
                result.sources[name] = "failed"
                continue
            result.sources[name] = _store(conn, work_id, name, outcome, candidates_for(name, outcome, ctx), ctx.kind,
                                          hold=name != "ndl" and kind_unresolved(conn, work_id),
                                          title_is_romaji=title_is_romaji)
            apply_work(conn, work_id)
        except Exception as e:  # noqa: BLE001 (取得の不具合でスキャン・閲覧・ほかの取得元を止めない)
            log.exception("公開 DB の取得に失敗しました: source=%s", name)
            try:
                _record_failure(conn, work_id, name, works._dumps(
                    {"kind": "internal", "message": f"想定外のエラー({type(e).__name__})", "status": None,
                     "retry_after": None, "at": db.now()}))
            except sqlite3.Error:
                pass
            result.sources[name] = "failed"
    return result


def _known_source(source: str) -> None:
    if source not in SEARCH_ORDER:
        raise works.WorkNotFound(f"取得元 {source} はありません")


def choose(conn: sqlite3.Connection, work_id: int, source: str, source_id: str | None) -> None:
    """確認待ち(または別の候補への変更)の候補を選ぶ。source_id が None なら「該当なし」。
    選んだ結果は手動の決定として固定し、再取得で変えない。"""
    with db.transaction(conn) as c:
        if works.root_of(c, work_id) != work_id:
            raise works.WorkNotFound(work_id)
        _known_source(source)
        row = _link_row(c, work_id, source)
        if row is None:
            raise works.Conflict("この取得元はまだ取得していません")
        if source_id is None:
            ev = {"summary": None, "decision": "manual_none", "margin": None,
                  "responses": (json.loads(row["evidence"]) if row["evidence"] else {}).get("responses", [])}
            c.execute("""UPDATE work_links SET status = 'none', source_id = NULL, manual = 1, confidence = NULL,
                         evidence = ?, item = NULL WHERE work_id = ? AND source = ?""",
                      (works._dumps(ev), work_id, source))
        else:
            cands = json.loads(row["candidates"]) if row["candidates"] else []
            cand = next((x for x in cands if x["summary"]["source_id"] == source_id), None)
            if cand is None:
                raise works.InvalidValue("その候補はありません。もう一度取得してください")
            prev = json.loads(row["evidence"]) if row["evidence"] else {}
            ev = {"summary": cand["summary"], "decision": "manual", "margin": None, "responses": prev.get("responses", [])}
            c.execute("""UPDATE work_links SET status = 'linked', source_id = ?, manual = 1, confidence = ?,
                         evidence = ?, item = ? WHERE work_id = ? AND source = ?""",
                      (source_id, cand["summary"]["score"], works._dumps(ev), works._dumps(cand["item"]), work_id,
                       source))
    apply_work(conn, work_id)


def reset_link(conn: sqlite3.Connection, work_id: int, source: str) -> None:
    """選んだ連携の手動を解除して、保存済みの候補から自動の規則で決め直す(ネットワークには出ない)。"""
    with db.transaction(conn) as c:
        if works.root_of(c, work_id) != work_id:
            raise works.WorkNotFound(work_id)
        _known_source(source)
        row = _link_row(c, work_id, source)
        if row is None or not row["manual"]:
            raise works.Conflict("この連携は手動で選ばれていません")
        cands = json.loads(row["candidates"]) if row["candidates"] else []
        prev = json.loads(row["evidence"]) if row["evidence"] else {}
        # まず連携を外した状態(確認待ち)にする。手動の連携が決めた種別を、決め直しの前提に使わないため
        c.execute("""UPDATE work_links SET status = 'pending', source_id = NULL, manual = 0, item = NULL
                     WHERE work_id = ? AND source = ?""", (work_id, source))
    apply_work(conn, work_id)  # この連携から入った項目(種別など)を外す
    with db.transaction(conn) as c:
        hold = source != "ndl" and kind_unresolved(c, work_id)
        # 種別が決まっていなければ絞らない(ほかの候補が消えるのを避ける)。決まっていれば違う種別の候補は除く
        _rctx = _context(c, work_id)
        status, chosen, shown, margin = decide(cands, _rctx.kind, hold, title_is_romaji=not _has_cjk(_rctx.title))
        ev = {"summary": chosen["summary"] if chosen else None,
              "decision": "auto" if status == "linked" else status, "margin": margin,
              "responses": prev.get("responses", []),
              "hold": "kind_unresolved" if hold and status == "pending" else None}
        c.execute("""UPDATE work_links SET status = ?, source_id = ?, manual = 0, confidence = ?, evidence = ?,
                     item = ?, candidates = ? WHERE work_id = ? AND source = ?""",
                  (status, chosen["summary"]["source_id"] if chosen else None,
                   chosen["summary"]["score"] if chosen else (shown[0]["summary"]["score"] if shown else None),
                   works._dumps(ev), works._dumps(chosen["item"]) if chosen else None,
                   works._dumps(shown) if shown else None, work_id, source))
    apply_work(conn, work_id)


# --- 反映 ------------------------------------------------------------------------------------


@dataclass
class _Desired:
    value: str
    confidence: float
    manual_link: bool
    sources: list[str]

    def can_replace(self) -> bool:
        return self.manual_link or self.confidence >= REPLACE_MIN


def _extract(name: str, item: dict) -> str | None:
    if name == works.FIELD_TITLE:
        return (item.get("title") or "").strip() or None
    if name == works.FIELD_AUTHOR:
        authors = [a for a in item.get("authors") or [] if a]
        return works.author_text(authors)
    if name in (works.FIELD_KIND, works.FIELD_PUBLISHER):
        return (item.get(name) or "").strip() or None
    if name == works.FIELD_TOTAL_VOLUMES:
        n = item.get("total_volumes")
        return str(n) if isinstance(n, int) and n > 0 else None
    return None


def _desired_fields(links: dict[str, sqlite3.Row]) -> dict[str, _Desired]:
    payload = {s: json.loads(r["item"]) for s, r in links.items() if r["status"] == "linked" and r["item"]}
    out: dict[str, _Desired] = {}

    def meta(source: str):
        r = links[source]
        return float(r["confidence"] or 0.0), bool(r["manual"])

    for name, order in PRIORITY.items():
        found = [(s, v) for s in order if s in payload for v in [_extract(name, payload[s])] if v]
        if found:
            source, value = found[0]
            if name == works.FIELD_PUBLISHER:
                value = ja_terms.publisher_ja(value)  # NDL は日本語。MangaUpdates のローマ字名を日本語に
            conf, manual = meta(source)
            out[name] = _Desired(value, conf, manual, [source])
    for name, order in UNION_PRIORITY.items():
        merged: list[str] = []
        seen: set[str] = set()
        used: list[str] = []
        for source in order:
            # 英語の語を日本語にしてから重ねる(MangaUpdates の `Teacher/s` と AniList の `Teacher` は同じ「教師」)
            values = ja_terms.localize_terms((payload.get(source) or {}).get(name) or [],
                                             keep_unknown=_display.keep_untranslated_terms)
            for v in values:
                key = v.casefold()
                if key not in seen:
                    seen.add(key)
                    merged.append(v)
                    if source not in used:
                        used.append(source)
        if merged:
            merged = merged[:UNION_LIMIT[name]]
            conf = max(meta(s)[0] for s in used)
            out[name] = _Desired(works._dumps(merged), conf, any(meta(s)[1] for s in used), used)
    return out


def _same(name: str, a: str | None, b: str | None) -> bool:
    if name in (works.FIELD_TITLE, works.FIELD_AUTHOR):
        return normalize_title(a or "") == normalize_title(b or "")
    return (a or "") == (b or "")


def _prefer_japanese_value(name: str, row, desired: _Desired) -> bool:
    """ファイル名・外側の名前から入った作品名・著者より、公開 DB の日本語の値を優先する。

    作品名と著者は一覧・検索・整理のテンプレートにそのまま出るので、ローマ字のファイル名のままにしない
    (利用者の要望)。日本語同士の置き換えは確信度 0.9 の規則に委ねる(低確信度の別作品で正しい日本語を
    上書きしない)。手動の値(`manual`)は `_apply_field()` が先に除く。置き換えた元の作品名は別名と evidence.replaced に残す。
    """
    if name not in (works.FIELD_TITLE, works.FIELD_AUTHOR) or row is None:
        return False
    return (row["source"] in (works.SOURCE_FILENAME, works.SOURCE_CONTAINER)
            and not _has_cjk(row["value"] or "")
            and _has_cjk(desired.value))


def _apply_field(c, work_id: int, name: str, desired: _Desired | None) -> None:
    row = works._row(c, "work_fields", "work_id", work_id, name)
    if row is not None and row["manual"]:
        return  # 手動の項目は何があっても変えない(空にしたものも)
    evidence = json.loads(row["evidence"]) if row is not None and row["evidence"] else {}
    empty = row is None or not (row["value"] or "").strip() or (name in works.LIST_FIELDS and row["value"] == "[]")
    if desired is None:
        if row is not None and row["source"] == works.SOURCE_PUBLIC_DB:
            replaced = evidence.get("replaced")
            if replaced:  # 公開 DB の値で置き換える前の値に戻す
                works._set_field(c, work_id, name, replaced["value"], replaced["source"], False,
                                 replaced["confidence"], replaced["evidence"])
            elif name != works.FIELD_TITLE:
                c.execute("DELETE FROM work_fields WHERE work_id = ? AND field = ?", (work_id, name))
                c.execute("UPDATE works SET updated_at = ? WHERE id = ?", (db.now(), work_id))
        return
    new_ev = {"link": {"sources": desired.sources, "confidence": desired.confidence, "manual": desired.manual_link}}
    if empty:
        works._set_field(c, work_id, name, desired.value, works.SOURCE_PUBLIC_DB, False, desired.confidence, new_ev)
        return
    if row["source"] == works.SOURCE_PUBLIC_DB:
        if row["value"] != desired.value or row["confidence"] != desired.confidence:
            if evidence.get("replaced"):
                new_ev["replaced"] = evidence["replaced"]
            works._set_field(c, work_id, name, desired.value, works.SOURCE_PUBLIC_DB, False, desired.confidence, new_ev)
        return
    # 名前(ファイル名)から入った値。確信度が高いか利用者が連携を選んだときだけ置き換える。
    # ただし日本語の作品名・著者は、ファイル名由来の名前より優先する(ローマ字のファイル名のままにしない)
    if _same(name, row["value"], desired.value) or not (desired.can_replace() or _prefer_japanese_value(name, row, desired)):
        return
    new_ev["replaced"] = {"value": row["value"], "source": row["source"], "confidence": row["confidence"],
                          "evidence": evidence or None}
    if name == works.FIELD_TITLE:  # 置き換えた作品名は別名に残す(その名前の巻がまた新しい作品にならないように)
        works._insert_alias(c, work_id, row["value"], row["source"], manual=False, confidence=row["confidence"],
                            evidence={"replaced_by": desired.value})
    works._set_field(c, work_id, name, desired.value, works.SOURCE_PUBLIC_DB, False, desired.confidence, new_ev)


def _has_cjk(text: str) -> bool:
    return any("぀" <= ch <= "ヿ" or "一" <= ch <= "鿿" for ch in text)


def _usable_alias(name: str) -> bool:
    """日本語(かな・漢字)か英数字の綴りだけの名前。ほかの言語の別名(タイ語など)は名寄せの手がかりにしない。"""
    letters = [ch for ch in name if ch.isalnum()]
    if len(normalize_title(name)) < 3 or not letters:
        return False
    return all(ord(ch) < 0x250 or "぀" <= ch <= "ヿ" or "一" <= ch <= "鿿" or "＀" <= ch <= "￯"
               for ch in letters)


def _apply_aliases(c, work_id: int, links: dict[str, sqlite3.Row]) -> None:
    title = normalize_title(works._title_of(c, work_id)[0])
    wanted: set[str] = set()
    for source, r in links.items():
        if r["status"] != "linked" or not r["item"]:
            continue
        item = json.loads(r["item"])
        # ローマ字名と日本語の名前を先に(名寄せの手がかりになる)、そのほかの綴りの別名は残りの枠で
        others = [a for a in (item.get("aliases") or []) if a]
        names = [item.get("title"), item.get("romaji"), *[a for a in others if _has_cjk(a)],
                 *[a for a in others if not _has_cjk(a)]] if source != "ndl" else [item.get("title")]
        added = 0
        for name in names:
            if not name or normalize_title(name) == title or not _usable_alias(name):
                continue
            if added >= ALIAS_LIMIT:
                break
            wanted.add(normalize_title(name))
            works._insert_alias(c, work_id, name.strip(), works.SOURCE_PUBLIC_DB, manual=False,
                                confidence=min(float(r["confidence"] or 0.0), 1.0),
                                evidence={"source": source, "source_id": r["source_id"]})
            added += 1
    # 連携が外れた(または別の候補になった)ら、その公開 DB 由来の別名も外す。手動の別名と、利用者が外した別名は触らない
    for a in c.execute("SELECT id, norm FROM work_aliases WHERE work_id = ? AND source = ? AND manual = 0 "
                       "AND dismissed_at IS NULL", (work_id, works.SOURCE_PUBLIC_DB)).fetchall():
        if a["norm"] not in wanted:
            c.execute("DELETE FROM work_aliases WHERE id = ?", (a["id"],))


def _apply_volumes(c, work_id: int, links: dict[str, sqlite3.Row], findings: dict[int, list]) -> None:
    """国立国会図書館サーチの巻ごとの書誌を、巻番号が単独で分かる巻へ入れる。

    巻番号は名前から読んだ候補なので、中身との食い違い(findings)がある巻には入れない。同じ番号の巻
    (別スキャンの重複)には同じ書誌が入る。連携が外れたら、公開 DB から入れた値を消す。
    """
    ndl = links.get("ndl")
    records: dict[int, dict] = {}
    if ndl is not None and ndl["status"] == "linked" and ndl["item"]:
        for rec in json.loads(ndl["item"]).get("records") or []:
            if rec.get("volume") is not None:
                records.setdefault(rec["volume"], rec)
    conf = float(ndl["confidence"] or 0.0) if ndl is not None else 0.0
    rows = c.execute(
        "SELECT v.id, v.number_kind, v.number_start, v.number_end, v.number_evidence " + LISTED_VOLUMES_JOIN
        + " JOIN work_volumes wv ON wv.volume_id = v.id " + LISTED_VOLUMES_WHERE + " AND wv.work_id = ?",
        (work_id,)).fetchall()
    for v in rows:
        rec = None
        if (v["number_kind"] == "volume" and v["number_start"] is not None and v["number_start"] == v["number_end"]
                and not findings.get(v["id"])):
            rec = records.get(v["number_start"])
        existing = {r["field"]: r for r in c.execute("SELECT * FROM volume_fields WHERE volume_id = ?", (v["id"],))}
        for name in works.VOLUME_FIELDS:
            cur = existing.get(name)
            if cur is not None and cur["manual"]:
                continue
            value = (rec.get(name) if rec else None) or None
            if value is None:
                if cur is not None and cur["source"] == works.SOURCE_PUBLIC_DB:
                    c.execute("DELETE FROM volume_fields WHERE volume_id = ? AND field = ?", (v["id"], name))
                continue
            value = str(value)
            if cur is not None and cur["value"] == value and cur["source"] == works.SOURCE_PUBLIC_DB \
                    and cur["confidence"] == conf:
                continue
            works._set_volume_field(c, v["id"], name, value, works.SOURCE_PUBLIC_DB, False, conf, {
                "source": "ndl", "record": rec["source_id"], "url": rec.get("url"), "response_key": rec.get("response_key"),
                "number_from_name": v["number_evidence"], "link_manual": bool(ndl["manual"])})


def apply_work(conn: sqlite3.Connection, work_id: int, findings: dict[int, list] | None = None) -> None:
    """連携の中身から作品の項目・別名・巻の書誌を埋める。DB だけで完結する。冪等。"""
    with db.transaction(conn) as c:
        if works.root_of(c, work_id) != work_id:
            return
        links = {r["source"]: r for r in c.execute("SELECT * FROM work_links WHERE work_id = ?", (work_id,))}
        desired = _desired_fields(links)
        for name in MANAGED_FIELDS:
            _apply_field(c, work_id, name, desired.get(name))
        _apply_aliases(c, work_id, links)
        _apply_volumes(c, work_id, links, compute_findings(c) if findings is None else findings)


def apply_all(conn: sqlite3.Connection) -> int:
    """連携のある作品すべてに反映する(スキャン・統合の後に、新しい巻・吸収された作品の分を取り込む)。"""
    ids = [r["work_id"] for r in conn.execute(
        "SELECT DISTINCT l.work_id FROM work_links l JOIN works w ON w.id = l.work_id WHERE w.merged_into IS NULL")]
    if not ids:
        return 0
    findings = compute_findings(conn)
    for work_id in ids:
        apply_work(conn, work_id, findings)
    return len(ids)


# --- 取得の対象を選ぶ(データ管理の「作品情報のみ補完」と、裏の少しずつ取得) -----------------------------------


def works_needing_fetch(conn: sqlite3.Connection, settings: MetadataSettings, enabled: tuple[str, ...],
                        now: datetime | None = None) -> list[int]:
    """取得が必要な作品の id。一度も取得していない作品が先、次に取得が古い順。

    必要: 有効な取得元に行が無い / 失敗してから retry_hours 以上 / 取得してから recheck_days 以上。
    利用者が「該当なし」にした取得元は、取り直さない。見えている巻を持つ作品だけ。
    """
    now = now or datetime.now(timezone.utc)
    sources = [s for s in SEARCH_ORDER if s in enabled]
    if not sources:
        return []
    links: dict[int, dict[str, sqlite3.Row]] = defaultdict(dict)
    for r in conn.execute("SELECT * FROM work_links"):
        links[r["work_id"]][r["source"]] = r
    live = {r["work_id"] for r in conn.execute(
        "SELECT DISTINCT wv.work_id " + LISTED_VOLUMES_JOIN + " JOIN work_volumes wv ON wv.volume_id = v.id "
        + LISTED_VOLUMES_WHERE)}
    due: list[tuple[int, str, int]] = []
    for w in conn.execute("SELECT id FROM works WHERE merged_into IS NULL ORDER BY id"):
        wid = w["id"]
        if wid not in live:
            continue
        oldest = None
        never = False
        for s in sources:
            r = links[wid].get(s)
            if r is None or r["attempted_at"] is None:
                never = True
                continue
            if r["manual"] and r["status"] == "none":
                continue
            at = datetime.fromisoformat(r["attempted_at"])
            wait = timedelta(hours=settings.retry_hours) if r["failure"] else timedelta(days=settings.recheck_days)
            if now - at >= wait:
                oldest = r["attempted_at"] if oldest is None or r["attempted_at"] < oldest else oldest
        if never:
            due.append((0, "", wid))
        elif oldest is not None:
            due.append((1, oldest, wid))
    due.sort()
    return [wid for _, _, wid in due]
