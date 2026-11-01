"""AniList(GraphQL)クライアント。作品単位のタグ・あらすじ・ローマ字名・別名。

キー不要。GraphQL のエンドポイントは 1 つで、検索は POST。レート制限は 90 回/分(障害時は 30 回/分に下がる)、
超えると 429 と Retry-After。取得結果は有効期限つきで保存し、保存済みを使い回す(利用規約が
「バックアップ・データ置き場としての利用」「大量収集」を禁じるため、永続的な写しにしない)。
"""
from __future__ import annotations

import html
import json
import re

from .base import MetadataSource, ParseError, _Run
from .http import SourceRequest
from .model import KIND_MANGA, KIND_NOVEL, SCOPE_WORK, MetadataItem, SearchQuery

NAME = "anilist"
ENDPOINT = "https://graphql.anilist.co"

QUERY = """
query ($search: String, $perPage: Int) {
  Page(page: 1, perPage: $perPage) {
    media(search: $search, type: MANGA, sort: SEARCH_MATCH) {
      id
      siteUrl
      format
      title { romaji english native }
      synonyms
      genres
      tags { name rank isMediaSpoiler isGeneralSpoiler }
      description(asHtml: false)
      startDate { year month day }
      volumes
      staff(perPage: 8, sort: RELEVANCE) { edges { role node { name { full native } } } }
    }
  }
}
""".strip()

# 作者として拾うスタッフの役割(`Story & Art` `Story` `Art` `Original Creator` `Illustration`)
_AUTHOR_ROLE = re.compile(r"story|art\b|original creator|illustration", re.IGNORECASE)
# 取り込むタグの順位(0〜100 の支持率)。低いものは作品の内容として怪しい
TAG_MIN_RANK = 40
TAG_LIMIT = 20


class AniListSource(MetadataSource):
    name = NAME

    def _search(self, query: SearchQuery, run: _Run):
        body = {"query": QUERY, "variables": {"search": query.title, "perPage": self.settings.candidate_limit}}
        request = SourceRequest(NAME, "POST", ENDPOINT, json_body=body, label="search")
        items = run.fetch(request, parse_search)
        return [(item, request) for item in items]


def parse_search(body: bytes) -> list[MetadataItem]:
    try:
        data = json.loads(body)
    except ValueError:
        raise ParseError("JSON として不正") from None
    if not isinstance(data, dict):
        raise ParseError("想定した JSON の形ではない")
    if data.get("errors"):
        # GraphQL のエラーは 200 で返ることがある。内容(メッセージ)は残さない
        raise ParseError("GraphQL がエラーを返した")
    try:
        media = data["data"]["Page"]["media"]
    except (KeyError, TypeError):
        raise ParseError("data.Page.media が無い") from None
    if not isinstance(media, list):
        raise ParseError("data.Page.media が配列でない")
    return [item for item in (_parse_media(m) for m in media if isinstance(m, dict)) if item is not None]


def _parse_media(m: dict) -> MetadataItem | None:
    media_id = m.get("id")
    titles = m.get("title") or {}
    native, romaji, english = titles.get("native"), titles.get("romaji"), titles.get("english")
    title = native or romaji or english
    if media_id is None or not title:
        return None
    others = [t for t in (romaji, english, *(m.get("synonyms") or [])) if isinstance(t, str) and t]
    aliases = tuple(dict.fromkeys(t for t in others if t != title and t != romaji))
    start = m.get("startDate") or {}
    return MetadataItem(
        source=NAME,
        source_id=str(media_id),
        scope=SCOPE_WORK,
        title=title,
        romaji=romaji if romaji and romaji != title else None,
        aliases=aliases,
        authors=_authors(m.get("staff")),
        total_volumes=m.get("volumes") if isinstance(m.get("volumes"), int) else None,
        released=_date(start),
        kind=KIND_NOVEL if m.get("format") == "NOVEL" else KIND_MANGA if m.get("format") else None,
        genres=tuple(g for g in (m.get("genres") or []) if isinstance(g, str)),
        tags=_tags(m.get("tags")),
        synopsis=clean_text(m.get("description")),
        source_url=m.get("siteUrl"),
    )


def _authors(staff: object) -> tuple[str, ...]:
    edges = (staff or {}).get("edges") if isinstance(staff, dict) else None
    names: list[str] = []
    for edge in edges or []:
        if not isinstance(edge, dict) or not _AUTHOR_ROLE.search(edge.get("role") or ""):
            continue
        name = ((edge.get("node") or {}).get("name") or {})
        text = name.get("native") or name.get("full")
        if text:
            names.append(text)
    return tuple(dict.fromkeys(names))


def _tags(tags: object) -> tuple[str, ...]:
    picked = [t for t in (tags or []) if isinstance(t, dict) and t.get("name")
              and not t.get("isMediaSpoiler") and not t.get("isGeneralSpoiler")
              and isinstance(t.get("rank"), int) and t["rank"] >= TAG_MIN_RANK]
    picked.sort(key=lambda t: t["rank"], reverse=True)
    return tuple(t["name"] for t in picked[:TAG_LIMIT])


def _date(d: dict) -> str | None:
    year, month, day = d.get("year"), d.get("month"), d.get("day")
    if not isinstance(year, int):
        return None
    out = f"{year:04d}"
    if isinstance(month, int):
        out += f"-{month:02d}"
        if isinstance(day, int):
            out += f"-{day:02d}"
    return out


def clean_text(text: object) -> str | None:
    """あらすじから HTML のタグ・実体参照を除く(改行の `<br>` は改行にする)。"""
    if not isinstance(text, str):
        return None
    text = re.sub(r"<\s*br\s*/?\s*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text).replace("\r\n", "\n").strip()
    return text or None
