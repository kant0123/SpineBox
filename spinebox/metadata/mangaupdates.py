"""MangaUpdates(公開 API v1)クライアント。作品単位のジャンル・タグ・別名(日本語名を含む)・原版元。

キー不要(ログインしない読み取りだけ)。検索は `POST /v1/series/search`、詳細は `GET /v1/series/{id}`。
検索の結果には別名・著者・出版社が無いので、上位の候補だけ詳細を引き直す(詳細が取れなければ検索結果の項目で返す)。
レート制限は公式ドキュメントに数値が無い。応答のヘッダにも制限の値は出ないので、控えめな間隔で送る。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

from .anilist import clean_text
from .base import MetadataSource, ParseError, _Run
from .http import FetchError, SourceRequest
from .match import score_item
from .model import KIND_MANGA, KIND_NOVEL, SCOPE_WORK, MetadataItem, SearchQuery

NAME = "mangaupdates"
BASE = "https://api.mangaupdates.com/v1"

MIN_PERPAGE = 10

# 取り込むカテゴリ(タグ)の最低得票数と上限。得票の少ないものは荒らし・誤りが混ざる
CATEGORY_MIN_VOTES = 3
CATEGORY_LIMIT = 20

_KINDS = {"manga": KIND_MANGA, "manhwa": KIND_MANGA, "manhua": KIND_MANGA, "doujinshi": KIND_MANGA,
          "oel": KIND_MANGA, "novel": KIND_NOVEL}
_HAS_JAPANESE = re.compile("[\u3040-\u30ff\u3400-\u9fff]")  # かな・漢字


@dataclass(frozen=True)
class Hit:
    record: dict
    hit_title: str | None


class MangaUpdatesSource(MetadataSource):
    name = NAME

    def _search(self, query: SearchQuery, run: _Run):
        # perpage は 10 未満を指定しても無視されて 25 件返る(実通信で確認)ので、10 以上にして先頭から切る
        body = {"search": query.title, "perpage": max(MIN_PERPAGE, self.settings.candidate_limit)}
        search_req = SourceRequest(NAME, "POST", f"{BASE}/series/search", json_body=body, label="search")
        hits = run.fetch(search_req, parse_search)[: self.settings.candidate_limit]
        items = [_item_from_record(h.record, h.hit_title) for h in hits]
        # 詳細を引くのは、検索結果の項目で見た確からしさの高い順(MangaUpdates の並びは当てにしない)
        ranked = sorted(range(len(hits)), key=lambda i: score_item(query, items[i]).score, reverse=True)
        detail_for = set(ranked[: self.settings.detail_limit])
        out: list[tuple[MetadataItem, SourceRequest]] = []
        for index, hit in enumerate(hits):
            item, request = items[index], search_req
            if index in detail_for:
                series_id = hit.record["series_id"]
                detail_req = SourceRequest(NAME, "GET", f"{BASE}/series/{series_id}", label="detail")
                try:
                    item = run.fetch(detail_req, parse_detail)
                    request = detail_req
                except (FetchError, ParseError) as e:
                    run.note(f"詳細を取得できなかったため検索結果の項目で返した: series_id={series_id} ({e})")
            out.append((item, request))
        return out


def _load(body: bytes) -> dict:
    try:
        data = json.loads(body)
    except ValueError:
        raise ParseError("JSON として不正") from None
    if not isinstance(data, dict):
        raise ParseError("想定した JSON の形ではない")
    return data


def parse_search(body: bytes) -> list[Hit]:
    data = _load(body)
    results = data.get("results")
    if not isinstance(results, list):
        raise ParseError("results が無い(検索の応答ではない)")
    hits = []
    for r in results:
        record = r.get("record") if isinstance(r, dict) else None
        if isinstance(record, dict) and record.get("series_id") is not None and record.get("title"):
            hits.append(Hit(record, r.get("hit_title") if isinstance(r.get("hit_title"), str) else None))
    return hits


def parse_detail(body: bytes) -> MetadataItem:
    data = _load(body)
    item = _item_from_record(data, None)
    if item is None:
        raise ParseError("series_id か title が無い(詳細の応答ではない)")
    return item


def _item_from_record(record: dict, hit_title: str | None) -> MetadataItem | None:
    """検索結果の record でも、詳細の応答でも同じ関数で正規化する(詳細にだけある項目は無ければ空)。"""
    series_id, primary = record.get("series_id"), record.get("title")
    if series_id is None or not primary:
        return None
    associated = [a["title"] for a in (record.get("associated") or [])
                  if isinstance(a, dict) and isinstance(a.get("title"), str) and a["title"]]
    if hit_title and hit_title != primary:
        associated.append(hit_title)  # 検索が当たった別名(検索の応答にだけ出る)
    names = list(dict.fromkeys([primary, *associated]))
    japanese = next((n for n in names if _HAS_JAPANESE.search(n)), None)
    # MangaUpdates の正式名(primary)は日本の作品ではローマ字が多い。日本語名があればそれを作品名にする
    title = japanese or primary
    romaji = primary if japanese and primary.isascii() else None
    aliases = tuple(n for n in names if n != title and n != romaji)
    return MetadataItem(
        source=NAME,
        source_id=str(series_id),
        scope=SCOPE_WORK,
        title=title,
        romaji=romaji,
        aliases=aliases,
        authors=_authors(record.get("authors")),
        total_volumes=_total_volumes(record.get("status")),
        publisher=_original_publisher(record.get("publishers")),
        released=str(record["year"]) if record.get("year") not in (None, "") else None,
        kind=_KINDS.get(str(record.get("type") or "").lower()),
        genres=tuple(g["genre"] for g in (record.get("genres") or []) if isinstance(g, dict) and g.get("genre")),
        tags=_categories(record.get("categories")),
        synopsis=clean_text(record.get("description")),
        source_url=record.get("url"),
    )


def _authors(authors: object) -> tuple[str, ...]:
    names = [a["name"] for a in (authors or []) if isinstance(a, dict) and a.get("name")]
    return tuple(dict.fromkeys(names))


def _original_publisher(publishers: object) -> str | None:
    for p in publishers or []:
        if isinstance(p, dict) and p.get("type") == "Original" and p.get("publisher_name"):
            return p["publisher_name"]
    return None


def _total_volumes(status: object) -> int | None:
    """`status` は `5 Volumes (Complete)` のような自由記述。巻数が書かれていれば取る。"""
    m = re.match(r"\s*(\d+)\s+Volumes?\b", status) if isinstance(status, str) else None
    return int(m.group(1)) or None if m else None  # 0 巻は未定の意味なので None


def _categories(categories: object) -> tuple[str, ...]:
    picked = [c for c in (categories or []) if isinstance(c, dict) and c.get("category")
              and isinstance(c.get("votes"), int) and c["votes"] >= CATEGORY_MIN_VOTES]
    picked.sort(key=lambda c: c["votes"], reverse=True)
    return tuple(c["category"] for c in picked[:CATEGORY_LIMIT])
