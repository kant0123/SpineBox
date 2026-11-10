"""メディア芸術データベース(文化庁)クライアント。作品単位の全巻数・著者・出版社。

新エンドポイント `https://mediaarts-db.artmuseums.go.jp/sparql`(旧 `mediaarts-db.bunka.go.jp` は
証明書が切れて接続不可、2026-11-10 に確認)。キー不要・CC BY。

`rdfs:label` の全件スキャン(`FILTER(CONTAINS(...))`)は 1 クエリ 3.6 秒かかるため、Neptune の全文検索
(`SERVICE neptune-fts:search`)でまず絞り込む。`FILTER(CONTAINS(...))` も残し、全文検索の表記ゆれ
(読みでの一致など)で混ざる無関係な結果を取り除く。

**作品(シリーズ)の実体(`class:MangaBookSeries`)は、すべての作品に登録されているわけではない**
(実通信で確認。「お気楽領主の楽しい領地防衛」にはシリーズの実体が無く、巻(`class:MangaBook`)だけ
登録されていた)。そのためシリーズの実体があれば `schema:numberOfItems`(確定の全巻数)を使い、
無ければ検索で見つかった巻(`schema:volumeNumber`)の最大値を全巻数の下限として使う
(全文検索の件数上限に収まらない巻がある大作では過少になりうる。ベストエフォート)。

巻・シリーズには `schema:name` が読み(`ja-hrkt`、カタカナ)つきでも入っている。作品名の候補としては
除く(除かないと同じ巻が読みの違いだけの別候補として二重に出る、実データで確認)が、`MetadataItem.reading`
には別途入れる。NDL と同じ経路(`spinebox/metadata/match.py score_item()` が読みをローマ字にして比べる)で、
ローマ字の検索語(ファイル名が英字の蔵書)でも一致させるため。
"""
from __future__ import annotations

import json

from .base import MetadataSource, ParseError, _Run
from .http import SourceRequest
from .model import KIND_MANGA, SCOPE_WORK, MetadataItem, SearchQuery

NAME = "mediaarts"
ENDPOINT = "https://mediaarts-db.artmuseums.go.jp/sparql"
# Neptune の全文検索(Elasticsearch)のエンドポイント。SPARQL クエリの中の SERVICE 句が参照する
FTS_ENDPOINT = "https://vpc-mediaarts-db-qaymrmtqbprlhmqq33a2ncf4ke.ap-northeast-1.es.amazonaws.com"

_NS = "https://mediaarts-db.artmuseums.go.jp/data/class#"
# 1 件の検索で見る生の行数(巻 + シリーズ)。作品の候補数(limit)よりかなり多く取り、同名の巻をまとめる
_RAW_ROWS = 300


class MediaArtsSource(MetadataSource):
    name = NAME

    def _search(self, query: SearchQuery, run: _Run):
        sparql = _build_query(query.title)
        request = SourceRequest(NAME, "GET", ENDPOINT, (("query", sparql), ("format", "json")), label="search")
        items = run.fetch(request, parse_search)
        return [(item, request) for item in items[: self.settings.candidate_limit]]


def _escape_fts_phrase(text: str) -> str:
    """Elasticsearch の query_string に渡すフレーズ(ダブルクォートで囲む)。"""
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _escape_sparql_single_quoted(text: str) -> str:
    """SPARQL のシングルクォート文字列として安全な形(改行は空白に畳む)。"""
    escaped = text.replace("\\", "\\\\").replace("'", "\\'").replace("\n", " ").replace("\r", " ")
    return f"'{escaped}'"


def _escape_sparql_double_quoted(text: str) -> str:
    """SPARQL のダブルクォート文字列として安全な形(改行は空白に畳む)。"""
    escaped = text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ").replace("\r", " ")
    return f'"{escaped}"'


def _build_query(title: str) -> str:
    fts_query = _escape_sparql_single_quoted(_escape_fts_phrase(title))
    contains = _escape_sparql_double_quoted(title)
    return f"""
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX schema: <https://schema.org/>
PREFIX class: <{_NS}>
PREFIX neptune-fts: <http://aws.amazon.com/neptune/vocab/v01/services/fts#>
SELECT ?s ?kind ?name ?reading ?creator ?publisher ?vol ?items WHERE {{
  SERVICE neptune-fts:search {{
    neptune-fts:config neptune-fts:endpoint "{FTS_ENDPOINT}" .
    neptune-fts:config neptune-fts:field rdfs:label .
    neptune-fts:config neptune-fts:queryType "query_string" .
    neptune-fts:config neptune-fts:query {fts_query} .
    neptune-fts:config neptune-fts:maxResults {_RAW_ROWS} .
    neptune-fts:config neptune-fts:return ?s .
  }}
  {{
    ?s a class:MangaBookSeries ; rdfs:label ?label .
    BIND(?label AS ?name)
    BIND("series" AS ?kind)
    OPTIONAL {{ ?s schema:numberOfItems ?items }}
  }} UNION {{
    ?s a class:MangaBook ; rdfs:label ?label ; schema:name ?name .
    FILTER(lang(?name) != "ja-hrkt")
    BIND("volume" AS ?kind)
    OPTIONAL {{ ?s schema:volumeNumber ?vol }}
  }}
  FILTER(CONTAINS(?label, {contains}))
  OPTIONAL {{ ?s schema:creator ?creator }}
  OPTIONAL {{ ?s schema:publisher ?publisher }}
  OPTIONAL {{ ?s schema:name ?reading . FILTER(lang(?reading) = "ja-hrkt") }}
}}
LIMIT {_RAW_ROWS}
""".strip()


def parse_search(body: bytes) -> list[MetadataItem]:
    try:
        data = json.loads(body)
    except ValueError:
        raise ParseError("JSON として不正") from None
    try:
        bindings = data["results"]["bindings"]
    except (KeyError, TypeError):
        raise ParseError("results.bindings が無い") from None
    if not isinstance(bindings, list):
        raise ParseError("results.bindings が配列でない")
    return _aggregate(bindings)


def _value(binding: dict, key: str) -> str | None:
    cell = binding.get(key) if isinstance(binding, dict) else None
    v = cell.get("value") if isinstance(cell, dict) else None
    return v if isinstance(v, str) and v else None


def _aggregate(bindings: list) -> list[MetadataItem]:
    """巻(`MangaBook`)の行を作品名でまとめ、シリーズ(`MangaBookSeries`)の行があればそちらの全巻数を使う。"""
    groups: dict[str, dict] = {}  # name → {series_items, max_vol, creator, publisher, reading, source_id, order}
    order: list[str] = []
    for b in bindings:
        if not isinstance(b, dict):
            continue
        name = _value(b, "name")
        if not name:
            continue
        g = groups.get(name)
        if g is None:
            g = {"series_items": None, "max_vol": None, "creator": None, "publisher": None, "reading": None,
                 "source_id": None, "source_url": None}
            groups[name] = g
            order.append(name)
        creator, publisher, reading = _value(b, "creator"), _value(b, "publisher"), _value(b, "reading")
        if creator and not g["creator"]:
            g["creator"] = creator
        if publisher and not g["publisher"]:
            g["publisher"] = publisher
        if reading and not g["reading"]:
            g["reading"] = reading
        if _value(b, "kind") == "series":
            uri = _value(b, "s")
            items_raw = _value(b, "items")
            if items_raw and items_raw.isdigit() and int(items_raw) > 0:
                g["series_items"] = int(items_raw)
            if uri:
                g["source_id"], g["source_url"] = uri.rsplit("/", 1)[-1], uri
        else:
            vol_raw = _value(b, "vol")
            if vol_raw and vol_raw.replace(".", "", 1).isdigit():
                vol = int(float(vol_raw))
                if g["max_vol"] is None or vol > g["max_vol"]:
                    g["max_vol"] = vol
            uri = _value(b, "s")
            if uri and g["source_id"] is None:
                g["source_id"], g["source_url"] = uri.rsplit("/", 1)[-1], uri

    items = []
    for name in order:
        g = groups[name]
        if g["source_id"] is None:
            continue
        total_volumes = g["series_items"] if g["series_items"] is not None else g["max_vol"]
        items.append(MetadataItem(
            source=NAME,
            source_id=g["source_id"],
            scope=SCOPE_WORK,
            title=name,
            reading=g["reading"],
            authors=_split_agents(g["creator"]),
            total_volumes=total_volumes,
            publisher=_primary_reading(g["publisher"]),
            kind=KIND_MANGA,  # この取得元(マンガ単行本・シリーズ)は漫画のみ
            source_url=g["source_url"],
        ))
    return items


def _split_agents(value: str | None) -> tuple[str, ...]:
    """`[著]東村アキコ` のような役割つきの表記から人名を拾う(`∥` で複数が並ぶこともある)。"""
    if not value:
        return ()
    names = []
    for part in value.split("∥"):
        name = part.strip()
        name = name.split("]", 1)[1].strip() if name.startswith("[") and "]" in name else name
        if name:
            names.append(name)
    return tuple(dict.fromkeys(names))


def _primary_reading(value: str | None) -> str | None:
    """`集英社　∥　シュウエイシャ` のように漢字表記とカタカナ読みが並ぶ。漢字表記(先頭)を使う。"""
    if not value:
        return None
    primary = value.split("∥", 1)[0].strip()
    return primary or None
