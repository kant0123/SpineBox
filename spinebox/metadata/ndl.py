"""国立国会図書館サーチ API(OpenSearch)クライアント。巻ごとの書誌の主力。

SRU(dcndl の RDF)ではなく OpenSearch(RSS 2.0 の拡張)を使う理由は wiki/concepts/metadata-sources.md。
キー不要・無料(非営利)。同時要求数に制限があるので、要求は直列にして間隔を空ける。
"""
from __future__ import annotations

import re
import unicodedata
import xml.etree.ElementTree as ET

from .base import MetadataSource, ParseError, _Run
from .http import SourceRequest
from .model import KIND_MANGA, KIND_NOVEL, SCOPE_VOLUME, MetadataItem, SearchQuery

NAME = "ndl"
ENDPOINT = "https://ndlsearch.ndl.go.jp/api/opensearch"
MAX_COUNT = 500  # cnt の上限(501 件目以降は取れない)

_NS = {
    "dc": "http://purl.org/dc/elements/1.1/",
    "dcndl": "http://ndl.go.jp/dcndl/terms/",
    "dcterms": "http://purl.org/dc/terms/",
    "xsi": "http://www.w3.org/2001/XMLSchema-instance",
}
_XSI_TYPE = "{http://www.w3.org/2001/XMLSchema-instance}type"

_MANGA_LABEL = re.compile(r"コミック|comic|漫画|マンガ", re.IGNORECASE)
_NOVEL_LABEL = re.compile(r"novels?\b|ノベル|文庫|bunko", re.IGNORECASE)


class NdlSource(MetadataSource):
    name = NAME

    def _search(self, query: SearchQuery, run: _Run):
        limit = str(min(max(query.limit or self.settings.candidate_limit, 1), MAX_COUNT))
        if query.isbn:
            request = self._request([("isbn", _digits(query.isbn))], limit)
            return [(item, request) for item in run.fetch(request, parse_opensearch)]
        # 著者があれば creator で絞る(同名の別の本が大量に出る作品名でも巻が揃う)。
        # 絞り込みで 0 件なら、著者の表記が合わなかった可能性があるので著者なしで取り直す
        if query.author:
            request = self._request([("title", query.title), ("creator", query.author)], limit)
            items = run.fetch(request, parse_opensearch)
            if items:
                return [(item, request) for item in items]
            run.note("著者で絞ると 0 件だったため、著者なしで検索し直した")
        request = self._request([("title", query.title)], limit)
        return [(item, request) for item in run.fetch(request, parse_opensearch)]

    @staticmethod
    def _request(params: list[tuple[str, str]], limit: str) -> SourceRequest:
        # mediatype=books: 図書だけ(録音資料・映像資料・雑誌記事などを除く)
        return SourceRequest(NAME, "GET", ENDPOINT, (*params, ("mediatype", "books"), ("cnt", limit)),
                             label="search")


def parse_opensearch(body: bytes) -> list[MetadataItem]:
    """OpenSearch の RSS 応答を項目の列にする。不正な XML・RSS でない応答は ParseError。"""
    try:
        root = ET.fromstring(body)
    except ET.ParseError as e:
        raise ParseError(f"XML として不正({e.__class__.__name__})") from None
    if root.tag != "rss" or root.find("channel") is None:
        raise ParseError("OpenSearch の RSS ではない")
    return [item for item in (_parse_item(el) for el in root.iter("item")) if item is not None]


def _text(el: ET.Element, path: str) -> str | None:
    found = el.find(path, _NS)
    text = found.text.strip() if found is not None and found.text else ""
    return text or None


def _texts(el: ET.Element, path: str) -> list[str]:
    return [t for t in ((f.text or "").strip() for f in el.findall(path, _NS)) if t]


def _parse_item(el: ET.Element) -> MetadataItem | None:
    raw_title = _text(el, "dc:title") or _text(el, "title")
    link = _text(el, "link") or _text(el, "guid")
    if not raw_title or not link:
        return None
    title, subtitle = split_title(raw_title)
    identifiers = {(i.get(_XSI_TYPE) or "").split(":")[-1]: (i.text or "").strip()
                   for i in el.findall("dc:identifier", _NS)}
    ndc = next(((s.text or "").strip() for s in el.findall("dc:subject", _NS)
                if (s.get(_XSI_TYPE) or "").endswith("NDC10")), None)
    label = _text(el, "dcndl:seriesTitle")
    volume_label = _text(el, "dcndl:volume")
    reading = _text(el, "dcndl:titleTranscription")
    return MetadataItem(
        source=NAME,
        source_id=identifiers.get("NDLBibID") or link.rsplit("/", 1)[-1],
        scope=SCOPE_VOLUME,
        title=title,
        subtitle=subtitle,
        reading=reading,
        authors=tuple(dict.fromkeys(normalize_creator(c) for c in _texts(el, "dc:creator"))),
        volume=parse_volume(volume_label),
        volume_label=volume_label,
        publisher=_text(el, "dc:publisher"),
        label=label,
        released=parse_issued(_text(el, "dcterms:issued"), _text(el, "dc:date")),
        isbn=normalize_isbn(identifiers.get("ISBN")),
        kind=infer_kind(_text(el, "dcndl:genre"), ndc, label),
        source_url=link,
    )


def split_title(raw: str) -> tuple[str, str | None]:
    """`作品名 : 副題` を分ける。NDL は副題を ` : ` でつなぐ。"""
    main, sep, sub = raw.partition(" : ")
    main = main.strip()
    return (main, sub.strip() or None) if sep and main else (raw.strip(), None)


def normalize_creator(name: str) -> str:
    """`赤池, 宗` → `赤池宗`、`東村, アキコ, 1975-` → `東村アキコ`、`Smith, John` → `John Smith`。"""
    parts = [p.strip() for p in unicodedata.normalize("NFKC", name).split(",")]
    parts = [p for p in parts if p and not re.fullmatch(r"\d{3,4}\??-?\d{0,4}\??", p)]  # 生没年を除く
    if len(parts) < 2:
        return parts[0] if parts else name.strip()
    family, given = parts[0], parts[1]
    if all(ord(c) < 128 for c in family + given):
        return f"{given} {family}"
    return family + given


def parse_volume(label: str | None) -> int | None:
    """巻次の表記が数字だけのときに数値にする。`上` `1-2` `別冊` は None(label は残る)。"""
    if not label:
        return None
    text = unicodedata.normalize("NFKC", label).strip()
    return int(text) if re.fullmatch(r"\d{1,4}", text) else None


def parse_issued(issued: str | None, date: str | None) -> str | None:
    """`dcterms:issued`(`2022.2` `2021.9.10`)を `YYYY-MM[-DD]` にする。無ければ `dc:date` の年。"""
    for text in (issued, date):
        if not text:
            continue
        m = re.fullmatch(r"\s*(\d{4})(?:[.\-/](\d{1,2})(?:[.\-/](\d{1,2}))?)?\s*", text)
        if m:
            return "-".join([m.group(1), *(f"{int(g):02d}" for g in m.groups()[1:] if g)])
    return None


def _digits(isbn: str) -> str:
    return re.sub(r"[^0-9Xx]", "", isbn)


def normalize_isbn(isbn: str | None) -> str | None:
    if not isbn:
        return None
    digits = _digits(isbn).upper()
    return digits if len(digits) in (10, 13) else None


def infer_kind(genre: str | None, ndc: str | None, label: str | None) -> str | None:
    """漫画か小説かを、ジャンル・NDC・シリーズ名から推す。判断できなければ None(決めつけない)。"""
    if genre and "漫画" in genre:
        return KIND_MANGA
    if ndc:
        if ndc.startswith("726"):
            return KIND_MANGA
        if ndc.startswith("913"):
            return KIND_NOVEL
    if label:
        if _MANGA_LABEL.search(label):
            return KIND_MANGA
        if _NOVEL_LABEL.search(label):
            return KIND_NOVEL
    return None
