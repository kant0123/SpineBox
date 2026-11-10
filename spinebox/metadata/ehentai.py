"""E-Hentai クライアント。同人誌・漫画の題名(日本語を含む)・サークル・作者・原作・タグ。

E-Hentai の公式 API(`https://api.e-hentai.org/api.php`)は、ギャラリーの ID とトークンを渡して詳細を引く
`gdata` だけで、**検索 API は無い**。そのため検索は検索ページ(`https://e-hentai.org/?f_search=…`)の HTML から
ギャラリーへのリンク(`/g/{gid}/{token}/`)を拾い、その組をまとめて `gdata` に渡す(1 要求 25 件まで)。
HTML の見た目(表示モード)には頼らず、リンクの形だけを見る。公式ドキュメントのレート制限は
「4〜5 回連続で送ると 5 秒ほど待つ」程度の記述で、守らないと IP が一時的に遮断される。
題名は同人誌の書式で、`doujin.py` で分けて使う。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

from .base import MetadataSource, ParseError, _Run
from .doujin import build_item, dedupe_key, is_translation, search_queries
from .http import MAX_RATE_LIMIT_BLOCK, FetchError, SourceRequest
from .model import FAIL_RATE_LIMITED, Failure, MetadataItem, SearchQuery

NAME = "ehentai"
SEARCH_URL = "https://e-hentai.org/"
API_URL = "https://api.e-hentai.org/api.php"
GDATA_LIMIT = 25  # 1 回の gdata に渡せる件数の上限(公式ドキュメント)

_GALLERY_LINK = re.compile(r"/g/(\d+)/([0-9a-f]{10})/")
_NO_HITS = "No hits found"
_BAN_MARKERS = ("temporarily banned", "ban expires", "IP address has been")

# 内容の説明として残す名前空間(人物・作品・言語などは別に扱う)
_CONTENT_NAMESPACES = ("female", "male", "mixed", "other")
CONTENT_TAG_LIMIT = 20


class BannedError(ParseError):
    """検索ページが IP の一時遮断の案内だった。"""


@dataclass(frozen=True)
class Gallery:
    gid: int
    token: str
    item: MetadataItem
    translated: bool
    has_japanese: bool


class EHentaiSource(MetadataSource):
    name = NAME

    def _search(self, query: SearchQuery, run: _Run):
        refs: list[tuple[int, str]] = []
        for keywords in search_queries(query.title, query.author, fallback=not query.quick):
            search_req = SourceRequest(NAME, "GET", SEARCH_URL, params=(("f_search", keywords),), label="search")
            try:
                refs = run.fetch(search_req, parse_search_page)
            except BannedError:
                # 遮断されている間に送り続けると長引くので、しばらく要求を出さない
                self.fetcher.gate(NAME).block(MAX_RATE_LIMIT_BLOCK)
                raise FetchError(Failure(FAIL_RATE_LIMITED, "E-Hentai に IP を一時的に遮断されている可能性がある",
                                         retry_after=MAX_RATE_LIMIT_BLOCK)) from None
            if refs:
                break
        refs = refs[: min(self.settings.candidate_limit, GDATA_LIMIT)]
        if not refs:
            return []

        gdata_req = SourceRequest(NAME, "POST", API_URL, label="detail", json_body={
            "method": "gdata", "gidlist": [[gid, token] for gid, token in refs], "namespace": 1})
        galleries = run.fetch(gdata_req, parse_gdata)

        # 同じ本の翻訳版・再アップロードを 1 件にまとめる。日本語の原本 → 翻訳でない → 古い ID の順に残す
        best: dict[str, Gallery] = {}
        merged = 0
        for g in galleries:
            key = dedupe_key(g.item)
            if key in best:
                merged += 1
                if _rank(g) >= _rank(best[key]):
                    continue
            best[key] = g
        if merged:
            run.note(f"同じ本の翻訳版・再アップロード {merged} 件を 1 件にまとめた")
        return [(g.item, gdata_req) for g in best.values()]


def _rank(g: Gallery) -> tuple:
    return (g.translated, not g.has_japanese, g.gid)


def parse_search_page(body: bytes) -> list[tuple[int, str]]:
    """検索ページ(HTML)→ (gid, token) の列(出てきた順・重複なし)。0 件の案内は空の列、検索ページでなければ ParseError。"""
    text = body.decode("utf-8", errors="replace")
    pairs: list[tuple[int, str]] = []
    seen: set[int] = set()
    for m in _GALLERY_LINK.finditer(text):
        gid = int(m.group(1))
        if gid not in seen:
            seen.add(gid)
            pairs.append((gid, m.group(2)))
    if pairs:
        return pairs
    if any(marker in text for marker in _BAN_MARKERS):
        raise BannedError("検索ページが遮断の案内だった")
    if _NO_HITS in text:
        return []
    raise ParseError("検索ページではない(ギャラリーへのリンクも 0 件の案内も無い)")


def parse_gdata(body: bytes) -> list[Gallery]:
    """gdata の応答 → ギャラリーの列。個別のエラー(トークン違い)と削除済み(expunged)は捨てる。"""
    try:
        data = json.loads(body)
    except ValueError:
        raise ParseError("JSON として不正") from None
    if not isinstance(data, dict):
        raise ParseError("想定した JSON の形ではない")
    if isinstance(data.get("error"), str):
        raise ParseError("API がエラーを返した")
    records = data.get("gmetadata")
    if not isinstance(records, list):
        raise ParseError("gmetadata が無い(gdata の応答ではない)")
    out: list[Gallery] = []
    for r in records:
        if not isinstance(r, dict) or "error" in r or r.get("expunged") is True:
            continue
        gid, token = r.get("gid"), r.get("token")
        if not isinstance(gid, int) or not isinstance(token, str):
            continue
        japanese = r.get("title_jpn") if isinstance(r.get("title_jpn"), str) and r["title_jpn"].strip() else None
        english = r.get("title") if isinstance(r.get("title"), str) and r["title"].strip() else None
        if not (japanese or english):
            continue
        raw_tags = [t for t in r.get("tags") or [] if isinstance(t, str)]
        languages, content = _split_tags(raw_tags)
        item, base = build_item(
            source=NAME, source_id=f"{gid}/{token}", url=f"https://e-hentai.org/g/{gid}/{token}/",
            japanese=japanese, english=english, category=r.get("category") if isinstance(r.get("category"), str) else None,
            tags=content)
        out.append(Gallery(gid, token, item, is_translation(base.marks, languages), japanese is not None))
    return out


def _split_tags(tags: list[str]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """`namespace:value` のタグ(gdata の `namespace=1`)→ (言語の値, 内容のタグの値)。"""
    languages: list[str] = []
    content: list[str] = []
    for tag in tags:
        ns, sep, value = tag.partition(":")
        if not sep:
            continue
        if ns == "language":
            languages.append(value)
        elif ns in _CONTENT_NAMESPACES and value:
            content.append(value)
    return tuple(languages), tuple(content[:CONTENT_TAG_LIMIT])

