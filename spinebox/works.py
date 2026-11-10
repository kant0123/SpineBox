"""作品の名寄せ(GOAL.md フェーズ 3)。巻を作品にまとめ、別名・著者・統合の根拠を持つ。

- 作品(`works`)は**派生**で、巻の名前から作る。名前は候補なので(CLAUDE.md「蔵書を扱う不変条件」)、
  作品の項目(`work_fields`)・別名(`work_aliases`)・巻の割り当て(`work_volumes`)は、出所・確信度・
  根拠・「手動か」を 1 件ごとに持つ。手動のものは再計算(`sync`)で上書きしない。外部ソース(公開 DB)は
  空欄と別名だけを足す
- 割り当て: 巻の**自分の名前**(巻の名前 → 入れ子の名前 → 外側のアーカイブ・フォルダの名前の順で最初に作品名と読めたもの)
  の正規化が、既存の作品の名前(作品名か別名)と一致すればその作品へ、無ければ新しい作品へ。
  `作品名 01` `作品名 02` のように**同じ名前で末尾の番号だけが違う巻が 2 つ以上**あるときは、
  末尾の番号を巻番号として読む(`_trailing_series`。名前だけでは決めない)
- 統合: 別々の作品が同じ本と言える強い根拠があれば**統合の提案**を出す。確信度が高く、手動の決定が絡まないものだけ自動で統合し、
  残りは確認待ち(`open`)。根拠は (1) 別スキャンの重複候補(spinebox/duplicates.py。却下されたものは使わない)、
  (2) 別名と作品名の一致。却下した提案・利用者が分けた組は再計算で戻さない
- 蔵書には触れない。スキャンも内側アーカイブを取り出さない(名前の解析と DB の更新だけ)
"""
from __future__ import annotations

import json
import logging
import random
import re
import sqlite3
import unicodedata
from collections import defaultdict
from difflib import SequenceMatcher
from dataclasses import dataclass, field, replace

from . import db, ja_terms, search
from .duplicates import candidate_counts
from .library import VolumeNotFound, is_offline
from .mojibake import looks_garbled
from .naming import (JUNK_NAMES, WorkName, is_chapter_label, is_fragment_name, is_junk_name, normalize_title,
                     parse_number, parse_work_name, trailing_number)
from .pages import natural_key
from .scanner import LISTED_VOLUMES_JOIN, LISTED_VOLUMES_WHERE

log = logging.getLogger(__name__)

FIELD_TITLE = "title"
FIELD_AUTHOR = "author"
FIELD_KIND = "kind"                # manga / novel / doujinshi
FIELD_PUBLISHER = "publisher"
FIELD_GENRES = "genres"            # JSON の文字列配列
FIELD_TAGS = "tags"                # JSON の文字列配列
FIELD_TOTAL_VOLUMES = "total_volumes"  # 全何巻か(公開 DB だけが持つ。手で編集する項目ではない)
EDITABLE_FIELDS = (FIELD_TITLE, FIELD_AUTHOR, FIELD_KIND, FIELD_PUBLISHER, FIELD_GENRES, FIELD_TAGS)
# 廃止した項目(あらすじ。#82)。既にある行は消さずに残すが、API・検索・編集・統合・「手動が絡むか」の判定には使わない
RETIRED_FIELDS = ("synopsis",)
LIST_FIELDS = (FIELD_GENRES, FIELD_TAGS)
KINDS = ("manga", "novel", "doujinshi")

# 巻単位の書誌(`volume_fields`)。手で直せるのは publisher / label / released / isbn
VOLUME_EDITABLE_FIELDS = ("publisher", "label", "released", "isbn")
VOLUME_FIELDS = (*VOLUME_EDITABLE_FIELDS, "volume_label", "subtitle")

# 取得元の表示名(取得元の順は結線の検索の順)
SOURCE_LABELS = {"ndl": "国立国会図書館サーチ", "mediaarts": "メディア芸術データベース", "bangumi": "Bangumi",
                 "nhentai": "nHentai", "ehentai": "E-Hentai"}

# 出所(source)。work_fields / work_aliases / work_volumes / volume_fields で共通。公開 DB は `public_db`(結線は spinebox/linking.py)
SOURCE_FILENAME = "filename"      # 巻自身の名前から
SOURCE_CONTAINER = "container"    # 外側のアーカイブ・フォルダの名前から
SOURCE_DUPLICATE = "duplicate"    # 重複候補の証拠で統合した
SOURCE_ALIAS = "alias"            # 別名と作品名の一致で統合した
SOURCE_MERGE = "merge"            # 統合された作品の名前(別名になった)
SOURCE_MANUAL = "manual"          # 利用者が決めた
SOURCE_PREVIOUS = "previous_title"  # 手で直す前の作品名(別名に残す)
SOURCE_PUBLIC_DB = "public_db"    # 公開 DB(国立国会図書館サーチ・メディア芸術データベース・Bangumi)から

# 確信度(0〜1)。しきい値の根拠は wiki/concepts/work-grouping.md
CONF_OWN_NAME = 0.9          # 巻自身の名前から作品名を読めた
CONF_ENCLOSING_NAME = 0.6    # 巻自身の名前に作品名が無く、外側の名前から読んだ
CONF_CONTAINER_ALIAS = 0.5   # 作品の全部の巻を含む外側のアーカイブの名前(別名・著者の候補)
CONF_CONTAINER_CLAIM = 0.8   # 1 作品だけを収めた外側のフォルダ・アーカイブの名前で割り当てた(`_container_claims`)
AUTO_MERGE_MIN = 0.9         # この確信度以上で、手動の決定が絡まなければ自動で統合する
PROPOSE_MIN = 0.5            # これ未満は提案にも出さない
VISUAL_ONLY_CAP = 0.85       # 見た目だけ(巻番号が不明)の重複候補は自動統合しない
MAX_MISSING_SPAN = 500       # 巻番号の最大がこれを超えたら欠けを数えない(名前の誤読で何千件も出さない)
SAMPLE_VOLUMES = 6           # 提案の画面に出す、作品ごとの巻の例

AUTHOR_SEPARATOR = "、"

# 作品一覧の並び順(#273)
WORK_SORTS = ("title", "recent_read", "added_desc", "added_asc", "released_desc", "released_asc", "random")


class WorkNotFound(LookupError):
    pass


class ProposalNotFound(LookupError):
    pass


class Conflict(RuntimeError):
    """今の状態では行えない操作(統合済み・同じ作品など)。メッセージは利用者向け。"""


class InvalidValue(ValueError):
    """値が使えない(空の作品名など)。メッセージは利用者向け。"""


# --- 名前の連鎖 ------------------------------------------------------------------------


def _segments(path: str | None) -> list[str]:
    return [s for s in (path or "").replace("\\", "/").split("/") if s]


def name_chain(row) -> list[str]:
    """巻の名前を内側から外側へ並べる。先頭が巻自身の名前。

    巻の名前 → アーカイブ内のフォルダ → 入れ子のアーカイブ(分解した子の親側)→ 内側アーカイブの名前 →
    外側のアーカイブ・フォルダと、その上のフォルダ。
    """
    names = [row["title"]]
    names += reversed(_segments(row["entry_prefix"]))
    try:
        inner = json.loads(row["inner_path"]) if row["inner_path"] else []
    except ValueError:
        inner = []
    names += reversed([str(x) for x in inner])
    names += reversed(_segments(row["container_path"]))
    names += reversed(_segments(row["rel_path"]))
    return names


@dataclass(frozen=True)
class OwnName:
    """巻の作品名の候補。level 0 = 巻自身の名前、1 以上 = 外側の名前から読んだ。"""
    name: object  # WorkName
    level: int
    text: str
    container: bool = False  # 1 作品だけを収めた外側の名前で決めた(`_container_claims`)
    romaji: tuple[str, ...] = ()  # その入れ物の中(と入れ物自身)のローマ字の名前。作品の別名の候補

    @property
    def confidence(self) -> float:
        if self.container:
            return CONF_CONTAINER_CLAIM
        return CONF_OWN_NAME if self.level == 0 else CONF_ENCLOSING_NAME

    def evidence(self) -> dict:
        level = "container" if self.container else ("volume" if self.level == 0 else "enclosing")
        return {"name": self.text, "level": level, **self.name.as_dict()}


def own_name(row, series: dict | None = None) -> OwnName | None:
    """巻の作品名の候補。`series` を渡すと、末尾の番号まで読んだ作品名を優先する(`_trailing_series`)。"""
    if series is not None:
        wn = series["work_name"]
        # 番号を読んだ根拠を parts に残す(画面で「名前の中のどこを番号と読んだか」を見せる)
        return OwnName(replace(wn, parts=wn.parts + (("number", series["evidence"]),)), 0, row["title"])
    for level, text in enumerate(name_chain(row)):
        wn = parse_work_name(text)
        if wn is not None:
            return OwnName(wn, level, text)
    return None


def _has_cjk(text: str) -> bool:
    return any("぀" <= ch <= "ヿ" or "一" <= ch <= "鿿" for ch in text)


def _dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True)


# --- メモリ上の状態(sync 用) ---------------------------------------------------------------


@dataclass
class _Name:
    display: str
    source: str
    confidence: float
    is_title: bool


@dataclass
class _Work:
    id: int
    title: str = ""
    title_manual: bool = False
    title_source: str = SOURCE_FILENAME
    author: str | None = None
    author_manual: bool = False
    names: dict[str, _Name] = field(default_factory=dict)
    manual_volumes: int = 0


class _State:
    def __init__(self, c: sqlite3.Connection):
        self.vols = c.execute(
            "SELECT v.id, v.title, v.container_path, v.entry_prefix, v.inner_path, v.parent_id, "
            "v.number_kind, v.number_start, v.number_end, v.number_evidence, "
            "s.id AS source_id, s.rel_path " + LISTED_VOLUMES_JOIN + LISTED_VOLUMES_WHERE + " ORDER BY v.id").fetchall()
        self.listed = {r["id"] for r in self.vols}
        # EPUB の OPF の dc:creator(巻の書誌 author。名前に著者が無いときの候補。#251)
        self.creators: dict[int, str] = {r["volume_id"]: r["value"] for r in c.execute(
            "SELECT volume_id, value FROM volume_fields WHERE field = 'author' AND source = 'epub' AND value <> ''")}
        self.works: dict[int, _Work] = {}
        for r in c.execute("SELECT id FROM works WHERE merged_into IS NULL"):
            self.works[r["id"]] = _Work(r["id"])
        for r in c.execute("SELECT * FROM work_fields"):
            w = self.works.get(r["work_id"])
            if w is None:
                continue
            if r["field"] == FIELD_TITLE:
                w.title, w.title_manual, w.title_source = r["value"] or "", bool(r["manual"]), r["source"]
                norm = normalize_title(w.title)
                if norm and (w.title_manual or not is_junk_name(norm)):
                    w.names[norm] = _Name(w.title, r["source"], r["confidence"] or CONF_OWN_NAME, True)
            elif r["field"] == FIELD_AUTHOR:
                w.author, w.author_manual = r["value"] or None, bool(r["manual"])
        for r in c.execute("SELECT * FROM work_aliases WHERE dismissed_at IS NULL ORDER BY id"):
            w = self.works.get(r["work_id"])
            if w is not None and r["norm"] not in w.names and (r["manual"] or not is_junk_name(r["norm"])):
                w.names[r["norm"]] = _Name(r["alias"], r["source"], r["confidence"] or CONF_CONTAINER_ALIAS, False)
        self.assign: dict[int, dict] = {}
        for r in c.execute("SELECT * FROM work_volumes"):
            self.assign[r["volume_id"]] = dict(r)
            w = self.works.get(r["work_id"])
            if w is not None and r["manual"]:
                w.manual_volumes += 1
        self.by_norm: dict[str, list[int]] = defaultdict(list)
        for w in self.works.values():
            for norm, n in w.names.items():
                self._add_index(w.id, norm, n)
        # 同じ公開 DB 項目に linked した作品どうしの組(統合の確信度の補い用)
        self.links: dict[int, set[tuple[str, str]]] = defaultdict(set)
        for r in c.execute("""SELECT work_id, source, source_id FROM work_links
                              WHERE status = 'linked' AND source_id IS NOT NULL AND source_id <> ''"""):
            self.links[r["work_id"]].add((r["source"], r["source_id"]))

    def _add_index(self, work_id: int, norm: str, n: _Name) -> None:
        # 外側のアーカイブ名から付けた別名は、巻の割り当てには使わない(提案の根拠にだけ使う)
        if n.source != SOURCE_CONTAINER and work_id not in self.by_norm[norm]:
            self.by_norm[norm].append(work_id)

    def find_work(self, norm: str) -> int | None:
        cands = [i for i in self.by_norm.get(norm, ()) if i in self.works]
        if not cands:
            return None
        cands.sort(key=lambda i: (not self.works[i].names[norm].is_title, i))
        return cands[0]


# --- 1 作品だけを収めた外側の名前(フォルダ・アーカイブ)による割り当て ------------------------------
#
# 蔵書は「作品名のフォルダ(多くは日本語)の下に、配布元ごとにばらばらの名前(ローマ字・日本語・章の見出し)の
# 巻が並ぶ」形が多い。巻の名前だけで作品を決めると、ローマ字の巻・日本語の巻・章のフォルダが別々の作品に
# 割れる(実データ 2026-11-09: 16 のフォルダ・アーカイブが 292 作品になった)。そこで、外側の名前のうち
# **中に 1 つの作品しか入っていない**と言えるものを、その下の巻の作品名にする。
#
# 「1 つの作品しか入っていない」の判定(外側の名前を N とする):
# - 下の巻ごとに、巻 → N の手前までの名前で最初に作品名と読めた名前(系列名)を取る。章の見出し(`第4話 …`)・
#   断片(`CONTENTAS`)・番号だけ(`17` `03s`)・保存ページのフォルダ(`16_files`)は系列名にしない
#   (どこにも無ければ「名前の無い巻」)
# - N が日本語: 系列名が N と同じか `N + 番号` `N + …編` なら N の巻。N と違う日本語の系列が 2 つ以上あれば
#   N はまとめのフォルダ(`小説`)なので何もしない。違う日本語の系列が無く、ローマ字の系列が 1 つだけ
#   (または N の名前の巻が 1 つでもある)なら、ローマ字の巻も N の巻(ローマ字と日本語は字面で結べないため、
#   入れ物の中で他に候補が無いことを根拠にする)
# - N がローマ字: 日本語の系列がちょうど 1 つで、N と違うローマ字の系列が無ければ、全部をその日本語の作品にする
# - ローマ字の綴りの揺れ(`Sudachi no mao-jo` と `Sudachi_no_Maoujou`)は 1 つの系列と数える(`_romaji_variant`)。
#   巻 1 冊の入れ物の名前(`動物人間 13.zip`)は末尾の番号を落として数える(`_without_trailing_number`。#263)
# 巻ごとに、当てはまる外側の名前のうち日本語の作品名になる最も内側のものを採る(無ければ最も外側)。

_CATEGORY_NAMES = frozenset(normalize_title(x) for x in (
    "小説", "ライトノベル", "ラノベ", "漫画", "マンガ", "コミック", "コミックス", "技術本", "技術書", "本", "書籍",
    "雑誌", "画集", "写真集", "同人誌", "まとめ", "その他", "未分類", "novel", "novels", "lightnovel", "comic",
    "comics", "book", "books", "magazine", "magazines", "misc", "others", "library",
))
# ローマ字の系列名の末尾に付く配布の印(`… raw v03` `… jp v08s`)。系列の突き合わせでだけ落とす
_ROMAJI_NOISE = frozenset({"raw", "raws", "jp", "jpn", "jap", "dl", "fix", "fixed", "digital", "end", "complete",
                           "hq", "hd", "s", "b", "w", "zip", "rar", "jpg", "color", "colour", "mono"})
_SAVED_PAGE = re.compile(r"[_\s]files$", re.IGNORECASE)  # ブラウザの「ページを保存」のフォルダ(`16_files`)
_SUFFIX_OK = re.compile(r"^(?:\d.*|.{0,8}[編篇]|[上中下]|[上中下]巻)$")


def _path_parts(r) -> list[str]:
    """巻の場所を外側から並べる(蔵書ルートからのフォルダ → アーカイブ → 入れ子 → アーカイブ内のフォルダ)。
    最後の要素が巻そのもの。"""
    try:
        inner = json.loads(r["inner_path"]) if r["inner_path"] else []
    except ValueError:
        inner = []
    return (_segments(r["rel_path"]) + _segments(r["container_path"]) + [str(x) for x in inner]
            + _segments(r["entry_prefix"]))


def _series_name(text: str) -> WorkName | None:
    """作品名(系列名)として数える名前。章の見出し・断片・保存ページのフォルダは数えない。"""
    if not text or is_chapter_label(text) or is_fragment_name(text) or _SAVED_PAGE.search(text.strip()):
        return None
    return parse_work_name(text)


def _without_trailing_number(wn: WorkName) -> WorkName:
    """`動物人間 13` `黄泉のツガイ 1` `…(4)` のような名前は、末尾の番号を落とした系列名で数える。
    巻 1 冊を収めたアーカイブ・フォルダの名前で、番号まで作品名にしない。"""
    t = trailing_number(wn.title)
    if t is None:
        return wn
    base = parse_work_name(t.base)
    return base if base is not None and len(base.norm) >= 2 else wn


def _series_key(wn: WorkName) -> str:
    if _has_cjk(wn.title):
        return re.sub(r"(?<=[^a-z0-9])[a-z]$", "", wn.norm)  # 末尾の版の印(`…件c`)
    words = [w for w in re.split(r"[\s_.\-]+", unicodedata.normalize("NFKC", wn.title).casefold()) if w]
    while len(words) > 1 and words[-1] in _ROMAJI_NOISE:
        words.pop()
    return normalize_title(" ".join(words)) or wn.norm




_TITLE_BREAK = re.compile(r"[\s~〜～\-－‐:：、,]+")


def _extends(short: str, long_key: str, long_title: str) -> bool:
    """long が short に番号・`…編`・区切りの後ろの副題を足しただけの名前か(`ゾンビ` と `ゾンビだらけの…` は別)。"""
    if len(short) < 3 or not long_key.startswith(short):
        return False
    if _SUFFIX_OK.match(long_key[len(short):]):
        return True
    pieces = _TITLE_BREAK.split(long_title)
    return any(normalize_title(" ".join(pieces[:i])) == short for i in range(1, len(pieces)))


ROMAJI_PREFIX_SHARE = 0.8   # ローマ字の系列名どうしで、短い方のこの割合以上が共通の頭なら綴りの揺れ
ROMAJI_SIMILARITY = 0.8     # または文字列の似ている度合い(SequenceMatcher.ratio)がこれ以上


def _romaji_variant(a: str, b: str) -> bool:
    """ローマ字の系列名どうしが同じ作品の綴りの揺れか(`sudachinomaojo` と `sudachinomaoujou`、
    `asokohatarakumusubu` と `asokodehatarakumusubuchan`、`…hajimattachito` と `…hajimattacheat…`)。
    1 つの入れ物の中だけで使う(`isekainonbirisozai…` と `isekainonbirinoka` のような別作品は頭の共通が短い)。"""
    if _has_cjk(a) or _has_cjk(b) or min(len(a), len(b)) < 6:
        return False
    prefix = 0
    for x, y in zip(a, b):
        if x != y:
            break
        prefix += 1
    return prefix >= ROMAJI_PREFIX_SHARE * min(len(a), len(b)) or SequenceMatcher(None, a, b).ratio() >= ROMAJI_SIMILARITY


def _belongs_to(node_key: str, key: str, title: str = "", node_title: str = "") -> bool:
    """系列名 key が外側の名前 node_key の作品の巻か(同じ名前、片方がもう片方に `番号` `…編` `区切り + 副題` を
    足しただけの名前)。外側が長い方(`フシノカミ ~辺境から…~` の下の `フシノカミ`)も同じ規則で見る。"""
    if key == node_key or _romaji_variant(node_key, key):
        return True
    return _extends(node_key, key, title) or (
        _has_cjk(key) == _has_cjk(node_key) and _extends(key, node_key, node_title))


def _differs_only_in_numbers(a: str, b: str) -> bool:
    """日本語の系列名どうしが、数字の部分だけ違うか(`…前に1ぼすきゃら…` と `…前に2ぼすきゃら…`)。
    1 つの入れ物の中だけで使う。巻の番号が作品名と副題の間にある名前の組。"""
    return _has_cjk(a) and _has_cjk(b) and re.sub(r"\d+", "", a) == re.sub(r"\d+", "", b) != ""


def _same_series(a: str, b: str, titles: dict[str, str]) -> bool:
    """同じ系列か。長い方が短い方に番号・`…編`・区切りの後ろの副題を足しただけのときだけ(`ゾンビ` と
    `ゾンビだらけの世界` は別)。外側の名前との突き合わせ(`_belongs_to`)と同じ規則。"""
    if a == b or _romaji_variant(a, b) or _differs_only_in_numbers(a, b):
        return True
    short, long_ = (a, b) if len(a) <= len(b) else (b, a)
    return _extends(short, long_, titles.get(long_, ""))


def _group_keys(items: dict[str, list]) -> list[list[str]]:
    """系列名の鍵を同じ系列ごとにまとめる。items は 鍵 → [(巻, WorkName)](区切りの判定に作品名を使う)。"""
    titles = {k: v[0][1].title for k, v in items.items() if v}
    groups: list[list[str]] = []
    for k in sorted(set(items), key=len):
        for g in groups:
            if any(_same_series(k, x, titles) for x in g):
                g.append(k)
                break
        else:
            groups.append([k])
    return groups


def _container_claims(st: _State) -> dict[int, OwnName]:
    parts_of: dict[int, list[str]] = {}
    under: dict[tuple, list] = defaultdict(list)
    for r in st.vols:
        parts = _path_parts(r)
        parts_of[r["id"]] = parts
        for i in range(len(parts) - 1):
            under[tuple(parts[:i + 1])].append(r)

    def series_below(r, depth: int) -> WorkName | None:
        parts = parts_of[r["id"]]
        for text in [r["title"], *reversed(parts[depth + 1:-1])]:
            wn = _series_name(text)
            if wn is not None:
                return _without_trailing_number(wn)
        return None

    found: dict[int, list[tuple[int, WorkName, str]]] = defaultdict(list)
    for key, rows in under.items():
        depth, text = len(key) - 1, key[-1]
        nwn = _series_name(text)
        if nwn is None or nwn.norm in _CATEGORY_NAMES:
            continue
        nwn = _without_trailing_number(nwn)
        node_jp, nkey = _has_cjk(nwn.title), _series_key(nwn)
        mine, free = [], []
        jp: dict[str, list] = defaultdict(list)
        ro: dict[str, list] = defaultdict(list)
        for r in rows:
            wn = series_below(r, depth)
            if wn is None:
                free.append(r)
                continue
            k = _series_key(wn)
            if _belongs_to(nkey, k, wn.title, nwn.title):
                mine.append(r)
            elif _has_cjk(wn.title):
                jp[k].append((r, wn))
            else:
                ro[k].append((r, wn))
        jg, rg = _group_keys(jp), _group_keys(ro)
        if node_jp:
            if len(jg) >= 2:
                continue  # まとめのフォルダ(`小説`)
            claimed = mine + free
            if not jg and (len(rg) <= 1 or mine):
                claimed += [r for items in ro.values() for r, _ in items]
            title = nwn
        else:
            if rg or len(jg) > 1:
                continue
            if jg:
                names = [wn for k in jg[0] for _, wn in jp[k]]
                counts: dict[str, int] = defaultdict(int)
                for wn in names:
                    counts[wn.title] += 1
                best = min(counts, key=lambda t: (-counts[t], len(t)))
                title = next(wn for wn in names if wn.title == best)
                if len(counts) > 1 and len({re.sub(r"\d+", "", t) for t in counts}) == 1:
                    # 名前の間の番号だけが違う(`チュートリアルが始まる前に 1 ボスキャラ…`)。番号を落とした名前を作品名にする
                    title = parse_work_name(re.sub(r"\s+\d{1,3}\s+", " ", best)) or title
                claimed = mine + free + [r for k in jg[0] for r, _ in jp[k]]
            else:
                title, claimed = nwn, mine + free
        for r in claimed:
            found[r["id"]].append((depth, title, text))

    def romaji_names(vid: int, depth: int) -> tuple[str, ...]:
        """入れ物(depth)とその中のフォルダ・アーカイブの、ローマ字の系列名(巻自身の名前は別に残る)。"""
        names: list[str] = []
        for text in parts_of[vid][depth:-1]:
            wn = _series_name(text)
            if wn is None or _has_cjk(wn.title) or wn.norm in _CATEGORY_NAMES:
                continue
            wn = _without_trailing_number(wn)
            title = " ".join(w for w in re.split(r"[\s_]+", wn.title)
                             if w.casefold() not in _ROMAJI_NOISE) or wn.title
            if title not in names:
                names.append(title)
        return tuple(names)

    out: dict[int, OwnName] = {}
    for vid, cands in found.items():
        cands.sort(key=lambda x: x[0])  # 外側から
        jp_cands = [x for x in cands if _has_cjk(x[1].title)]
        depth, wn, text = jp_cands[-1] if jp_cands else cands[0]
        out[vid] = OwnName(wn, 1, text, container=True,
                           romaji=romaji_names(vid, depth) if _has_cjk(wn.title) else ())
    return out


def _claim_keep_key(c, work_id: int):
    """外側の名前でまとめるときに残す作品の順: 手動の作品名 > 公開 DB の日本語の作品名 > 日本語 > 巻の多い方 > id。"""
    r = c.execute("SELECT value, source, manual FROM work_fields WHERE work_id = ? AND field = ?",
                  (work_id, FIELD_TITLE)).fetchone()
    title, source, manual = (r["value"] or "", r["source"], bool(r["manual"])) if r else ("", "", False)
    n = c.execute("SELECT COUNT(*) FROM work_volumes WHERE work_id = ?", (work_id,)).fetchone()[0]
    return (manual, source == SOURCE_PUBLIC_DB and _has_cjk(title), _has_cjk(title), n, -work_id)


def _settle_claims(c, st: _State, claims: dict[int, OwnName], moved: dict[int, set[int]],
                   summary: SyncSummary) -> None:
    """外側の名前で割り当てた後の片付け。

    1. 同じ外側の名前の巻が複数の作品に分かれていたら 1 つに統合する(巻ごとの作品が、公開 DB の系列名を別名に
       持つせいで動かなかった組。`オーバーロード 1 …` `オーバーロード 10 …` など)
    2. 巻が全部ほかの作品へ移って空になった作品は、移り先へ統合する(残すと同じ別名の空の作品が総当たりの提案になる)
    3. 名前から付けた作品名を、外側の日本語の名前に揃える(全部の巻が同じ外側の名前で割り当たっているとき)
    手動の決定が絡む作品は動かさない(提案の規則に任せる)。
    """
    def root(i: int) -> int:
        return root_of(c, i) or i

    groups: dict[str, set[int]] = defaultdict(set)
    for vid, cl in claims.items():
        a = st.assign.get(vid)
        if a is not None and not a.get("manual"):
            groups[cl.name.norm].add(a["work_id"])  # 外側と入れ子で同じ作品名になったもの(`Fushi no Kami …`)も 1 つ
    for ids in groups.values():
        ids = {root(i) for i in ids}
        ids = {i for i in ids if not _manual_involved(c, i)}
        if len(ids) < 2:
            continue
        keep = max(ids, key=lambda i: _claim_keep_key(c, i))
        for drop in sorted(ids - {keep}):
            merge_works(c, keep, drop, rule="container", confidence=CONF_CONTAINER_CLAIM, manual=False)
            summary.merged += 1
    for w, targets in moved.items():
        w = root(w)
        dests = {root(t) for t in targets} - {w}
        if len(dests) != 1 or _manual_involved(c, w):
            continue
        if c.execute("SELECT 1 FROM work_volumes WHERE work_id = ? LIMIT 1", (w,)).fetchone():
            continue
        dest = dests.pop()
        # 移り先に手動の決定が絡むなら必ず移り先を残す(手動の作品を吸収させない)
        prefer_w = not _manual_involved(c, dest) and _claim_keep_key(c, w)[:2] > _claim_keep_key(c, dest)[:2]
        keep, drop = (w, dest) if prefer_w else (dest, w)
        merge_works(c, keep, drop, rule="container", confidence=CONF_CONTAINER_CLAIM, manual=False)
        summary.merged += 1
    # 3. 作品名を外側の日本語の名前に揃える
    by_work: dict[int, set[tuple[str, str]]] = defaultdict(set)
    work_of = dict(c.execute("SELECT volume_id, work_id FROM work_volumes").fetchall())  # 巻ごとに引かない
    for vid in st.listed:
        wid = work_of.get(vid)
        if wid is None:
            continue
        cl = claims.get(vid)
        key = (cl.name.norm, cl.name.title) if cl is not None and _has_cjk(cl.name.title) else ("", "")
        by_work[wid].add(key)
    own_titles: dict[int, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for r in st.vols:
        wid = work_of.get(r["id"])
        own = own_name(r)
        if wid is not None and own is not None:
            own_titles[wid][own.name.title] += 1
    for wid, keys in by_work.items():
        norm, title = next(iter(keys)) if len(keys) == 1 else ("", "")
        if not norm:
            # 前の読み方で付いた、今は作品名と読めない作品名(`bbjjf1 y files`)は、巻の名前の多数で付け直す
            f = c.execute("SELECT value, source, manual FROM work_fields WHERE work_id = ? AND field = ?",
                          (wid, FIELD_TITLE)).fetchone()
            cands = own_titles.get(wid)
            if (f is None or f["manual"] or f["source"] != SOURCE_FILENAME or not cands
                    or parse_work_name(f["value"] or "") is not None):
                continue
            title = min(cands, key=lambda t: (-cands[t], len(t)))
            norm = normalize_title(title)
        f = c.execute("SELECT value, source, manual FROM work_fields WHERE work_id = ? AND field = ?",
                      (wid, FIELD_TITLE)).fetchone()
        if f is None or f["manual"] or f["source"] not in (SOURCE_FILENAME, SOURCE_MERGE, SOURCE_CONTAINER):
            continue
        old = f["value"] or ""
        if normalize_title(old) == norm or (_has_cjk(old) and norm.startswith(normalize_title(old))):
            continue
        readable = parse_work_name(old) is not None
        if readable:
            _insert_alias(c, wid, old, SOURCE_FILENAME, manual=False, confidence=CONF_OWN_NAME, evidence=None)
        if len(keys) == 1 and next(iter(keys))[0]:
            _set_field(c, wid, FIELD_TITLE, title, SOURCE_CONTAINER, False, CONF_CONTAINER_CLAIM, {"container": title})
        else:
            _set_field(c, wid, FIELD_TITLE, title, SOURCE_FILENAME, False, CONF_OWN_NAME, {"name": title})


def _keep_container_romaji(c, st: _State, work_id: int, own: OwnName | None) -> None:
    """日本語の作品名の入れ物の中のローマ字の名前(`Isekai_nonbiri_sozai_saishu_seikatsu_v01-03`)を、外側の名前の
    別名(確信度 0.5)に残す。別の場所に同じローマ字の名前だけで届いた巻の作品を、作品名との一致で統合する根拠になる
    (`_alias_evidence` の「外側の名前の別名と作品名の完全一致」)。巻の割り当てには使わない(`_add_index`)。"""
    work = st.works.get(work_id)
    if work is None or own is None:
        return
    for title in own.romaji:
        norm = normalize_title(title)
        if not norm or is_junk_name(norm) or norm in work.names:
            continue
        if _insert_alias(c, work_id, title, SOURCE_CONTAINER, manual=False, confidence=CONF_CONTAINER_ALIAS,
                         evidence={"container": own.text, "name": title}):
            work.names[norm] = _Name(title, SOURCE_CONTAINER, CONF_CONTAINER_ALIAS, False)


def _keep_own_alias(c, st: _State, work_id: int, own: OwnName | None) -> None:
    """外側の名前で割り当てた巻の、巻自身の名前(ローマ字の作品名など)を作品の別名に残す。"""
    if own is None or own.level != 0 or is_junk_name(own.name.norm):
        return
    work = st.works.get(work_id)
    if work is None or own.name.norm in work.names:
        return
    if _insert_alias(c, work_id, own.name.title, SOURCE_FILENAME, manual=False, confidence=own.confidence,
                     evidence=own.evidence()):
        work.names[own.name.norm] = _Name(own.name.title, SOURCE_FILENAME, own.confidence, False)
        st._add_index(work_id, own.name.norm, work.names[own.name.norm])


@dataclass
class SyncSummary:
    works_created: int = 0
    volumes_assigned: int = 0
    merged: int = 0
    proposals_open: int = 0


# --- 書き込みの部品 --------------------------------------------------------------------------


def _set_field(c, work_id: int, name: str, value: str | None, source: str, manual: bool, confidence: float | None,
               evidence: dict | None) -> None:
    c.execute(
        """INSERT INTO work_fields (work_id, field, value, source, manual, confidence, evidence, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT (work_id, field) DO UPDATE SET value = excluded.value, source = excluded.source,
               manual = excluded.manual, confidence = excluded.confidence, evidence = excluded.evidence,
               updated_at = excluded.updated_at""",
        (work_id, name, value, source, int(manual), confidence, _dumps(evidence) if evidence else None, db.now()))
    c.execute("UPDATE works SET updated_at = ? WHERE id = ?", (db.now(), work_id))


def _new_work(c, title: str, *, source: str, manual: bool, confidence: float, evidence: dict | None,
              author: str | None = None, author_source: str = SOURCE_FILENAME, author_confidence: float | None = None,
              author_evidence: dict | None = None) -> int:
    now = db.now()
    work_id = c.execute("INSERT INTO works (created_at, updated_at) VALUES (?, ?)", (now, now)).lastrowid
    _set_field(c, work_id, FIELD_TITLE, title, source, manual, confidence, evidence)
    if author:
        _set_field(c, work_id, FIELD_AUTHOR, author, author_source, False, author_confidence, author_evidence)
    return work_id


def _insert_alias(c, work_id: int, alias: str, source: str, *, manual: bool, confidence: float | None,
                  evidence: dict | None, revive: bool = False) -> bool:
    """別名を足す。同じ正規化の別名が既にあれば何もしない(外された別名は、revive でなければ戻さない)。"""
    norm = normalize_title(alias)
    if not norm:
        return False
    cur = c.execute(
        """INSERT INTO work_aliases (work_id, alias, norm, source, manual, confidence, evidence, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (work_id, norm) DO NOTHING""",
        (work_id, alias, norm, source, int(manual), confidence, _dumps(evidence) if evidence else None, db.now()))
    if cur.rowcount == 0 and revive:
        c.execute("""UPDATE work_aliases SET dismissed_at = NULL, alias = ?, source = ?, manual = ?, confidence = ?,
                     evidence = ? WHERE work_id = ? AND norm = ? AND dismissed_at IS NOT NULL""",
                  (alias, source, int(manual), confidence, _dumps(evidence) if evidence else None, work_id, norm))
        return True
    return cur.rowcount > 0


def _put_assignment(c, volume_id: int, work_id: int, source: str, manual: bool, confidence: float | None,
                    evidence: dict | None) -> None:
    c.execute(
        """INSERT INTO work_volumes (volume_id, work_id, source, manual, confidence, evidence, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT (volume_id) DO UPDATE SET work_id = excluded.work_id, source = excluded.source,
               manual = excluded.manual, confidence = excluded.confidence, evidence = excluded.evidence,
               updated_at = excluded.updated_at""",
        (volume_id, work_id, source, int(manual), confidence, _dumps(evidence) if evidence else None, db.now()))


def author_text(authors) -> str | None:
    return AUTHOR_SEPARATOR.join(authors) if authors else None


def transfer_assignment(c: sqlite3.Connection, from_id: int, to_id: int) -> None:
    """巻の作品の割り当て(手動の決定を含む)を別の巻へ写す。整理で巻が CBZ の新しい巻に置き換わるときに使う。"""
    row = c.execute("SELECT * FROM work_volumes WHERE volume_id = ?", (from_id,)).fetchone()
    if row is None:
        return
    _put_assignment(c, to_id, row["work_id"], row["source"], bool(row["manual"]), row["confidence"],
                    json.loads(row["evidence"]) if row["evidence"] else None)


# --- 統合 ------------------------------------------------------------------------------------


def _roots(c) -> dict[int, int]:
    return {r["id"]: r["merged_into"] for r in c.execute("SELECT id, merged_into FROM works WHERE merged_into IS NOT NULL")}


def root_of(c, work_id: int) -> int | None:
    """統合されていれば吸収先、そうでなければ自分。作品が無ければ None。"""
    row = c.execute("SELECT id, merged_into FROM works WHERE id = ?", (work_id,)).fetchone()
    return None if row is None else (row["merged_into"] or row["id"])


def _title_of(c, work_id: int) -> tuple[str, bool]:
    r = c.execute("SELECT value, manual FROM work_fields WHERE work_id = ? AND field = ?",
                  (work_id, FIELD_TITLE)).fetchone()
    return (r["value"] or "", bool(r["manual"])) if r else ("", False)


def choose_survivor(c, a: int, b: int) -> tuple[int, int]:
    """統合で残す作品と吸収される作品。手動の作品名 > 日本語の作品名 > 巻の多い方 > id の若い方。"""
    def key(i: int):
        title, manual = _title_of(c, i)
        n = c.execute("SELECT COUNT(*) FROM work_volumes WHERE work_id = ?", (i,)).fetchone()[0]
        return (manual, _has_cjk(title), n, -i)
    keep, drop = (a, b) if key(a) >= key(b) else (b, a)
    return keep, drop


def merge_works(c, keep: int, drop: int, *, rule: str, confidence: float, manual: bool) -> None:
    """drop を keep に統合する(巻・別名・空欄の著者・ブックマークを移す)。drop の行は消さず merged_into を立てる。

    手動の統合・承認(manual)で動かした巻は手動の割り当てにする。drop の作品名は keep の別名に残す
    (その名前の巻がまた見つかっても、keep に入る)。
    """
    source = {"duplicate": SOURCE_DUPLICATE, "alias": SOURCE_ALIAS, "container": SOURCE_CONTAINER}.get(rule, SOURCE_MANUAL)
    merge_info = {"merged_from": drop, "merged_into": keep, "rule": rule, "confidence": confidence}
    for m in c.execute("SELECT * FROM work_volumes WHERE work_id = ?", (drop,)).fetchall():
        was_manual = bool(m["manual"])
        ev = {"merge": merge_info, "previous": {"source": m["source"], "manual": was_manual,
                                                "confidence": m["confidence"],
                                                "evidence": json.loads(m["evidence"]) if m["evidence"] else None}}
        _put_assignment(c, m["volume_id"], keep, m["source"] if was_manual else source, was_manual or manual,
                        m["confidence"] if was_manual else confidence, ev)
    keep_norm = normalize_title(_title_of(c, keep)[0])
    drop_title, _ = _title_of(c, drop)
    if normalize_title(drop_title) != keep_norm:
        _insert_alias(c, keep, drop_title, SOURCE_MERGE, manual=manual, confidence=confidence, evidence=merge_info,
                      revive=True)
    for a in c.execute("SELECT * FROM work_aliases WHERE work_id = ? AND dismissed_at IS NULL", (drop,)).fetchall():
        if a["norm"] != keep_norm:
            _insert_alias(c, keep, a["alias"], a["source"], manual=bool(a["manual"]), confidence=a["confidence"],
                          evidence=json.loads(a["evidence"]) if a["evidence"] else None, revive=True)
    for name in (FIELD_AUTHOR, FIELD_KIND, FIELD_PUBLISHER, FIELD_GENRES, FIELD_TAGS, FIELD_TOTAL_VOLUMES):
        kept = c.execute("SELECT value, manual FROM work_fields WHERE work_id = ? AND field = ?",
                         (keep, name)).fetchone()
        dropped = c.execute("SELECT * FROM work_fields WHERE work_id = ? AND field = ?", (drop, name)).fetchone()
        if dropped and dropped["value"] and (
                kept is None or (not kept["manual"] and (not kept["value"] or dropped["manual"]))):
            # 手動の値は、名前や公開 DB から入った値より優先して残す(吸収される側が手動なら、その値を移す)
            _set_field(c, keep, name, dropped["value"], dropped["source"], bool(dropped["manual"]),
                       dropped["confidence"], json.loads(dropped["evidence"]) if dropped["evidence"] else None)
    _merge_links(c, keep, drop)
    _merge_bookmarks(c, keep, drop)
    # 後で読む(#173)の印も吸収先へ移す。read_later は循環 import を避けてここで直接扱う
    _dropped = c.execute("SELECT work_id, created_at FROM read_later WHERE work_id = ?", (drop,)).fetchone()
    if _dropped is not None:
        _kept = c.execute("SELECT work_id, created_at FROM read_later WHERE work_id = ?", (keep,)).fetchone()
        if _kept is None:
            c.execute("UPDATE read_later SET work_id = ? WHERE work_id = ?", (keep, drop))
        else:
            c.execute("DELETE FROM read_later WHERE work_id = ?", (drop,))
            if _dropped["created_at"] < _kept["created_at"]:
                c.execute("UPDATE read_later SET created_at = ? WHERE work_id = ?", (_dropped["created_at"], keep))
    now = db.now()
    c.execute("UPDATE works SET merged_into = ?, updated_at = ? WHERE id = ?", (keep, now, drop))
    c.execute("UPDATE works SET merged_into = ?, updated_at = ? WHERE merged_into = ?", (keep, now, drop))
    c.execute("UPDATE works SET updated_at = ? WHERE id = ?", (now, keep))


def _merge_links(c, keep: int, drop: int) -> None:
    """吸収される作品の公開 DB との連携を、残る作品へ移す。残る側に同じ取得元の連携が無いとき、
    または吸収される側が手動で選んだものを残る側が手動で選んでいないときだけ(手動の決定を守る)。"""
    for dl in c.execute("SELECT * FROM work_links WHERE work_id = ?", (drop,)).fetchall():
        kl = c.execute("SELECT * FROM work_links WHERE work_id = ? AND source = ?", (keep, dl["source"])).fetchone()
        if kl is not None:
            better = (dl["manual"] and not kl["manual"]) or (
                not kl["manual"] and kl["status"] != "linked" and dl["status"] == "linked")
            if not better:
                continue
            c.execute("DELETE FROM work_links WHERE work_id = ? AND source = ?", (keep, dl["source"]))
        c.execute("UPDATE work_links SET work_id = ? WHERE work_id = ? AND source = ?", (keep, drop, dl["source"]))


def _merge_bookmarks(c, keep: int, drop: int) -> None:
    """吸収される作品のブックマーク(#86)を残る作品へ移す。両方にあれば早い方の日時で 1 つにする。"""
    dropped = c.execute("SELECT id, created_at FROM bookmarks WHERE work_id = ?", (drop,)).fetchone()
    if dropped is None:
        return
    kept = c.execute("SELECT id, created_at FROM bookmarks WHERE work_id = ?", (keep,)).fetchone()
    if kept is None:
        c.execute("UPDATE bookmarks SET work_id = ? WHERE id = ?", (keep, dropped["id"]))
        return
    c.execute("DELETE FROM bookmarks WHERE id = ?", (dropped["id"],))
    if dropped["created_at"] < kept["created_at"]:
        c.execute("UPDATE bookmarks SET created_at = ? WHERE id = ?", (dropped["created_at"], kept["id"]))


def _manual_involved(c, work_id: int) -> bool:
    retired = ",".join("?" * len(RETIRED_FIELDS))
    if c.execute(f"SELECT 1 FROM work_fields WHERE work_id = ? AND manual = 1 AND field NOT IN ({retired}) LIMIT 1",
                 (work_id, *RETIRED_FIELDS)).fetchone():
        return True  # 手で直した作品名・著者などの項目(廃止した項目は画面から戻せないので数えない)
    if c.execute("SELECT 1 FROM work_volumes WHERE work_id = ? AND manual = 1 LIMIT 1", (work_id,)).fetchone():
        return True  # 手動の巻の割り当て
    # 利用者が選んだ公開 DB の連携(「どれでもない」も)。統合で片方が消えると、その決定が失われうる
    return c.execute("SELECT 1 FROM work_links WHERE work_id = ? AND manual = 1 LIMIT 1", (work_id,)).fetchone() is not None


def _numbered_variant(title: str, norm: str) -> bool:
    """作品名が「系列名 + 末尾の番号」だけか(例: `作品名 01`。`norm` は系列名の正規化)。

    公開 DB に linked した 1 巻 = 1 作品の残骸を見分けるために使う(#162)。別名で系列名を持つだけなので、
    系列名の作品があればそちらへ寄せる。
    """
    t = trailing_number(title)
    if t is None:
        return False
    wn = parse_work_name(t.base)
    return wn is not None and wn.norm == norm


def _leaves_numbered_work(c, work: _Work, norm: str, want: dict | None) -> bool:
    """末尾の番号で読んだ系列名へ付け替えるために、今の作品を離れるか(#162)。

    今の作品名が `系列名 + 番号` そのもの(`作品名 01`)なら、その作品は 1 巻ぶんの残骸で、系列名は
    公開 DB の別名として持っているだけなので、系列名の作品へ移す。手で直した作品は動かさない。
    """
    if want is None or not _numbered_variant(work.title, norm):
        return False
    return not _manual_involved(c, work.id)


def _pair(a: int, b: int) -> tuple[int, int]:
    return (a, b) if a < b else (b, a)


def _dismiss_junk_aliases(c: sqlite3.Connection) -> None:
    """EPUB の構造名など、作品名として使えない名前の別名と、ジャンク作品の自動別名を外す(汚染防止)。

    1. 別名の正規化が JUNK_NAMES に入る自動別名(manual = 0)を dismiss する。
    2. 作品のタイトル自体が JUNK_NAMES である非手動の作品に付いた自動別名(manual = 0)をすべて dismiss する。
       (EPUB 内の OEBPS フォルダが作品になり、外側アーカイブ名が別名として大量登録された汚染の解消)
    3. タイトルが JUNK_NAMES である非手動作品との確認待ち(open)の提案を削除する。
    """
    now = db.now()
    # 直せない文字化けの別名(統合の根拠の作品名が化けていたもの)も外す。手動の別名には触らない
    junk_alias_ids = [
        r["id"] for r in c.execute(
            "SELECT id, norm, alias FROM work_aliases WHERE dismissed_at IS NULL AND manual = 0"
        ).fetchall()
        if is_junk_name(r["norm"]) or looks_garbled(r["alias"] or "")
    ]
    if junk_alias_ids:
        c.executemany("UPDATE work_aliases SET dismissed_at = ? WHERE id = ?",
                      [(now, aid) for aid in junk_alias_ids])

    junk_work_ids = [
        r["work_id"] for r in c.execute(
            "SELECT work_id, value FROM work_fields WHERE field = 'title' AND manual = 0"
        ).fetchall()
        if is_junk_name(normalize_title(r["value"] or ""))
    ]
    if junk_work_ids:
        q_placeholders = ",".join("?" * len(junk_work_ids))
        c.execute(f"""UPDATE work_aliases SET dismissed_at = ? WHERE dismissed_at IS NULL AND manual = 0
                      AND work_id IN ({q_placeholders})""", (now, *junk_work_ids))
        c.execute(f"""DELETE FROM work_merge_proposals WHERE status = 'open'
                      AND (work_a IN ({q_placeholders}) OR work_b IN ({q_placeholders}))""",
                  (*junk_work_ids, *junk_work_ids))


# --- sync ------------------------------------------------------------------------------------


def sync(conn: sqlite3.Connection) -> SyncSummary:
    """一覧に見えている巻を作品に割り当て、統合の提案を作り直す。冪等で、変化が無ければ書かない。

    手動の項目・割り当て・別名、却下した提案は触らない。1 つのトランザクションで行う(同時に 2 つ走っても
    同じ名前の作品を二重に作らない)。
    """
    summary = SyncSummary()
    with db.transaction(conn) as c:
        _dismiss_junk_aliases(c)
        st = _State(c)
        _assign_volumes(c, st, summary)
        _container_aliases(c, _State(c))  # 提案の根拠になるので、統合の前に別名を足す
        _propose_and_merge(c, _State(c), summary)
        summary.proposals_open = c.execute(
            "SELECT COUNT(*) FROM work_merge_proposals WHERE status = 'open'").fetchone()[0]
    return summary


def _owns_trailing_number(r) -> bool:
    """末尾の番号の規則で入れた番号か(付け直しで消してよいのはこれだけ)。"""
    if r["number_kind"] != "volume" or r["number_start"] is None or r["number_start"] != r["number_end"]:
        return False
    t = trailing_number(r["title"])
    return t is not None and t.number == r["number_start"]


def _trailing_series(st: _State) -> dict[int, dict]:
    """同じ名前で末尾の番号だけが違う巻の組から、作品名と巻番号を読む(名前だけでは決めない)。

    `作品名 01` `作品名 02` … と 2 つ以上の違う番号が並ぶ組を系列とみなす。1 巻だけの
    `作品名 01` は、作品名がローマ字なら作品名の一部として残す(`Kitchen 2` が作品名のこともあるため。
    `spinebox/naming.py`)。日本語なら巻番号と読む。
    """
    groups: dict[str, list] = defaultdict(list)
    for r in st.vols:
        if r["number_kind"] is not None and not _owns_trailing_number(r):
            continue  # 印の付いた番号(`v01-03`)など、強い根拠で読めている番号は触らない
        t = trailing_number(r["title"])
        if t is None:
            continue
        wn = parse_work_name(t.base)
        if wn is None:
            continue
        groups[wn.norm].append((r["id"], t, wn))
    out: dict[int, dict] = {}
    for items in groups.values():
        # 1 冊だけの `作品名 01` は、作品名が日本語のときは巻番号と読む(`かぐや様は告らせたい 3`)。
        # 作品名の一部の番号(`Kitchen 2`)はローマ字の名前に多く、日本語の作品名は番号を空白で離さない(`3月のライオン`)
        if len({t.number for _, t, _ in items}) < 2 and not all(_has_cjk(wn.title) for _, _, wn in items):
            continue
        for vid, t, wn in items:
            out[vid] = {"work_name": wn, "number": t.number, "evidence": t.evidence}
    return out


def _apply_trailing_numbers(c, st: _State, series: dict[int, dict]) -> None:
    """末尾の番号を巻の番号の列へ反映する。組が無くなった番号は消す(名前から導出した値なので)。"""
    now = db.now()
    for r in st.vols:
        want = series.get(r["id"])
        if want is None:
            # 末尾の番号の組が無くなった番号と、今の読み方で読めない番号(小数点の巻など)を消す。
            # 番号を持たない巻の行は触らない(冪等)。
            if _owns_trailing_number(r) or (r["number_kind"] is not None and parse_number(r["title"]) is None):
                c.execute("UPDATE volumes SET number_kind = NULL, number_start = NULL, number_end = NULL, "
                          "number_evidence = NULL, updated_at = ? WHERE id = ?", (now, r["id"]))
            continue
        if (r["number_kind"], r["number_start"], r["number_end"], r["number_evidence"]) == \
                ("volume", want["number"], want["number"], want["evidence"]):
            continue
        c.execute("UPDATE volumes SET number_kind = 'volume', number_start = ?, number_end = ?, "
                  "number_evidence = ?, updated_at = ? WHERE id = ?",
                  (want["number"], want["number"], want["evidence"], now, r["id"]))


def _assign_volumes(c, st: _State, summary: SyncSummary) -> None:
    series = _trailing_series(st)
    _apply_trailing_numbers(c, st, series)
    claims = _container_claims(st)
    moved: dict[int, set[int]] = defaultdict(set)
    for r in st.vols:
        vid = r["id"]
        cur = st.assign.get(vid)
        if cur is not None and cur["manual"]:
            continue
        if cur is None and r["parent_id"] is not None:
            parent = st.assign.get(r["parent_id"])
            if parent is not None and parent["manual"]:
                # 分解前のまとめに付けた手動の決定は、中の巻にも及ぶ
                ev = {"inherited_from_volume": r["parent_id"]}
                _put_assignment(c, vid, parent["work_id"], SOURCE_MANUAL, True, parent["confidence"], ev)
                st.assign[vid] = {"work_id": parent["work_id"], "manual": 1, "source": SOURCE_MANUAL}
                summary.volumes_assigned += 1
                continue
        want = series.get(vid)
        own = own_name(r, want)
        claim = claims.get(vid)
        alias = None
        if claim is not None and (own is None or claim.name.norm != own.name.norm):
            alias, own = own, claim  # 巻自身の名前(ローマ字など)は、割り当てた作品の別名に残す
        if own is None:
            continue
        norm = own.name.norm
        work = st.works.get(cur["work_id"]) if cur is not None else None
        if work is not None:
            _refresh_title(c, st, work, own)  # 系列名の別名が無くても、誤読した 1 巻の作品名を直す
        leaves = work is not None and norm in work.names and _leaves_numbered_work(c, work, norm, want)
        if work is not None and norm in work.names and not leaves:
            if cur["source"] == SOURCE_FILENAME:
                _refresh_assignment(c, vid, cur, own)
            _fill_author(c, work, _authors_of(st, vid, own), SOURCE_FILENAME, own.confidence, own.evidence())
            _keep_own_alias(c, st, work.id, alias)
            _keep_container_romaji(c, st, work.id, claim)
            continue
        target = st.find_work(norm)
        if want is not None and target is not None and _numbered_variant(st.works[target].title, norm):
            target = None  # 系列名を別名で持つだけの番号付きの作品には寄せない(新しい系列名の作品を作る)
        if target is None:
            author = author_text(_authors_of(st, vid, own))
            target = _new_work(c, own.name.title, source=SOURCE_FILENAME, manual=False, confidence=own.confidence,
                               evidence=own.evidence(), author=author, author_source=SOURCE_FILENAME,
                               author_confidence=own.confidence, author_evidence=own.evidence())
            w = _Work(target, own.name.title, False, SOURCE_FILENAME, author, False)
            w.names[norm] = _Name(own.name.title, SOURCE_FILENAME, own.confidence, True)
            st.works[target] = w
            st._add_index(target, norm, w.names[norm])
            summary.works_created += 1
        else:
            _fill_author(c, st.works[target], _authors_of(st, vid, own), SOURCE_FILENAME, own.confidence, own.evidence())
        if leaves:
            _merge_links(c, target, work.id)
        if cur is not None and cur["work_id"] != target and own.container:
            moved[cur["work_id"]].add(target)  # 外側の名前で動かした巻だけ(ほかの付け替えは提案の規則に任せる)
        _put_assignment(c, vid, target, SOURCE_FILENAME, False, own.confidence, own.evidence())
        st.assign[vid] = {"work_id": target, "manual": 0, "source": SOURCE_FILENAME}
        _keep_own_alias(c, st, target, alias)
        _keep_container_romaji(c, st, target, claim)
        summary.volumes_assigned += 1
    _settle_claims(c, st, claims, moved, summary)


def _refresh_assignment(c, vid: int, cur: dict, own: OwnName) -> None:
    """名前から付けた割り当てを、今の名前の読み(確信度・根拠)に合わせる。変化が無ければ書かない。"""
    evidence = _dumps(own.evidence())
    if cur.get("evidence") != evidence or cur.get("confidence") != own.confidence:
        _put_assignment(c, vid, cur["work_id"], SOURCE_FILENAME, False, own.confidence, own.evidence())
        cur["evidence"], cur["confidence"] = evidence, own.confidence


def _authors_of(st: _State, vid: int, own: OwnName) -> tuple[str, ...]:
    """巻の名前から読んだ著者。無ければ、巻自身の名前で作品を決めた EPUB の dc:creator。"""
    if own.name.authors:
        return own.name.authors
    creator = st.creators.get(vid) if own.level == 0 and not own.container else None
    return (creator,) if creator else ()


def _fill_author(c, work: _Work, authors, source: str, confidence: float, evidence: dict) -> None:
    """著者が空欄で、手で空にしたのでもなければ、名前から読んだ著者で埋める(あるものは上書きしない)。"""
    text = author_text(authors)
    if text and not work.author and not work.author_manual:
        _set_field(c, work.id, FIELD_AUTHOR, text, source, False, confidence, evidence)
        work.author = text


def _stale_title_norm(title: str) -> str | None:
    """今の読みで作品名を読み直すと変わる古い作品名(`★作品名` `作品名【`)の正規化。変わらなければ None。"""
    wn = parse_work_name(title)
    if wn is None or wn.title == title:
        return None
    return wn.norm


def _refresh_title(c, st: _State, work: _Work, own: OwnName) -> None:
    """名前から読んだ作品名が変わったら、手動でない作品名を今の読みに合わせる(#167)。

    `ようこそ… 第04` のように小数点の巻を誤読して作った作品名を、小数点を外した系列名へ直す。
    1 巻の作品は常に直す。複数の巻を持つ作品は、別の巻の名前と食い違う可能性があるので、
    今の読みで変わる古い作品名(`★さわらないで小手指くん` など)を、同じ系列の巻が読んだときだけ直す。
    """
    if work.title_manual or work.title_source != SOURCE_FILENAME:
        return  # 手動の作品名・公開 DB が置き換えた作品名は、名前の読みで上書きしない
    if trailing_number(work.title) is not None:
        return  # 末尾の番号を持つ作品名は `_leaves_numbered_work` の領分(手動が絡むときは動かさない)
    title = own.name.title
    if not title or title == work.title:
        return
    stale = _stale_title_norm(work.title)
    if c.execute("SELECT COUNT(*) FROM work_volumes WHERE work_id = ?", (work.id,)).fetchone()[0] != 1:
        if stale is None or own.name.norm != stale:
            return
    old_norm = normalize_title(work.title)
    if old_norm in st.by_norm:
        st.by_norm[old_norm] = [i for i in st.by_norm[old_norm] if i != work.id]
    if old_norm in work.names and work.names[old_norm].is_title:
        del work.names[old_norm]
    new_norm = own.name.norm
    work.names[new_norm] = _Name(title, SOURCE_FILENAME, own.confidence, True)
    st._add_index(work.id, new_norm, work.names[new_norm])
    _set_field(c, work.id, FIELD_TITLE, title, SOURCE_FILENAME, False, own.confidence, own.evidence())
    work.title = title
    work.title_source = SOURCE_FILENAME


# --- 統合の提案 ------------------------------------------------------------------------------


def _duplicate_evidence(c, st: _State) -> dict[tuple[int, int], list[dict]]:
    """却下されていない重複候補のうち、別々の作品に割り当てられた巻どうしの組を、作品の組ごとに集める。"""
    titles = {r["id"]: r["title"] for r in st.vols}
    out: dict[tuple[int, int], list[dict]] = defaultdict(list)
    for r in c.execute("SELECT * FROM duplicate_candidates WHERE dismissed_at IS NULL ORDER BY id"):
        a, b = r["volume_a"], r["volume_b"]
        if a not in st.listed or b not in st.listed:
            continue
        wa, wb = st.assign.get(a), st.assign.get(b)
        if wa is None or wb is None or wa["work_id"] == wb["work_id"]:
            continue
        ratio = r["matched_pages"] / r["compared_pages"] if r["compared_pages"] else 0.0
        conf = ratio if r["rule"] == "same_number" else min(ratio, VISUAL_ONLY_CAP)
        a_first = wa["work_id"] < wb["work_id"]
        out[_pair(wa["work_id"], wb["work_id"])].append({
            "candidate_id": r["id"], "rule": r["rule"], "matched_pages": r["matched_pages"],
            "compared_pages": r["compared_pages"], "ratio": round(ratio, 3), "confidence": round(conf, 3),
            "volume_a": {"id": a if a_first else b, "title": titles[a if a_first else b]},
            "volume_b": {"id": b if a_first else a, "title": titles[b if a_first else a]},
        })
    return out


# 巻・外側の名前から付けた作品名の出所(手動・公開 DB は含めない。それらが絡む組は確認待ちのまま)
_DERIVED_TITLE_SOURCES = (SOURCE_FILENAME, SOURCE_CONTAINER, SOURCE_MERGE)


def _vouched_alias(n: _Name) -> bool:
    """作品名と完全一致したら同じ作品と言える別名か。外側の名前の別名(#214)と、1 作品だけを収めた入れ物が
    作品を決めた巻の、巻自身の名前(`_keep_own_alias`。確信度 0.9。`Isekai nonbiri sozai saishu seikatsu`)。"""
    return n.source == SOURCE_CONTAINER or (n.source == SOURCE_FILENAME and n.confidence >= AUTO_MERGE_MIN)


def _alias_evidence(st: _State) -> dict[tuple[int, int], dict]:
    """同じ名前(正規化)を持つ別々の作品の組。名前の確信度の低い方がその組の確信度。

    作品名どうし・作品名と別名の一致、または確かな出所(外側の名前以外)の別名どうしの一致を拾う。
    外側のアーカイブ名から付けた別名どうし(`Collection` など)の一致は拾わない。
    """
    active = {st.assign[v]["work_id"] for v in st.listed if v in st.assign}
    holders: dict[str, list[int]] = defaultdict(list)
    for w in st.works.values():
        for norm in w.names:
            holders[norm].append(w.id)
    out: dict[tuple[int, int], dict] = {}
    for norm, ids in holders.items():
        if len(ids) < 2:
            continue
        if is_junk_name(norm):
            continue
        for i, x in enumerate(sorted(ids)):
            for y in sorted(ids)[i + 1:]:
                wx, wy = st.works[x], st.works[y]
                if (not wx.title_manual and is_junk_name(normalize_title(wx.title))) or \
                   (not wy.title_manual and is_junk_name(normalize_title(wy.title))):
                    continue
                nx, ny = wx.names[norm], wy.names[norm]
                if not (nx.is_title or ny.is_title) and SOURCE_CONTAINER in (nx.source, ny.source):
                    continue
                conf = min(nx.confidence, ny.confidence)
                # 同じ公開 DB 項目に linked した組は「同じ作品である証拠」(#167)。別名の確信度で 0.9 に届かなくても
                # 0.9 として扱い、手動の決定が絡まなければ自動統合する。元の確信度は alias_confidence に残す
                shared = sorted(st.links.get(x, set()) & st.links.get(y, set()))
                # 外側アーカイブ名由来の別名が、別の作品の作品名と完全一致する場合も同じ作品の証拠と扱う(#214)。
                # _container_aliases は 1 作品しか入らないソースにしか別名を付けないので、作品名との完全一致は
                # 誤統合のリスクが低い。元の別名の確信度(0.5)は alias_confidence に残す。
                # ただし、両方の作品が見えている巻を持っている(active)場合に限る。空になった作品は巻の組み替えの
                # 残骸であり、単巻ファイル名由来の別名で自動統合すると過去の別名・提案を現役作品へ誤って引き継ぐため。
                container_alias_matches_title = (
                    (x in active and y in active)
                    and (
                        (nx.is_title and not ny.is_title and _vouched_alias(ny))
                        or (ny.is_title and not nx.is_title and _vouched_alias(nx))
                    )
                )
                # 名前から付けた作品名どうしの完全一致(#268)。空の DB から同期すれば巻の割り当てで同じ作品に入る組で、
                # 前の読み方で別々に作った作品の作品名が、読み方の変更で同じになったもの(`DLRAW.AC あつまれ!…` → `あつまれ!…`)
                same_derived_title = (
                    x in active and y in active and nx.is_title and ny.is_title
                    and nx.source in _DERIVED_TITLE_SOURCES and ny.source in _DERIVED_TITLE_SOURCES)
                if shared or container_alias_matches_title or same_derived_title:
                    eff = max(conf, AUTO_MERGE_MIN)
                else:
                    eff = conf
                key = (x, y)
                if key not in out or eff > out[key]["confidence"]:
                    out[key] = {"norm": norm, "confidence": round(eff, 3), "alias_confidence": round(conf, 3),
                                "shared_item": list(shared[0]) if shared else None,
                                "a": {"work": x, "name": nx.display, "source": nx.source, "is_title": nx.is_title},
                                "b": {"work": y, "name": ny.display, "source": ny.source, "is_title": ny.is_title}}
    for key, ev in _subtitle_evidence(st, active).items():
        if key not in out or ev["confidence"] > out[key]["confidence"]:
            out[key] = ev
    return out


SUBTITLE_SPACE_CONF = 0.8  # 空白だけで区切った副題(外伝の作品名のことがある)。確認待ち
_EXPLICIT_BREAK = re.compile(r"\s*[~〜～\-－‐:：、,]")
SUBTITLE_MIN_LEN = 4  # 副題を落とした側の作品名(正規化)の最短。`ゾンビ` のような短い名前は別作品の頭になりやすい


def _subtitle_evidence(st: _State, active: set[int]) -> dict[tuple[int, int], dict]:
    """日本語の作品名が、もう一方の作品名に区切り(空白・`~`・`、` など)と副題・番号を足しただけの組
    (`フシノカミ` と `フシノカミ ~辺境から始める文明再生記`、`村人転生` と `村人転生 最強のスローライフ`)。

    1 巻ずつ別の名前で届いた同じ作品で、外側の名前が無い・違うために割れたもの。区切りが無い続き
    (`異世界のんびり農家` と `異世界のんびり農家の日常`)は別作品なので拾わない(`_extends` と同じ規則)。
    """
    by_title: dict[str, list[int]] = defaultdict(list)
    for w in st.works.values():
        if w.id in active and _has_cjk(w.title):
            by_title[normalize_title(w.title)].append(w.id)
    out: dict[tuple[int, int], dict] = {}
    for long_norm, longs in by_title.items():
        long_id = longs[0]
        title = st.works[long_id].title
        for brk in _TITLE_BREAK.finditer(title):
            short_norm = normalize_title(title[:brk.start()])
            if len(short_norm) < SUBTITLE_MIN_LEN or short_norm == long_norm or short_norm not in by_title:
                continue
            # 区切りの記号(`~` `-` `:` `、`)の後ろの副題は同じ作品として自動統合する。空白だけの区切りの後ろは
            # 外伝・スピンオフの作品名のこともある(`進撃の巨人 Before the fall`)ので確認待ち
            sep = brk.group(0)
            conf = AUTO_MERGE_MIN if _EXPLICIT_BREAK.match(sep) else SUBTITLE_SPACE_CONF
            for short_id in by_title[short_norm]:
                key = _pair(short_id, long_id)
                out[key] = {"norm": short_norm, "confidence": conf, "alias_confidence": conf,
                            "shared_item": None, "subtitle": True,
                            "a": {"work": short_id, "name": st.works[short_id].title, "source": "title", "is_title": True},
                            "b": {"work": long_id, "name": title, "source": "title", "is_title": True}}
    return out


def _propose_and_merge(c, st: _State, summary: SyncSummary) -> None:
    found: dict[tuple[int, int], dict] = {}
    for pair, items in _duplicate_evidence(c, st).items():
        best = max(items, key=lambda i: i["confidence"])
        found[pair] = {"rule": "duplicate", "confidence": best["confidence"],
                       "evidence": {"duplicate": items, "best": best["confidence"], "pairs": len(items)}}
    for pair, ev in _alias_evidence(st).items():
        if pair not in found or ev["confidence"] > found[pair]["confidence"]:
            found[pair] = {"rule": "alias", "confidence": ev["confidence"], "evidence": {"alias": ev}}

    def existing_index() -> tuple[dict[int, int], dict[tuple[int, int], dict]]:
        roots = _roots(c)
        index: dict[tuple[int, int], dict] = {}
        for r in c.execute("SELECT * FROM work_merge_proposals WHERE status IN ('open', 'dismissed') ORDER BY id"):
            pair = _pair(roots.get(r["work_a"], r["work_a"]), roots.get(r["work_b"], r["work_b"]))
            if pair[0] != pair[1] and (pair not in index or r["status"] == "dismissed"):
                index[pair] = dict(r)
        return roots, index

    def merge_now(a: int, b: int, cand: dict) -> tuple[dict, dict]:
        """自動統合して、統合で変わった根と提案の index を引き直す。"""
        keep, drop = choose_survivor(c, a, b)
        merge_works(c, keep, drop, rule=cand["rule"], confidence=cand["confidence"], manual=False)
        summary.merged += 1
        return existing_index()

    roots, index = existing_index()
    active = {st.assign[v]["work_id"] for v in st.listed if v in st.assign}
    kept_open: set[int] = set()
    for pair, cand in sorted(found.items(), key=lambda kv: (-kv[1]["confidence"], kv[0])):
        a, b = _pair(roots.get(pair[0], pair[0]), roots.get(pair[1], pair[1]))
        if a == b or cand["confidence"] < PROPOSE_MIN:
            continue
        auto = (cand["confidence"] >= AUTO_MERGE_MIN and not _manual_involved(c, a) and not _manual_involved(c, b))
        if not auto and (a not in active or b not in active):
            # 見えている巻を持たない作品は画面に出ないので、確認待ちは作らない(既にある行は下で消える)。
            # 自動統合は空の作品を片付けるのに使えるので残す
            continue
        ex = index.get((a, b))
        payload = _dumps(cand["evidence"])
        if ex is not None:
            # 却下・分けた組は戻さない
            if ex["status"] != "open":
                continue
            c.execute("""UPDATE work_merge_proposals SET work_a = ?, work_b = ?, rule = ?, confidence = ?,
                         evidence = ? WHERE id = ?""", (a, b, cand["rule"], cand["confidence"], payload, ex["id"]))
            if not auto:
                # 確認待ちの提案は根拠だけ最新にする(利用者の決定を待つ)
                kept_open.add(ex["id"])
                continue
            # 後から確信度が基準を超えた確認待ちは自動で統合する(#162)。行は自動の記録として残す
            c.execute("UPDATE work_merge_proposals SET status = 'auto', decided_at = ? WHERE id = ?",
                      (db.now(), ex["id"]))
            roots, index = merge_now(a, b, cand)
            continue
        now = db.now()
        pid = c.execute(
            """INSERT INTO work_merge_proposals (work_a, work_b, rule, confidence, evidence, status, created_at, decided_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (a, b, cand["rule"], cand["confidence"], payload, "auto" if auto else "open", now,
             now if auto else None)).lastrowid
        if auto:
            roots, index = merge_now(a, b, cand)
        else:
            kept_open.add(pid)
            index[(a, b)] = {"id": pid, "status": "open"}
    # 根拠が無くなった(重複候補が却下された・巻が見えなくなった)確認待ちの提案は消す。派生の記録で、利用者の決定ではない
    for r in c.execute("SELECT id FROM work_merge_proposals WHERE status = 'open'").fetchall():
        if r["id"] not in kept_open:
            c.execute("DELETE FROM work_merge_proposals WHERE id = ?", (r["id"],))


def _container_aliases(c, st: _State) -> None:
    """外側のアーカイブ・フォルダの名前を、その中の巻がすべて同じ作品なら、その作品の別名・著者の候補にする。

    いろいろな作品を含む入れ物(`漫画/` や詰め合わせのアーカイブ)の名前は、どの作品の別名にもしない。
    """
    by_source: dict[int, tuple[set[int], str]] = {}
    for r in st.vols:
        a = st.assign.get(r["id"])
        if a is None:
            continue
        works, rel = by_source.setdefault(r["source_id"], (set(), r["rel_path"]))
        works.add(a["work_id"])
    for works, rel in by_source.values():
        if len(works) != 1 or not rel:
            continue
        work = st.works.get(next(iter(works)))
        if work is None:
            continue
        if not work.title_manual and is_junk_name(normalize_title(work.title)):
            continue
        wn = parse_work_name(_segments(rel)[-1])
        if wn is None or is_junk_name(wn.norm):
            continue
        evidence = {"source": rel, **wn.as_dict()}
        if wn.norm not in work.names:
            _insert_alias(c, work.id, wn.title, SOURCE_CONTAINER, manual=False, confidence=CONF_CONTAINER_ALIAS,
                          evidence=evidence)
        _fill_author(c, work, wn.authors, SOURCE_CONTAINER, CONF_CONTAINER_ALIAS, evidence)


# --- 手動の操作 ------------------------------------------------------------------------------


def _alive(c, work_id: int) -> int:
    root = root_of(c, work_id)
    if root is None or root != work_id:
        raise WorkNotFound(work_id)
    return root


def _row(c, table: str, key_col: str, key: int, field: str):
    return c.execute(f"SELECT * FROM {table} WHERE {key_col} = ? AND field = ?", (key, field)).fetchone()


def _stash(row) -> dict | None:
    """手で直す前の値(自動に戻すときに復元する)。すでに手動なら、その前の値を引き継ぐ。"""
    if row is None:
        return None
    ev = json.loads(row["evidence"]) if row["evidence"] else None
    if row["manual"]:
        return (ev or {}).get("previous")
    return {"value": row["value"], "source": row["source"], "confidence": row["confidence"], "evidence": ev}


def decode_value(field: str, text: str | None):
    """work_fields.value を API の形にする(genres / tags は配列、total_volumes は整数)。"""
    if text is None:
        return [] if field in LIST_FIELDS else None
    if field in LIST_FIELDS:
        try:
            value = json.loads(text)
        except ValueError:
            return []
        return [str(x) for x in value] if isinstance(value, list) else []
    if field == FIELD_TOTAL_VOLUMES:
        return int(text) if text.isdigit() else None
    return text


def _clean_field(field: str, raw) -> str | None:
    """手で入力された値を保存する形にする。空は None(手動で空にした)。"""
    if field in LIST_FIELDS:
        if raw is None:
            return None
        if not isinstance(raw, (list, tuple)):
            raise InvalidValue("一覧は配列で指定してください")
        items = list(dict.fromkeys(str(x).strip() for x in raw if str(x).strip()))
        return _dumps(items) if items else None
    if raw is not None and not isinstance(raw, str):
        raise InvalidValue("値は文字列で指定してください")
    text = (raw or "").strip()
    if field == FIELD_KIND and text and text not in KINDS:
        raise InvalidValue("種別は manga(漫画)・novel(小説)・doujinshi(同人誌)のいずれかです")
    return text or None


def edit_fields(conn: sqlite3.Connection, work_id: int, changes: dict[str, object]) -> None:
    """作品の項目を手で直す。手動の項目は再計算でも公開 DB の取得でも上書きしない(空にした場合も)。

    直す前の値は `evidence.previous` に残し、「自動に戻す」で復元する。直す前の作品名は別名にも残す
    (その名前の巻が、また新しい作品として現れないように)。
    """
    unknown = set(changes) - set(EDITABLE_FIELDS)
    if unknown:
        raise InvalidValue(f"直せない項目: {', '.join(sorted(unknown))}")
    with db.transaction(conn) as c:
        _alive(c, work_id)
        for name, raw in changes.items():
            old = _row(c, "work_fields", "work_id", work_id, name)
            previous = _stash(old)
            evidence = {"edited": True, **({"previous": previous} if previous else {})}
            if name == FIELD_TITLE:
                text = (raw or "").strip() if isinstance(raw, str) else ""
                if not normalize_title(text):
                    raise InvalidValue("作品名を入力してください")
                before, _ = _title_of(c, work_id)
                if normalize_title(before) != normalize_title(text):
                    _insert_alias(c, work_id, before, SOURCE_PREVIOUS, manual=True, confidence=1.0,
                                  evidence={"replaced_by": text}, revive=True)
                _set_field(c, work_id, FIELD_TITLE, text, SOURCE_MANUAL, True, 1.0, evidence)
            else:
                _set_field(c, work_id, name, _clean_field(name, raw), SOURCE_MANUAL, True, 1.0, evidence)


def update_fields(conn: sqlite3.Connection, work_id: int, *, title: str | None = None, author: str | None = None,
                  set_title: bool = False, set_author: bool = False) -> None:
    """作品名・著者を手で直す(`edit_fields` の旧い呼び方)。"""
    changes: dict[str, object] = {}
    if set_title:
        changes[FIELD_TITLE] = title
    if set_author:
        changes[FIELD_AUTHOR] = author
    edit_fields(conn, work_id, changes)


def reset_field(conn: sqlite3.Connection, work_id: int, field: str) -> None:
    """手動の項目を自動に戻す。直す前の値があれば復元し、無ければ項目を空に戻す(公開 DB の値は
    呼び出し側が `linking.apply_work` で入れ直す)。作品名は直す前の名前が無いと戻せない。"""
    if field not in EDITABLE_FIELDS:
        raise InvalidValue(f"直せない項目: {field}")
    with db.transaction(conn) as c:
        _alive(c, work_id)
        row = _row(c, "work_fields", "work_id", work_id, field)
        if row is None or not row["manual"]:
            raise Conflict("この項目は手動で編集されていません")
        previous = _stash(row)
        if previous is not None:
            _set_field(c, work_id, field, previous["value"], previous["source"], False, previous["confidence"],
                       previous["evidence"])
            if field == FIELD_TITLE:
                c.execute("UPDATE work_aliases SET dismissed_at = ? WHERE work_id = ? AND norm = ? AND source = ? "
                          "AND dismissed_at IS NULL",
                          (db.now(), work_id, normalize_title(previous["value"] or ""), SOURCE_PREVIOUS))
        elif field == FIELD_TITLE:
            raise Conflict("元の作品名が残っていないため、自動には戻せません")
        else:
            c.execute("DELETE FROM work_fields WHERE work_id = ? AND field = ?", (work_id, field))
            c.execute("UPDATE works SET updated_at = ? WHERE id = ?", (db.now(), work_id))


# --- 巻単位の書誌(volume_fields) --------------------------------------------------------------


def _clean_volume_value(field: str, raw) -> str | None:
    if raw is not None and not isinstance(raw, str):
        raise InvalidValue("値は文字列で指定してください")
    text = unicodedata.normalize("NFKC", raw or "").strip()
    if not text:
        return None
    if field == "isbn":
        digits = re.sub(r"[\s-]", "", text).upper()
        if not re.fullmatch(r"\d{13}|\d{9}[\dX]", digits):
            raise InvalidValue("ISBN は 10 桁か 13 桁で入力してください")
        return digits
    if field == "released" and not re.fullmatch(r"\d{4}(-\d{2}(-\d{2})?)?", text):
        raise InvalidValue("発売日は YYYY、YYYY-MM、YYYY-MM-DD のいずれかで入力してください")
    return text


def _set_volume_field(c, volume_id: int, field: str, value: str | None, source: str, manual: bool,
                      confidence: float | None, evidence: dict | None) -> None:
    c.execute(
        """INSERT INTO volume_fields (volume_id, field, value, source, manual, confidence, evidence, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT (volume_id, field) DO UPDATE SET value = excluded.value, source = excluded.source,
               manual = excluded.manual, confidence = excluded.confidence, evidence = excluded.evidence,
               updated_at = excluded.updated_at""",
        (volume_id, field, value, source, int(manual), confidence, _dumps(evidence) if evidence else None, db.now()))


def edit_volume_fields(conn: sqlite3.Connection, volume_id: int, changes: dict[str, object]) -> None:
    """巻の書誌(出版社・レーベル・発売日・ISBN)を手で直す。再取得・再計算で上書きしない。"""
    unknown = set(changes) - set(VOLUME_EDITABLE_FIELDS)
    if unknown:
        raise InvalidValue(f"直せない項目: {', '.join(sorted(unknown))}")
    cleaned = {name: _clean_volume_value(name, raw) for name, raw in changes.items()}
    with db.transaction(conn) as c:
        if c.execute("SELECT 1 FROM volumes WHERE id = ?", (volume_id,)).fetchone() is None:
            raise VolumeNotFound(volume_id)
        for name, value in cleaned.items():
            previous = _stash(_row(c, "volume_fields", "volume_id", volume_id, name))
            _set_volume_field(c, volume_id, name, value, SOURCE_MANUAL, True, 1.0,
                              {"edited": True, **({"previous": previous} if previous else {})})


def reset_volume_field(conn: sqlite3.Connection, volume_id: int, field: str) -> None:
    """巻の手動の項目を自動に戻す(直す前の値を復元、無ければ空。公開 DB の値は `linking.apply_work` が入れ直す)。"""
    if field not in VOLUME_EDITABLE_FIELDS:
        raise InvalidValue(f"直せない項目: {field}")
    with db.transaction(conn) as c:
        if c.execute("SELECT 1 FROM volumes WHERE id = ?", (volume_id,)).fetchone() is None:
            raise VolumeNotFound(volume_id)
        row = _row(c, "volume_fields", "volume_id", volume_id, field)
        if row is None or not row["manual"]:
            raise Conflict("この項目は手動で編集されていません")
        previous = _stash(row)
        if previous is not None:
            _set_volume_field(c, volume_id, field, previous["value"], previous["source"], False,
                              previous["confidence"], previous["evidence"])
        else:
            c.execute("DELETE FROM volume_fields WHERE volume_id = ? AND field = ?", (volume_id, field))


def transfer_volume_fields(c: sqlite3.Connection, from_id: int, to_id: int) -> None:
    """巻の書誌(手動の編集を含む)を別の巻へ写す。整理で巻が CBZ の新しい巻に置き換わるときに使う。
    写し先で手動の項目は、写し元の自動の値で上書きしない。"""
    for r in c.execute("SELECT * FROM volume_fields WHERE volume_id = ?", (from_id,)).fetchall():
        cur = _row(c, "volume_fields", "volume_id", to_id, r["field"])
        if cur is not None and cur["manual"] and not r["manual"]:
            continue
        _set_volume_field(c, to_id, r["field"], r["value"], r["source"], bool(r["manual"]), r["confidence"],
                          json.loads(r["evidence"]) if r["evidence"] else None)


def add_alias(conn: sqlite3.Connection, work_id: int, alias: str) -> None:
    with db.transaction(conn) as c:
        _alive(c, work_id)
        text = alias.strip()
        norm = normalize_title(text)
        if not norm:
            raise InvalidValue("別名を入力してください")
        if norm == normalize_title(_title_of(c, work_id)[0]):
            raise Conflict("作品名と同じです")
        _insert_alias(c, work_id, text, SOURCE_MANUAL, manual=True, confidence=1.0, evidence={"edited": True},
                      revive=True)


def dismiss_alias(conn: sqlite3.Connection, work_id: int, alias_id: int) -> None:
    """別名を外す。行は残し(dismissed_at)、再計算で同じ別名を戻さない。"""
    with db.transaction(conn) as c:
        _alive(c, work_id)
        cur = c.execute("UPDATE work_aliases SET dismissed_at = ? WHERE id = ? AND work_id = ?",
                        (db.now(), alias_id, work_id))
        if cur.rowcount == 0:
            raise WorkNotFound(alias_id)


def merge_manual(conn: sqlite3.Connection, work_id: int, other_id: int) -> None:
    """work_id に other_id を統合する(手動)。巻は手動の割り当てになる。"""
    with db.transaction(conn) as c:
        keep = _alive(c, work_id)
        drop = _alive(c, other_id)
        if keep == drop:
            raise Conflict("同じ作品です")
        a, b = _pair(keep, drop)
        now = db.now()
        evidence = {"manual": True}
        c.execute("""INSERT INTO work_merge_proposals (work_a, work_b, rule, confidence, evidence, status, created_at,
                     decided_at) VALUES (?, ?, 'manual', 1.0, ?, 'approved', ?, ?)""", (a, b, _dumps(evidence), now, now))
        merge_works(c, keep, drop, rule="manual", confidence=1.0, manual=True)


def assign_volumes(conn: sqlite3.Connection, volume_ids: list[int], work_id: int | None, title: str | None = None) -> int:
    """巻を別の作品へ付け替える(work_id が None なら新しい作品に分ける)。手動の割り当てになり、再計算で動かさない。

    新しい作品に分けたときは、元の作品との組を「分離済み」(却下)にして、重複候補の証拠で自動的に戻されないようにする。
    付け替えた作品の id を返す。
    """
    if not volume_ids:
        raise InvalidValue("巻を選んでください")
    with db.transaction(conn) as c:
        rows = {}
        for vid in dict.fromkeys(volume_ids):
            r = c.execute("""SELECT v.id, v.title, v.container_path, v.entry_prefix, v.inner_path, v.parent_id,
                                    s.id AS source_id, s.rel_path
                             FROM volumes v JOIN sources s ON s.id = v.source_id WHERE v.id = ?""", (vid,)).fetchone()
            if r is None:
                raise VolumeNotFound(vid)
            rows[vid] = r
        created = work_id is None
        if created:
            text = (title or "").strip()
            first = next(iter(rows.values()))
            if text:
                if not normalize_title(text):
                    raise InvalidValue("作品名を入力してください")
                target = _new_work(c, text, source=SOURCE_MANUAL, manual=True, confidence=1.0, evidence={"edited": True})
            else:
                own = own_name(first)
                name = own.name.title if own else first["title"]
                target = _new_work(c, name, source=SOURCE_FILENAME, manual=False,
                                   confidence=own.confidence if own else CONF_ENCLOSING_NAME,
                                   evidence=own.evidence() if own else {"name": first["title"]})
        else:
            target = _alive(c, work_id)
        olds: set[int] = set()
        for vid in rows:
            prev = c.execute("SELECT work_id FROM work_volumes WHERE volume_id = ?", (vid,)).fetchone()
            if prev is not None and prev["work_id"] != target:
                olds.add(prev["work_id"])
            _put_assignment(c, vid, target, SOURCE_MANUAL, True, 1.0,
                            {"moved_from": prev["work_id"] if prev else None})
        if created:
            for old in olds:
                _mark_separated(c, old, target)
        return target


def _mark_separated(c, a: int, b: int) -> None:
    """2 作品を別々のものとして記録する(却下済みの提案)。自動の統合・提案の対象から外す。"""
    roots = _roots(c)
    pair = _pair(roots.get(a, a), roots.get(b, b))
    now = db.now()
    for r in c.execute("SELECT * FROM work_merge_proposals WHERE status IN ('open', 'dismissed')").fetchall():
        if _pair(roots.get(r["work_a"], r["work_a"]), roots.get(r["work_b"], r["work_b"])) == pair:
            c.execute("UPDATE work_merge_proposals SET status = 'dismissed', decided_at = ? WHERE id = ?", (now, r["id"]))
            return
    c.execute("""INSERT INTO work_merge_proposals (work_a, work_b, rule, confidence, evidence, status, created_at,
                 decided_at) VALUES (?, ?, 'manual', 1.0, ?, 'dismissed', ?, ?)""",
              (pair[0], pair[1], _dumps({"separated": True}), now, now))


def decide_proposal(conn: sqlite3.Connection, proposal_id: int, action: str) -> dict:
    """提案を承認(統合)・却下・却下の取り消しにする。action は approve / dismiss / restore。"""
    with db.transaction(conn) as c:
        row = c.execute("SELECT * FROM work_merge_proposals WHERE id = ?", (proposal_id,)).fetchone()
        if row is None:
            raise ProposalNotFound(proposal_id)
        roots = _roots(c)
        a, b = roots.get(row["work_a"], row["work_a"]), roots.get(row["work_b"], row["work_b"])
        now = db.now()
        if action == "approve":
            if row["status"] != "open":
                raise Conflict("この提案は確認待ちではありません")
            if a == b:
                raise Conflict("すでに同じ作品です")
            keep, drop = choose_survivor(c, a, b)
            merge_works(c, keep, drop, rule=row["rule"], confidence=row["confidence"], manual=True)
            c.execute("UPDATE work_merge_proposals SET status = 'approved', decided_at = ? WHERE id = ?", (now, proposal_id))
        elif action == "dismiss":
            if row["status"] != "open":
                raise Conflict("この提案は確認待ちではありません。統合済みの作品は、巻を別の作品へ移して分けてください")
            c.execute("UPDATE work_merge_proposals SET status = 'dismissed', decided_at = ? WHERE id = ?", (now, proposal_id))
        elif action == "restore":
            if row["status"] != "dismissed":
                raise Conflict("この提案は却下されていません")
            c.execute("UPDATE work_merge_proposals SET status = 'open', decided_at = NULL WHERE id = ?", (proposal_id,))
        else:
            raise InvalidValue(f"未対応の操作: {action}")
        return {"id": proposal_id, "status": c.execute("SELECT status FROM work_merge_proposals WHERE id = ?",
                                                      (proposal_id,)).fetchone()["status"]}


# --- 参照(API 用) ----------------------------------------------------------------------------

_VOLUME_SELECT = """SELECT v.id, v.title, v.page_count, v.format, v.number_kind, v.number_start, v.number_end, v.number_evidence,
       pv.title AS parent_title, s.rel_path, s.offline AS source_offline, r.online AS root_online, p.page, p.finished,
       wv.work_id, wv.source AS a_source, wv.manual AS a_manual, wv.confidence AS a_confidence,
       wv.evidence AS a_evidence"""
_VOLUME_FROM = (LISTED_VOLUMES_JOIN + " JOIN work_volumes wv ON wv.volume_id = v.id"
                " LEFT JOIN reading_progress p ON p.volume_id = v.id ")


def _volume_json(r, dup_counts: dict[int, int], meta: dict[int, dict] | None = None) -> dict:
    number = None
    if r["number_kind"] is not None:
        number = {"kind": r["number_kind"], "start": r["number_start"], "end": r["number_end"],
                  "evidence": r["number_evidence"]}
    return {
        "id": r["id"], "title": r["title"], "parent_title": r["parent_title"], "page_count": r["page_count"],
        "format": "images" if r["format"] in (None, "pdf") else r["format"], "offline": is_offline(r),
        "progress": None if r["page"] is None else {"page": r["page"], "finished": bool(r["finished"])},
        "volume_number": number, "duplicate_count": dup_counts.get(r["id"], 0),
        "assignment": {"source": r["a_source"], "manual": bool(r["a_manual"]), "confidence": r["a_confidence"],
                       "evidence": json.loads(r["a_evidence"]) if r["a_evidence"] else None},
        **({"metadata": meta.get(r["id"], {})} if meta is not None else {}),
    }


def number_summary(vols: list[dict]) -> dict:
    """巻・話ごとの最大番号と、1 から最大までで見つからない番号(欠け)。"""
    out: dict[str, dict] = {}
    for kind in ("volume", "episode"):
        ranges = [(v["volume_number"]["start"], v["volume_number"]["end"]) for v in vols
                  if v["volume_number"] and v["volume_number"]["kind"] == kind]
        if not ranges:
            out[kind] = {"max": None, "missing": [], "limited": False}
            continue
        top = max(e for _, e in ranges)
        if kind == "episode" and out["volume"]["max"] is not None:
            # 巻の番号を持つ作品の話・章は、巻の中身であって別の系列ではない。1..最大 の欠けを数えない(#248)
            out[kind] = {"max": top, "missing": [], "limited": False}
            continue
        if top > MAX_MISSING_SPAN:
            out[kind] = {"max": top, "missing": [], "limited": True}
            continue
        covered: set[int] = set()
        for s, e in ranges:
            covered.update(range(s, e + 1))
        out[kind] = {"max": top, "missing": [n for n in range(1, top + 1) if n not in covered], "limited": False}
    return out


def build_slots(vols: list[dict]) -> tuple[list[dict], list[dict]]:
    """巻を番号ごとにまとめる(同じ番号の巻は重複としてまとめて表示)。番号の無い巻は別に返す。"""
    groups: dict[tuple, list[dict]] = defaultdict(list)
    unnumbered = []
    for v in vols:
        n = v["volume_number"]
        if n is None:
            unnumbered.append(v)
        else:
            groups[(0 if n["kind"] == "volume" else 1, n["start"], n["end"])].append(v)
    slots = []
    for (k, start, end), items in sorted(groups.items()):
        items.sort(key=lambda v: natural_key(v["title"]))
        unit = "巻" if k == 0 else "話"
        label = f"{start}{unit}" if start == end else f"{start}〜{end}{unit}"
        slots.append({"kind": "volume" if k == 0 else "episode", "start": start, "end": end, "label": label,
                      "duplicate": len(items) > 1, "volumes": items})
    unnumbered.sort(key=lambda v: natural_key(v["title"]))
    return slots, unnumbered


def next_volume(conn: sqlite3.Connection, volume_id: int) -> dict | None:
    """同じ作品の一覧の並び(番号順 → 番号なしは名前順)で次の巻。

    同じ番号の重複は同じ巻とみなして次に数えず、次の番号に重複があればその並びの先頭を返す。
    次の巻がオフラインでも返す(リーダーが「オフライン」と案内できるように)。作品に割り当てられていない巻は None。
    """
    row = conn.execute("SELECT work_id FROM work_volumes WHERE volume_id = ?", (volume_id,)).fetchone()
    if row is None:
        return None
    vols = [_volume_json(r, {}) for r in conn.execute(
        _VOLUME_SELECT + _VOLUME_FROM + LISTED_VOLUMES_WHERE + " AND wv.work_id = ?", (row["work_id"],))]
    slots, unnumbered = build_slots(vols)
    nxt = None
    for i, slot in enumerate(slots):
        if any(v["id"] == volume_id for v in slot["volumes"]):
            nxt = slots[i + 1]["volumes"][0] if i + 1 < len(slots) else (unnumbered[0] if unnumbered else None)
            break
    else:
        for i, v in enumerate(unnumbered):
            if v["id"] == volume_id:
                nxt = unnumbered[i + 1] if i + 1 < len(unnumbered) else None
                break
    if nxt is None:
        return None
    return {"id": nxt["id"], "title": nxt["title"], "volume_number": nxt["volume_number"],
            "offline": nxt["offline"]}


def _work_rows(c) -> dict[int, dict]:
    """生きている作品の項目・別名。"""
    works: dict[int, dict] = {}
    for r in c.execute("SELECT id FROM works WHERE merged_into IS NULL"):
        works[r["id"]] = {"id": r["id"], "title": "", "title_source": SOURCE_FILENAME, "title_manual": False,
                          "title_confidence": None, "title_evidence": None, "author": None,
                          "author_source": None, "author_manual": False, "author_confidence": None,
                          "aliases": [], "fields": {}}
    for r in c.execute("SELECT * FROM work_fields"):
        w = works.get(r["work_id"])
        if w is None or r["field"] in RETIRED_FIELDS:
            continue
        ev = json.loads(r["evidence"]) if r["evidence"] else {}
        w["fields"][r["field"]] = {"value": decode_value(r["field"], r["value"]), "source": r["source"],
                                   "manual": bool(r["manual"]), "confidence": r["confidence"],
                                   "sources": (ev.get("link") or {}).get("sources") or []}
        key = "title" if r["field"] == FIELD_TITLE else "author" if r["field"] == FIELD_AUTHOR else None
        if key:
            w[key] = r["value"] if key == "author" else (r["value"] or "")
            w[f"{key}_source"], w[f"{key}_manual"] = r["source"], bool(r["manual"])
            w[f"{key}_confidence"] = r["confidence"]
            if key == "title":
                w["title_evidence"] = json.loads(r["evidence"]) if r["evidence"] else None
    for r in c.execute("SELECT * FROM work_aliases WHERE dismissed_at IS NULL ORDER BY id"):
        w = works.get(r["work_id"])
        if w is not None:
            w["aliases"].append({"id": r["id"], "alias": r["alias"], "source": r["source"], "manual": bool(r["manual"]),
                                 "confidence": r["confidence"],
                                 "evidence": json.loads(r["evidence"]) if r["evidence"] else None})
    return works


def _volume_meta(c) -> dict[int, dict]:
    meta: dict[int, dict] = defaultdict(dict)
    for r in c.execute("SELECT * FROM volume_fields"):
        ev = json.loads(r["evidence"]) if r["evidence"] else {}
        meta[r["volume_id"]][r["field"]] = {"value": r["value"], "source": r["source"], "manual": bool(r["manual"]),
                                            "confidence": r["confidence"], "from": ev.get("source"), "url": ev.get("url")}
    return meta


def _listed_volumes(c, with_meta: bool = False, root_id: int | None = None) -> dict[int, list[dict]]:
    """作品 id → 一覧に見えている巻。root_id を渡すと、その蔵書ルートの巻だけ(蔵書の切り替え #85)。"""
    dups = candidate_counts(c)
    meta = _volume_meta(c) if with_meta else None
    by_work: dict[int, list[dict]] = defaultdict(list)
    where, params = _root_condition(root_id)
    for r in c.execute(_VOLUME_SELECT + _VOLUME_FROM + LISTED_VOLUMES_WHERE + where, params):
        by_work[r["work_id"]].append(_volume_json(r, dups, meta))
    return by_work


def _root_condition(root_id: int | None) -> tuple[str, tuple]:
    """一覧に見えている巻の条件(LISTED_VOLUMES_WHERE)に足す、蔵書ルートの絞り込み。"""
    return ("", ()) if root_id is None else (" AND r.id = ?", (root_id,))


def _open_proposal_counts(c) -> dict[int, int]:
    roots = _roots(c)
    counts: dict[int, int] = defaultdict(int)
    for r in c.execute("SELECT work_a, work_b FROM work_merge_proposals WHERE status = 'open'"):
        a, b = roots.get(r["work_a"], r["work_a"]), roots.get(r["work_b"], r["work_b"])
        if a != b:
            counts[a] += 1
            counts[b] += 1
    return dict(counts)


def cover_volume_id(vols: list[dict]) -> int | None:
    """作品の表紙にする巻: 画像の巻と EPUB(小説・オフライン・0 ページを除く)のうち、番号が最小のもの。

    番号の無い巻は番号のある巻の後ろ。同じ番号(重複)・番号の無い巻どうしでは EPUB を先にする(#250。
    「ページを保存」の zip の本文ページより、本が自分で持つ表紙画像の方が表紙らしい)。それでも並ぶなら巻の名前の自然順。
    表紙は画像の巻ならその 1 ページ目、EPUB なら OPF の表紙画像(spinebox/thumbs.py)。
    """
    candidates = [v for v in vols if v["format"] in ("images", "epub") and not v["offline"] and v["page_count"] != 0]
    if not candidates:
        return None
    best = min(candidates, key=lambda v: (v["volume_number"] is None, (v["volume_number"] or {}).get("start") or 0,
                                          v["format"] != "epub", natural_key(v["title"]), v["id"]))
    return best["id"]


def _summary(w: dict, vols: list[dict], open_count: int, link_pending: int = 0) -> dict:
    nums = number_summary(vols)
    slots, _ = build_slots(vols)
    return {
        **{k: w[k] for k in ("id", "title", "title_source", "title_manual", "author", "author_source", "author_manual")},
        "aliases": [{k: a[k] for k in ("id", "alias", "source", "manual", "confidence")} for a in w["aliases"]],
        "volume_count": len(vols),
        "offline_count": sum(1 for v in vols if v["offline"]),
        "finished_count": sum(1 for v in vols if v["progress"] and v["progress"]["finished"]),
        "reading_state": search.reading_state(len(vols), sum(1 for v in vols if v["progress"] and v["progress"]["finished"]),
                                              sum(1 for v in vols if v["progress"])),
        "duplicate_slots": sum(1 for s in slots if s["duplicate"]),
        "max_volume": nums["volume"]["max"],
        "missing_volumes": nums["volume"]["missing"],
        "missing_episodes": nums["episode"]["missing"],
        "pending_proposals": open_count,
        "merged": False,
        "kind": (w["fields"].get(FIELD_KIND) or {}).get("value"),
        "publisher": (w["fields"].get(FIELD_PUBLISHER) or {}).get("value"),
        "genres": (w["fields"].get(FIELD_GENRES) or {}).get("value") or [],
        "tags": (w["fields"].get(FIELD_TAGS) or {}).get("value") or [],
        "link_pending": link_pending,
        "cover_volume_id": cover_volume_id(vols),
    }


def _desc_text(text: str) -> tuple[int, ...]:
    return tuple(-ord(ch) for ch in text)


def _pad_released(text: str | None) -> str:
    """発売日(`YYYY` / `YYYY-MM` / `YYYY-MM-DD`)を `YYYY-MM-DD` に揃える。
    足りない月・日は `00` にし、同じ年の中では月・日が分かるものを先(新しい方)にする(follows._desc と同じ規則)。"""
    if not text:
        return ""
    parts = [p.zfill(2) for p in text.strip().split("-")[:3]]
    parts += ["00"] * (3 - len(parts))
    return "-".join(parts)


def _sort_dates(c: sqlite3.Connection, root_id: int | None) -> tuple[dict[int, str], dict[int, str], dict[int, str]]:
    """作品 id → (最新の読書日時, 最新の追加日時, 最新の発売日(pad 済み))。
    見えている巻(root_id 指定時はその蔵書ルートの巻だけ)から集計し、発売日は公開 DB 連携(work_links.item.released)も見る。"""
    where, params = _root_condition(root_id)
    read_at: dict[int, str] = {}
    added_at: dict[int, str] = {}
    for r in c.execute(
        "SELECT wv.work_id, MAX(p.updated_at) AS last_read, MAX(v.created_at) AS last_added "
        + _VOLUME_FROM + LISTED_VOLUMES_WHERE + where + " GROUP BY wv.work_id", params
    ):
        wid = r["work_id"]
        if r["last_read"]:
            read_at[wid] = r["last_read"]
        if r["last_added"]:
            added_at[wid] = r["last_added"]
    released_at: dict[int, str] = {}
    for r in c.execute(
        "SELECT wv.work_id, vf.value " + LISTED_VOLUMES_JOIN
        + " JOIN work_volumes wv ON wv.volume_id = v.id"
        + " JOIN volume_fields vf ON vf.volume_id = v.id AND vf.field = 'released' "
        + LISTED_VOLUMES_WHERE + where + " AND vf.value IS NOT NULL AND vf.value <> ''", params
    ):
        wid = r["work_id"]
        padded = _pad_released(r["value"])
        if padded and padded > released_at.get(wid, ""):
            released_at[wid] = padded
    for r in c.execute("SELECT work_id, item FROM work_links WHERE status = 'linked' AND item IS NOT NULL"):
        wid = r["work_id"]
        try:
            rel = json.loads(r["item"]).get("released")
        except ValueError:
            rel = None
        padded = _pad_released(str(rel) if rel else None)
        if padded and padded > released_at.get(wid, ""):
            released_at[wid] = padded
    return read_at, added_at, released_at


def _sort_works(items: list[dict], sort: str, c: sqlite3.Connection, root_id: int | None) -> None:
    if sort == "title":
        items.sort(key=lambda s: (natural_key(s["title"]), s["id"]))
        return
    if sort == "random":
        random.shuffle(items)
        return
    read_at, added_at, released_at = _sort_dates(c, root_id)
    if sort == "recent_read":
        items.sort(key=lambda s: (s["id"] not in read_at, _desc_text(read_at.get(s["id"], "")),
                                  natural_key(s["title"]), s["id"]))
    elif sort == "added_desc":
        items.sort(key=lambda s: (s["id"] not in added_at, _desc_text(added_at.get(s["id"], "")),
                                  natural_key(s["title"]), s["id"]))
    elif sort == "added_asc":
        items.sort(key=lambda s: (s["id"] not in added_at, added_at.get(s["id"], ""),
                                  natural_key(s["title"]), s["id"]))
    elif sort == "released_desc":
        items.sort(key=lambda s: (s["id"] not in released_at, _desc_text(released_at.get(s["id"], "")),
                                  natural_key(s["title"]), s["id"]))
    elif sort == "released_asc":
        items.sort(key=lambda s: (s["id"] not in released_at, released_at.get(s["id"], ""),
                                  natural_key(s["title"]), s["id"]))


def list_works(conn: sqlite3.Connection, q: str | None = None, filters: dict[str, str | None] | None = None,
               root_id: int | None = None, read_later_only: bool = False, sort: str | None = None) -> dict:
    """見えている巻を持つ作品の一覧と、作品に割り当てられていない巻の数。

    q は全文検索の語(空白区切りで AND)、filters は author / kind / genre / tag / publisher / status(読書状態)の絞り込み。
    read_later_only は後で読む(#173)を付けた作品だけにする。
    sort は並び順(#273。WORK_SORTS。省略・空は title)。
    root_id は蔵書の切り替え(#85): その蔵書ルートに巻がある作品だけにし、巻数・読書状態などもそのルートの巻で数える。
    total は絞る前の作品数(蔵書の切り替えの後)、facets は絞り込みの候補(同じく絞る前の作品から作る)。
    """
    sort_key = (sort or "").strip() or "title"
    if sort_key not in WORK_SORTS:
        raise InvalidValue(f"未対応の並び順: {sort_key}")
    wanted = search.check_filters(filters or {})
    try:
        search.prepare(conn)
    except sqlite3.OperationalError:
        if not search.index_exists(conn):
            raise
        # 書き込みのロックが取れない(長い書き込みの最中)。少し古い索引のまま引き、次の一覧で追いつく
        log.warning("検索の索引を更新できませんでした(古い索引で検索します)", exc_info=True)
    hits = search.match_ids(conn, q)
    values = search.field_values(conn)
    works = _work_rows(conn)
    vols = _listed_volumes(conn, root_id=root_id)
    opens = _open_proposal_counts(conn)
    pending_links = {r["work_id"]: r["n"] for r in conn.execute(
        "SELECT work_id, COUNT(*) AS n FROM work_links WHERE status = 'pending' GROUP BY work_id")}
    roots = _roots(conn)
    merged_roots = set()
    for r in conn.execute("SELECT work_a, work_b FROM work_merge_proposals WHERE status IN ('auto', 'approved')"):
        a, b = roots.get(r["work_a"], r["work_a"]), roots.get(r["work_b"], r["work_b"])
        if a == b:
            merged_roots.add(a)
    later_ids = {r[0] for r in conn.execute("SELECT work_id FROM read_later")}
    out = []
    for wid, w in works.items():
        if wid not in vols:
            continue
        s = _summary(w, vols[wid], opens.get(wid, 0), pending_links.get(wid, 0))
        s["merged"] = wid in merged_roots
        s["read_later"] = wid in later_ids
        out.append(s)
    _sort_works(out, sort_key, conn, root_id)
    where, params = _root_condition(root_id)
    total = conn.execute("SELECT COUNT(*) " + LISTED_VOLUMES_JOIN + LISTED_VOLUMES_WHERE + where, params).fetchone()[0]
    assigned = sum(len(v) for v in vols.values())
    facets = search.facets(values, [s["id"] for s in out])
    shown = [s for s in out if (hits is None or s["id"] in hits)
             and search.matches_filters(values.get(s["id"], {}), s["reading_state"], wanted)]
    if read_later_only:
        shown = [s for s in shown if s.get("read_later")]
    return {"works": shown, "total": len(out), "facets": facets, "unassigned": total - assigned}


def _brief(w: dict, vols: list[dict]) -> dict:
    ordered = sorted(vols, key=lambda v: ((v["volume_number"] or {}).get("start", 10 ** 9), natural_key(v["title"])))
    return {
        **{k: w[k] for k in ("id", "title", "author")},
        "aliases": [a["alias"] for a in w["aliases"]],
        "volume_count": len(vols),
        "volumes": [{"id": v["id"], "title": v["title"], "volume_number": v["volume_number"]}
                    for v in ordered[:SAMPLE_VOLUMES]],
    }


def _proposal_json(r, works: dict[int, dict], vols: dict[int, list[dict]], roots: dict[int, int]) -> dict | None:
    a, b = roots.get(r["work_a"], r["work_a"]), roots.get(r["work_b"], r["work_b"])
    wa, wb = works.get(a), works.get(b)
    if wa is None or wb is None:
        return None
    return {"id": r["id"], "rule": r["rule"], "confidence": r["confidence"], "status": r["status"],
            "evidence": json.loads(r["evidence"]), "decided_at": r["decided_at"],
            "a": _brief(wa, vols.get(a, [])), "b": _brief(wb, vols.get(b, []))}


def _merge_json(conn, r, roots: dict[int, int]) -> dict:
    """統合済み(auto / approved)の提案。どの作品がどの作品に統合されたかを、作品名で示す。"""
    return {"id": r["id"], "rule": r["rule"], "confidence": r["confidence"], "status": r["status"],
            "evidence": json.loads(r["evidence"]), "decided_at": r["decided_at"],
            "work_id": roots.get(r["work_a"], r["work_a"]),
            "titles": [_title_of(conn, r["work_a"])[0], _title_of(conn, r["work_b"])[0]]}


def _proposal_matches(item: dict, wanted: list[str]) -> bool:
    """提案の組(a・b)の作品名・著者・別名に、検索語がすべて含まれるか(#208)。語は a・b を合わせて判定する。
    正規化は作品の検索・名寄せと同じ(`search.normalize`)。"""
    texts = []
    for side in (item["a"], item["b"]):
        texts.extend([side["title"], side["author"], *side["aliases"]])
    hay = "\n".join(search.normalize(t) for t in texts if t)
    return all(t in hay for t in wanted)


def list_proposals(conn: sqlite3.Connection, status: str = "open", q: str | None = None) -> list[dict]:
    """統合の提案。status は open(確認待ち)/ dismissed(却下・分離)/ applied(統合済み: 自動と承認)。
    q は作品名・著者・別名の絞り込み(空白区切りで AND、open / dismissed のみ。#208)。"""
    wanted = {"open": ("open",), "dismissed": ("dismissed",), "applied": ("auto", "approved")}.get(status)
    if wanted is None:
        raise InvalidValue("status は open / dismissed / applied")
    works = _work_rows(conn)
    vols = _listed_volumes(conn)
    roots = _roots(conn)
    marks = ",".join("?" * len(wanted))
    words = search.terms(q) if status != "applied" else []
    out = []
    for r in conn.execute(f"SELECT * FROM work_merge_proposals WHERE status IN ({marks}) ORDER BY confidence DESC, id",
                          wanted):
        a, b = roots.get(r["work_a"], r["work_a"]), roots.get(r["work_b"], r["work_b"])
        if status == "applied":
            if a == b and a in vols:
                out.append(_merge_json(conn, r, roots))
        elif a != b and a in vols and b in vols:
            item = _proposal_json(r, works, vols, roots)
            if item is not None and (not words or _proposal_matches(item, words)):
                out.append(item)
    return out


def _link_json(row) -> dict:
    ev = json.loads(row["evidence"]) if row["evidence"] else {}
    candidates = json.loads(row["candidates"]) if row["candidates"] else []
    return {
        "source": row["source"], "label": SOURCE_LABELS.get(row["source"], row["source"]), "status": row["status"],
        "manual": bool(row["manual"]), "confidence": row["confidence"], "source_id": row["source_id"],
        "attempted_at": row["attempted_at"], "checked_at": row["checked_at"],
        "failure": json.loads(row["failure"]) if row["failure"] else None,
        # 候補の出版社・ジャンルは保存した英語のまま持ち、画面へ出すときに日本語にする(#74)
        "linked": ja_terms.localize_summary(ev["summary"]) if row["status"] == "linked" and ev.get("summary") else None,
        "decision": ev.get("decision"), "margin": ev.get("margin"), "hold": ev.get("hold"),
        "responses": ev.get("responses", []),
        "candidates": [ja_terms.localize_summary(c["summary"]) for c in candidates],
    }


def _links_of(rows: dict, enabled: tuple[str, ...] = ()) -> list[dict]:
    names = [n for n in SOURCE_LABELS if n in enabled or n in rows] + [n for n in rows if n not in SOURCE_LABELS]
    out = []
    for name in names:
        if name in rows:
            out.append(_link_json(rows[name]))
        else:
            out.append({"source": name, "label": SOURCE_LABELS.get(name, name), "status": None, "manual": False,
                        "confidence": None, "source_id": None, "attempted_at": None, "checked_at": None,
                        "failure": None, "linked": None, "decision": None, "margin": None, "hold": None,
                        "responses": [],
                        "candidates": []})
    return out


def links_json(conn: sqlite3.Connection, work_id: int, enabled: tuple[str, ...] = ()) -> list[dict]:
    """作品の公開 DB との連携(取得元ごと)。取得元が有効なのにまだ取得していなければ status = None で出す。"""
    rows = {r["source"]: r for r in conn.execute("SELECT * FROM work_links WHERE work_id = ?", (work_id,))}
    return _links_of(rows, enabled)


def link_pending_work_ids(conn: sqlite3.Connection) -> list[int]:
    """公開 DB の候補が確認待ちの作品(一覧に見えている巻を持つ生きた作品だけ)。並びは作品の一覧(作品名順)と同じ。

    作品の一覧(list_works)を組み立てずに引く。確認の画面の件数は画面を切り替えるたびに呼ばれるので(#291)。
    """
    rows = conn.execute(
        "SELECT w.id, (SELECT value FROM work_fields f WHERE f.work_id = w.id AND f.field = ?) AS title "
        "FROM works w WHERE w.merged_into IS NULL "
        "AND EXISTS (SELECT 1 FROM work_links l WHERE l.work_id = w.id AND l.status = 'pending') "
        "AND w.id IN (SELECT wv.work_id " + LISTED_VOLUMES_JOIN + " JOIN work_volumes wv ON wv.volume_id = v.id"
        + LISTED_VOLUMES_WHERE + ")", (FIELD_TITLE,)).fetchall()
    return [r["id"] for r in sorted(rows, key=lambda r: (natural_key(r["title"] or ""), r["id"]))]


def open_proposal_count(conn: sqlite3.Connection) -> int:
    """確認待ちの統合の提案の数。list_proposals(conn, "open") の長さと同じ(両方の作品が生きていて、一覧に見えている巻を持つ組)。"""
    listed = {r[0] for r in conn.execute(
        "SELECT DISTINCT wv.work_id " + LISTED_VOLUMES_JOIN + " JOIN work_volumes wv ON wv.volume_id = v.id"
        + LISTED_VOLUMES_WHERE)}
    alive = {r[0] for r in conn.execute("SELECT id FROM works WHERE merged_into IS NULL")} & listed
    roots = _roots(conn)
    n = 0
    for r in conn.execute("SELECT work_a, work_b FROM work_merge_proposals WHERE status = 'open'"):
        a, b = roots.get(r["work_a"], r["work_a"]), roots.get(r["work_b"], r["work_b"])
        if a != b and a in alive and b in alive:
            n += 1
    return n


def get_works(conn: sqlite3.Connection, work_ids: list[int], sources: tuple[str, ...] = ()) -> list[dict]:
    """作品の詳細を、渡した順にまとめて返す。全作品の項目・全巻・提案・連携は作品の数によらず 1 回ずつ引く
    (確認の画面は確認待ちの作品を全部並べるので、1 作品ずつ引くと作品の数 × 蔵書全体になる。#291)。"""
    works = _work_rows(conn)
    for wid in work_ids:
        if wid not in works:
            raise WorkNotFound(wid)
    wanted = set(work_ids)
    all_vols = _listed_volumes(conn, with_meta=True)
    marked = {r[0] for r in conn.execute("SELECT volume_id FROM bookmarks WHERE volume_id IS NOT NULL")}
    roots = _roots(conn)
    proposals: dict[int, list[dict]] = defaultdict(list)
    merges: dict[int, list[dict]] = defaultdict(list)
    for r in conn.execute("SELECT * FROM work_merge_proposals WHERE status IN ('open', 'auto', 'approved') "
                          "ORDER BY confidence DESC, id"):
        a, b = roots.get(r["work_a"], r["work_a"]), roots.get(r["work_b"], r["work_b"])
        if r["status"] == "open":
            if a != b and (a in wanted or b in wanted):
                item = _proposal_json(r, works, all_vols, roots)
                if item:
                    for wid in {a, b} & wanted:
                        proposals[wid].append(item)
        elif a == b and a in wanted:
            merges[a].append(_merge_json(conn, r, roots))
    link_rows: dict[int, dict] = defaultdict(dict)
    ids = sorted(wanted)
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        for r in conn.execute(f"SELECT * FROM work_links WHERE work_id IN ({','.join('?' * len(chunk))})", chunk):
            link_rows[r["work_id"]][r["source"]] = r
    work_marks = {r[0] for r in conn.execute("SELECT work_id FROM bookmarks WHERE work_id IS NOT NULL")}
    later = {r[0] for r in conn.execute("SELECT work_id FROM read_later")}
    out = []
    for wid in work_ids:
        w = works[wid]
        vols = all_vols.get(wid, [])
        for v in vols:
            v["bookmarked"] = v["id"] in marked
        slots, unnumbered = build_slots(vols)
        links = _links_of(link_rows.get(wid, {}), sources)
        out.append({
            **_summary(w, vols, len(proposals[wid]), sum(1 for x in links if x["status"] == "pending")),
            "title_confidence": w["title_confidence"], "title_evidence": w["title_evidence"],
            "author_confidence": w["author_confidence"],
            "fields": w["fields"], "links": links,
            "aliases": w["aliases"],
            "slots": slots, "unnumbered": unnumbered,
            "numbers": number_summary(vols),
            "proposals": proposals[wid], "merges": merges[wid], "merged": bool(merges[wid]),
            "bookmarked": wid in work_marks,
            "read_later": wid in later,
        })
    return out


def get_work(conn: sqlite3.Connection, work_id: int, sources: tuple[str, ...] = ()) -> dict:
    return get_works(conn, [work_id], sources)[0]


def assignments(conn: sqlite3.Connection) -> dict[int, int]:
    """巻 id → 作品 id(一覧の巻に作品へのリンクを付けるため)。"""
    return {r["volume_id"]: r["work_id"] for r in conn.execute("SELECT volume_id, work_id FROM work_volumes")}


def kind_compatible(work_kind: str | None, candidate_kind: str | None) -> bool:
    """作品の種別と公開 DB の候補の種別が食い違わないか。どちらかが未定なら食い違わない。

    公開 DB は同人誌を漫画として返す(KIND_MANGA)ので、同人誌の作品は漫画の候補とも両立する。
    """
    if not work_kind or not candidate_kind or work_kind == candidate_kind:
        return True
    return work_kind == "doujinshi" and candidate_kind == "manga"
