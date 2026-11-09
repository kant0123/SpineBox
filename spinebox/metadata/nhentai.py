"""nHentai(公式 API v2)クライアント。同人誌・成人向け漫画の題名・サークル・作者・原作・タグ。

検索は `GET /api/v2/search`、詳細は `GET /api/v2/galleries/{id}`。どちらもキー不要で、匿名の制限は
検索 10 回/分・詳細 20 回/分(IP ごと)。API キーがあれば `[metadata.keys] nhentai_api_key` に置くと
`Authorization: Key …` で送る(制限が緩む。間隔は `[metadata.min_interval_seconds] nhentai` で下げる)。
検索の結果は一覧用の軽い項目(題名・ページ数・タグ ID)で、タグ名・分類は詳細にだけある。そのため
同じ本の翻訳版をまとめたあと、確からしさの高い上位の候補だけ詳細を引き直す(MangaUpdates と同じ形)。
題名は同人誌の書式 `(イベント) [サークル (作者)] 題名 (原作)` で、`doujin.py` で分けて使う。
"""
from __future__ import annotations

import json

from .base import MetadataSource, ParseError, _Run
from .doujin import DoujinTitle, build_item, dedupe_key, is_translation, search_queries
from .http import FetchError, SourceRequest
from .match import score_item
from .model import MetadataItem, SearchQuery

NAME = "nhentai"
BASE = "https://nhentai.net/api/v2"
SITE = "https://nhentai.net"

KEY_NAME = "nhentai_api_key"  # [metadata.keys] に置く名前


class NHentaiSource(MetadataSource):
    name = NAME

    def _headers(self) -> tuple[tuple[str, str], ...]:
        key = self.settings.keys.get(KEY_NAME, "").strip()
        return (("Authorization", f"Key {key}"),) if key else ()

    def _search(self, query: SearchQuery, run: _Run):
        records: list[dict] = []
        search_req = None
        for keywords in search_queries(query.title, query.author):
            search_req = SourceRequest(NAME, "GET", f"{BASE}/search", params=(("query", keywords),),
                                       label="search", secret_headers=self._headers())
            records = run.fetch(search_req, parse_search)[: self.settings.candidate_limit]
            if records:
                break
        if not records or search_req is None:
            return []

        # 同じ本の翻訳版・再アップロードを 1 件にまとめる。日本語の原本 → 翻訳でない → 古い ID の順に残す
        best: dict[str, tuple[tuple, MetadataItem, dict]] = {}
        merged = 0
        for rec in records:
            item, base = _item_from_list(rec)
            rank = (is_translation(base.marks), not _japanese(rec), rec["id"])
            key = dedupe_key(item)
            if key in best:
                merged += 1
                if rank >= best[key][0]:
                    continue
            best[key] = (rank, item, rec)
        if merged:
            run.note(f"同じ本の翻訳版・再アップロード {merged} 件を 1 件にまとめた")
        entries = list(best.values())

        # 詳細を引くのは、検索結果の項目で見た確からしさの高い順
        ranked = sorted(range(len(entries)), key=lambda i: score_item(query, entries[i][1]).score, reverse=True)
        detail_for = set(ranked[: self.settings.detail_limit])
        out: list[tuple[MetadataItem, SourceRequest]] = []
        for index, (_, item, rec) in enumerate(entries):
            request = search_req
            if index in detail_for:
                detail_req = SourceRequest(NAME, "GET", f"{BASE}/galleries/{rec['id']}", label="detail",
                                           secret_headers=self._headers())
                try:
                    item = run.fetch(detail_req, parse_detail)
                    request = detail_req
                except (FetchError, ParseError) as e:
                    run.note(f"詳細を取得できなかったため検索結果の項目で返した: id={rec['id']} ({e})")
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


def _japanese(rec: dict) -> str | None:
    value = rec.get("japanese_title")
    return value if isinstance(value, str) and value.strip() else None


def parse_search(body: bytes) -> list[dict]:
    """検索の応答 → 一覧用の項目(辞書)の列。`id` と、題名のどちらかが無い項目は捨てる。"""
    data = _load(body)
    result = data.get("result")
    if not isinstance(result, list):
        raise ParseError("result が無い(検索の応答ではない)")
    return [r for r in result if isinstance(r, dict) and isinstance(r.get("id"), int)
            and (_japanese(r) or (isinstance(r.get("english_title"), str) and r["english_title"].strip()))]


def parse_detail(body: bytes) -> MetadataItem:
    data = _load(body)
    gallery_id, title = data.get("id"), data.get("title")
    if not isinstance(gallery_id, int) or not isinstance(title, dict):
        raise ParseError("id か title が無い(詳細の応答ではない)")
    japanese = title.get("japanese") if isinstance(title.get("japanese"), str) else None
    english = title.get("english") if isinstance(title.get("english"), str) else None
    if not (japanese or english):
        raise ParseError("題名が無い")
    tags = [t for t in data.get("tags") or [] if isinstance(t, dict) and isinstance(t.get("name"), str)]
    category = next((t["name"] for t in tags if t.get("type") == "category"), None)
    content_tags = tuple(t["name"] for t in tags if t.get("type") == "tag")
    item, _ = build_item(source=NAME, source_id=str(gallery_id), url=f"{SITE}/g/{gallery_id}/",
                         japanese=japanese, english=english, category=category, tags=content_tags)
    return item


def _item_from_list(rec: dict) -> tuple[MetadataItem, DoujinTitle]:
    return build_item(source=NAME, source_id=str(rec["id"]), url=f"{SITE}/g/{rec['id']}/",
                      japanese=_japanese(rec), english=rec.get("english_title"), category=None)
