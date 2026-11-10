"""Bangumi(bgm.tv)クライアント。作品単位の全巻数・著者・出版社。

キー不要。検索は巻ごとに別の項目になる旧 API(`GET /search/subject/{keyword}?type=1`。部分一致)で行い、
巻次の接尾辞(`(1)` など)を外して同じ作品名にまとめる。著者は、まとめた候補の確からしさの高い順に
上位だけ新 API(`GET /v0/subjects/{id}`)で詳細を引く(MangaUpdates と同じ構え)。

**タグ(簡体字中国語)は読み取らない。** `spinebox.ja_terms.is_japanese()` は仮名の有無で日本語を見分けるため、
仮名を含まない簡体字中国語の文を(漢字の字形が重なるので)日本語と誤認し、対応表に無い語を隠す既定の挙動が効かず、
中国語がそのまま `work_fields` に出てしまう(実データで確認、#278)。中国語を判別する手当は別 Issue に切り出した。
あらすじ(日本語で取れる)も読み取らない(#82 で廃止した方針を踏襲。`MetadataItem` に項目が無い)。

**巻次の接尾辞でまとめる都合上、同じ作品名の漫画版と原作小説版が 1 件に混ざることがある**
(実データで、ある作品の全巻数が実際の漫画の巻数より多く出た。原作小説の巻も同じ作品名でまとまったとみられる)。
NDL のように種別(漫画/小説)で割れて返るわけではないので、`kind` は検索結果だけでは分からず
詳細(`platform`)を引いた候補にだけ付く。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from urllib.parse import quote

from .base import MetadataSource, ParseError, _Run
from .http import FetchError, SourceRequest
from .match import score_item
from .model import KIND_MANGA, KIND_NOVEL, SCOPE_WORK, MetadataItem, SearchQuery

NAME = "bangumi"
BASE = "https://api.bgm.tv"
TYPE_BOOK = 1  # 書籍(漫画・小説・写真集などを含む。漫画かどうかは詳細の platform で判定する)

# 巻次の接尾辞(`(1)` `(7)`)と、どの巻にも付く定型のサブタイトル(`~…~`)を外して作品名にまとめる
_VOLUME_SUFFIX = re.compile(r"\(\d+\)")
_TILDE_SUFFIX = re.compile(r"~[^~]*~\s*$")

_KIND_BY_PLATFORM = {"漫画": KIND_MANGA, "小説": KIND_NOVEL, "小说": KIND_NOVEL, "轻小说": KIND_NOVEL}


@dataclass(frozen=True)
class Hit:
    subject_id: int
    name: str
    name_cn: str | None


class BangumiSource(MetadataSource):
    name = NAME

    def _search(self, query: SearchQuery, run: _Run):
        # 巻ごとに別項目で返るので、作品の候補数(candidate_limit)より多く取ってからまとめる
        raw_limit = min(max(self.settings.candidate_limit * 5, 20), 200)
        quoted = quote(query.title, safe="")
        search_req = SourceRequest(NAME, "GET", f"{BASE}/search/subject/{quoted}",
                                    (("type", str(TYPE_BOOK)), ("max_results", str(raw_limit))), label="search")
        hits = run.fetch(search_req, parse_search)
        drafts = _aggregate(hits)
        ranked = sorted(drafts, key=lambda item: score_item(query, item).score, reverse=True)
        top = ranked[: self.settings.candidate_limit]
        detail_for = {id(item) for item in top[: self.settings.detail_limit]}
        out: list[tuple[MetadataItem, SourceRequest]] = []
        for item in top:
            request = search_req
            if id(item) in detail_for:
                detail_req = SourceRequest(NAME, "GET", f"{BASE}/v0/subjects/{item.source_id}", label="detail")
                try:
                    item = run.fetch(detail_req, lambda body, base=item: parse_detail(body, base))
                    request = detail_req
                except (FetchError, ParseError) as e:
                    run.note(f"詳細を取得できなかったため検索結果の項目で返した: subject_id={item.source_id} ({e})")
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
    if data.get("error") or "list" not in data:
        # 0 件のときはエラー形で返る取得元(code 404 等)。見つからなかった = 成功した 0 件
        return []
    items = data.get("list")
    if not isinstance(items, list):
        raise ParseError("list が配列でない")
    hits = []
    for r in items:
        if isinstance(r, dict) and isinstance(r.get("id"), int) and r.get("name"):
            hits.append(Hit(r["id"], r["name"], r.get("name_cn") or None))
    return hits


def _base_name(name: str) -> str:
    base = _VOLUME_SUFFIX.sub("", name)
    base = _TILDE_SUFFIX.sub("", base)
    return base.strip()


def _aggregate(hits: list[Hit]) -> list[MetadataItem]:
    """代表の id: 番号の付かない項目(作品の代表の項目)があればそれを、無ければ最小の巻番号の項目を使う。"""
    groups: dict[str, dict] = {}
    order: list[str] = []
    for hit in hits:
        base = _base_name(hit.name)
        if not base:
            continue
        g = groups.setdefault(base, {"source_id": None, "rep_vol": None, "max_vol": None, "aliases": set()})
        if base not in order:
            order.append(base)
        m = re.search(r"\((\d+)\)", hit.name)
        vol = int(m.group(1)) if m else None
        if vol is not None and (g["max_vol"] is None or vol > g["max_vol"]):
            g["max_vol"] = vol
        if g["source_id"] is None or (vol is None and g["rep_vol"] is not None) \
                or (vol is not None and g["rep_vol"] is not None and vol < g["rep_vol"]):
            g["source_id"], g["rep_vol"] = hit.subject_id, vol
        if hit.name_cn and hit.name_cn != base:
            g["aliases"].add(hit.name_cn)
    items = []
    for base in order:
        g = groups[base]
        items.append(MetadataItem(
            source=NAME,
            source_id=str(g["source_id"]),
            scope=SCOPE_WORK,
            title=base,
            aliases=tuple(g["aliases"]),
            total_volumes=g["max_vol"],
            source_url=f"https://bgm.tv/subject/{g['source_id']}",
        ))
    return items


def parse_detail(body: bytes, base: MetadataItem) -> MetadataItem:
    """検索結果から作った項目(base)へ、詳細(著者・出版社・種別)を重ねる。タグ(簡体字中国語)は読み取らない。"""
    data = _load(body)
    if not isinstance(data.get("id"), int):
        raise ParseError("id が無い(詳細の応答ではない)")
    infobox = {e.get("key"): e.get("value") for e in (data.get("infobox") or [])
               if isinstance(e, dict) and isinstance(e.get("key"), str)}
    # 単純な単著は「作者」、作画・原作が分かれる作品は「作画」「原作」に分かれて入る(実データで確認)。
    # 値は単純な文字列のときだけ使う(別名の候補のように入れ子の配列で入ることがある)
    author_text = "/".join(infobox[k] for k in ("作者", "作画", "原作") if isinstance(infobox.get(k), str))
    authors = tuple(dict.fromkeys(a.strip() for a in re.split(r"[/、,]", author_text) if a.strip()))
    publisher = infobox["出版社"].strip() if isinstance(infobox.get("出版社"), str) else None
    platform = data.get("platform") if isinstance(data.get("platform"), str) else None
    return MetadataItem(
        source=base.source,
        source_id=base.source_id,
        scope=base.scope,
        title=base.title,
        aliases=base.aliases,
        authors=authors or base.authors,
        total_volumes=base.total_volumes,
        publisher=publisher or base.publisher,
        kind=_KIND_BY_PLATFORM.get(platform, base.kind),
        source_url=base.source_url,
    )
