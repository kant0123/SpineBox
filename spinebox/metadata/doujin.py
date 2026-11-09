"""同人誌の題名の書式 `(イベント) [サークル (作者)] 題名 (原作) [版・言語]` の読み取りと、nHentai / E-Hentai 共通の部品。

同人誌のファイル名と、nHentai / E-Hentai の題名は同じ書式を使うので、同じパーサで両側を読んで題名の核・
サークル・作者で突き合わせる。書式は慣習であって規則ではない(括弧の無い題名、題名の一部の括弧など)。
読めなかった部分は題名に残し、決めつけない(CLAUDE.md「ファイル名は候補」)。
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from .match import is_romaji_word, normalize, normalize_romaji
from .model import KIND_MANGA, SCOPE_WORK, MetadataItem

_LEADING_EVENT = re.compile(r"^\s*[(（]([^()（）]*)[)）]\s*(?=[\[【])")
_LEADING_CREDIT = re.compile(r"^\s*(?:\[([^\]]*)\]|【([^】]*)】)\s*")
_CREDIT_PARTS = re.compile(r"^(.*?)\s*[(（]([^()（）]*)[)）]$")
_TRAILING_BRACKET = re.compile(r"\s*(?:\[([^\]]*)\]|【([^】]*)】|\{([^}]*)\}|｛([^｝]*)｝)\s*$")
_TRAILING_TRANSLATOR = re.compile(r"\s*=[^=]{1,40}=\s*$")
_TRAILING_PAREN = re.compile(r"^(.*\S)\s*[(（]([^()（）]*)[)）]$")
_PARODY_SEP = re.compile(r"\s*[、,，]\s*")
_SPACES = re.compile(r"\s+")


@dataclass(frozen=True)
class DoujinTitle:
    """題名の書式を分けたもの。読めなかった部分は None / 空。`title` は常に残る(読めなければ全体)。"""
    title: str
    event: str | None = None            # `C71` `例大祭12`
    circle: str | None = None           # サークル名(`[サークル (作者)]` の前半)
    author: str | None = None           # 作者(同後半。括弧が無いときは括弧の中身全体)
    parody: tuple[str, ...] = ()        # 原作・パロディ元(`(東方Project)`)
    marks: tuple[str, ...] = ()         # 末尾の括弧の印(`DL版` `中国翻訳` `2024年8月31日`)

    @property
    def credit(self) -> str | None:
        """著者欄に出す表記。ファイル名の書式のまま `サークル (作者)`(片方だけならそれ)。"""
        if self.circle and self.author:
            return f"{self.circle} ({self.author})"
        return self.author or self.circle

    @property
    def structured(self) -> bool:
        return bool(self.event or self.circle or self.author)


def parse_doujin_title(text: str) -> DoujinTitle:
    """`(C71) [ありさんアンテナ (小蟻)] Eat The Rich! (スカッとゴルフ パンヤ) [DL版]` を分ける。"""
    rest = _SPACES.sub(" ", unicodedata.normalize("NFKC", text or "").strip())
    event = circle = author = None

    m = _LEADING_EVENT.match(rest)
    if m:
        event = m.group(1).strip() or None
        rest = rest[m.end():]
    m = _LEADING_CREDIT.match(rest)
    if m:
        content = (m.group(1) if m.group(1) is not None else m.group(2)).strip()
        parts = _CREDIT_PARTS.match(content)
        if parts and parts.group(1).strip() and parts.group(2).strip():
            circle, author = parts.group(1).strip(), parts.group(2).strip()
        else:
            author = content or None
        rest = rest[m.end():]

    marks: list[str] = []
    while True:
        t = _TRAILING_TRANSLATOR.search(rest)
        if t and rest[:t.start()].strip():
            marks.insert(0, t.group().strip())
            rest = rest[:t.start()]
            continue
        b = _TRAILING_BRACKET.search(rest)
        if b and rest[:b.start()].strip():
            marks.insert(0, next(g for g in b.groups() if g is not None).strip())
            rest = rest[:b.start()]
            continue
        break

    parody: tuple[str, ...] = ()
    p = _TRAILING_PAREN.match(rest)
    if p and (event or circle or author):  # 書式を読めた題名の末尾の括弧だけを原作とみなす(題名の一部の括弧を割らない)
        parody = tuple(x for x in _PARODY_SEP.split(p.group(2).strip()) if x)
        rest = p.group(1)
    title = rest.strip()
    return DoujinTitle(title or _SPACES.sub(" ", (text or "").strip()), event, circle, author, parody, tuple(marks))


# --- 取得元ごとの書き方の違いを吸収する部品 -------------------------------------------------------

CATEGORY_JA = {
    "doujinshi": "同人誌", "manga": "漫画", "artist cg": "CG集", "game cg": "ゲームCG", "western": "洋書",
    "image set": "画像集", "cosplay": "コスプレ", "non-h": "一般", "asian porn": "実写", "misc": "その他",
}

_KIND_BY_CATEGORY = {"doujinshi": KIND_MANGA, "manga": KIND_MANGA}

# 検索語に入れると構文として読まれるもの(名前空間 `artist:`、引用符、否定 `-`、完全一致 `$` など)
_SEARCH_SYNTAX = re.compile(r'[:"$*%~|<>=]')

# 翻訳版・訳者の印。原本(日本語)を優先するために使う
_TRANSLATION_MARK = re.compile(
    r"翻訳|翻譯|訳|譯|英語|中国|中文|한국|korean|english|chinese|spanish|french|german|russian|italian|thai|"
    r"vietnamese|indonesian|portuguese|translated|\btl\b|\bmtl\b|^=.*=$", re.IGNORECASE)


def search_keywords(title: str) -> str:
    """作品名を、nHentai / E-Hentai の検索語にする。検索の構文として読まれる記号は空白にする。"""
    text = _SEARCH_SYNTAX.sub(" ", unicodedata.normalize("NFKC", title or ""))
    words = [w.lstrip("-") for w in text.split()]
    return " ".join(w for w in words if w)


def search_queries(title: str, author: str | None) -> list[str]:
    """検索語の候補を、絞れるもの(作品名 + サークル名)から順に返す。サークル名が無ければ作品名だけ。

    作品名が短い・ありふれていると関係のない本が大量に当たるので、題名に含まれるサークル名を足して絞り、
    0 件ならサークル名なしで引き直す(呼び出し側が順に試す)。著者欄は `サークル (作者)` の書式。
    """
    keywords = search_keywords(title)
    if not keywords:
        return []
    credit = (author or "").strip()
    parts = _CREDIT_PARTS.match(unicodedata.normalize("NFKC", credit)) if credit else None
    extra = search_keywords(parts.group(1) if parts and parts.group(1).strip() else credit)
    return [f"{keywords} {extra}", keywords] if extra else [keywords]


def category_kind(category: str | None) -> str | None:
    return _KIND_BY_CATEGORY.get((category or "").strip().lower())


def category_ja(category: str | None) -> str | None:
    key = (category or "").strip().lower()
    return CATEGORY_JA.get(key)


def is_translation(marks: tuple[str, ...], languages: tuple[str, ...] = ()) -> bool:
    """翻訳版か。題名末尾の印か、取得元の言語タグ(`translated` や日本語以外)で見る。"""
    if any(_TRANSLATION_MARK.search(m) for m in marks):
        return True
    langs = {x.strip().lower() for x in languages}
    return "translated" in langs or bool(langs - {"japanese", "rewrite", "text cleaned", "speechless"})


def build_item(*, source: str, source_id: str, url: str, japanese: str | None, english: str | None,
               category: str | None, tags: tuple[str, ...] = ()) -> tuple[MetadataItem, DoujinTitle]:
    """日本語の題名(あれば)と英語の題名から、`MetadataItem` と、元にした題名の読み取り結果を作る。

    題名の核は日本語を優先し、英語の核はローマ字名に、`題名 (原作)` の形は別名に残す(検索側が原作つきで
    持っているときにも一致させる)。著者欄は日本語の題名の書式(`サークル (作者)`)を優先し、
    日本語の題名が無いときだけ英語の題名のもの(ローマ字)を使う。サークルは出版社に入れる。
    """
    ja = parse_doujin_title(japanese) if japanese and japanese.strip() else None
    en = parse_doujin_title(english) if english and english.strip() else None
    base = ja or en
    if base is None:
        raise ValueError("題名が無い")
    title = base.title
    romaji = en.title if ja is not None and en is not None and en.title != title and is_romaji_word(en.title) else None
    aliases: list[str] = []
    for parsed in (ja, en):
        if parsed is None:
            continue
        for p in parsed.parody[:1]:
            aliases.append(f"{parsed.title} ({p})")
    aliases = [a for a in dict.fromkeys(aliases) if a != title and a != romaji]
    if en is not None and en.title != title and en.title != romaji:
        aliases.append(en.title)
    kind = category_kind(category)
    genre = category_ja(category)
    parody_tags = tuple(p for p in base.parody if p.casefold() not in ("オリジナル", "original"))
    item = MetadataItem(
        source=source, source_id=source_id, scope=SCOPE_WORK, title=title, source_url=url,
        romaji=romaji, aliases=tuple(dict.fromkeys(aliases)),
        authors=(base.credit,) if base.credit else (),
        publisher=base.circle,
        label=base.event,
        kind=kind,
        genres=(genre,) if genre else (),
        tags=tuple(dict.fromkeys((*parody_tags, *tags))),
    )
    return item, base


def dedupe_key(item: MetadataItem) -> str:
    """同じ本の翻訳版・再アップロードをまとめる鍵(題名の核 + 著者欄)。表記体系が違えば別になる。"""
    norm = normalize_romaji if is_romaji_word(item.title) else normalize
    author = item.authors[0] if item.authors else ""
    a_norm = normalize_romaji if is_romaji_word(author) else normalize
    return f"{norm(item.title)}|{a_norm(author)}"
