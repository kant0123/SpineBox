"""候補の確からしさ。検索した名前・著者・巻番号と、取得した項目の一致度を数値と根拠で返す。

ファイル名・フォルダ名は候補でしかない(CLAUDE.md)ので、ここで出す数値も「採用してよい」という判断ではなく、
後続の結線が閾値を決めるための材料。比べようのない観点は点数に入れず、reasons にだけ残す。
"""
from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

from .kana import to_romaji
from .model import Match, MetadataItem, SearchQuery

# 観点ごとの重み。評価できた観点だけで重み付き平均を取る
WEIGHTS = {"title": 0.55, "author": 0.20, "volume": 0.15, "kind": 0.10}

# 部分一致を一致とみなす最小の文字数(「あ」のような 1 文字が何にでも含まれるのを避ける)
_MIN_CONTAIN = 3


def _kata_to_hira(text: str) -> str:
    return "".join(chr(ord(c) - 0x60) if "ァ" <= c <= "ヶ" else c for c in text)


def normalize(text: str) -> str:
    """比較用に正規化する。NFKC・小文字化・カタカナ→ひらがな、文字と数字以外(空白・記号)を除く。"""
    text = unicodedata.normalize("NFKC", text).casefold()
    text = _kata_to_hira(text)
    return "".join(c for c in text if c.isalnum())


_MACRON = str.maketrans({"ā": "a", "ī": "i", "ū": "u", "ē": "e", "ō": "o"})


def is_romaji_word(text: str) -> bool:
    """ASCII またはマクロン付きラテン文字(と記号・空白)だけで構成されているか。日本語の名前と区別する。"""
    cleaned = unicodedata.normalize("NFKC", text).casefold().translate(_MACRON)
    letters = [c for c in cleaned if c.isalnum()]
    return bool(letters) and all(ord(c) < 128 for c in letters)


def normalize_romaji(text: str) -> str:
    """ローマ字名の綴りの揺れ(長音の `ou` `oo` `uu` `ii`、マクロン、ヘボン式の n')を畳んで比較用にする。

    `Ryoushu` と `ryoshu`、`Tanoshii` と `tanoshi` を同じにする。日本語名には使わない。
    """
    text = unicodedata.normalize("NFKC", text).casefold().translate(_MACRON)
    text = re.sub(r"n'", "n", text)
    text = "".join(c for c in text if c.isalnum())
    text = re.sub(r"ou", "o", text)
    text = re.sub(r"oo", "o", text)
    text = re.sub(r"uu", "u", text)
    text = re.sub(r"ii", "i", text)
    return text


def _name_key(text: str) -> str:
    return normalize_romaji(text) if is_romaji_word(text) else normalize(text)


def title_score(query_title: str, names: tuple[str, ...]) -> tuple[float, str]:
    """検索した作品名と、項目の作品名の候補すべてを突き合わせた最良の一致度と根拠。"""
    qk = _name_key(query_title)
    if not qk:
        return 0.0, "title:empty"
    best, reason = 0.0, "title:none"
    for name in names:
        nk = _name_key(name)
        if not nk:
            continue
        if nk == qk:
            return 1.0, "title:exact"
        short, long_ = (qk, nk) if len(qk) <= len(nk) else (nk, qk)
        if len(short) >= _MIN_CONTAIN and short in long_:
            # 副題つき(`作品名 生産系魔術で…`)や巻数つきを、同じ作品の別表記として強めに見る
            score = 0.6 + 0.3 * len(short) / len(long_)
            if score > best:
                best, reason = score, "title:contains"
            continue
        ratio = SequenceMatcher(None, qk, nk).ratio()
        score = ratio * 0.8  # 部分一致より上にはならない
        if score > best:
            best, reason = score, f"title:similar({ratio:.2f})"
    return round(best, 4), reason


def _author_key(name: str) -> str:
    return normalize_romaji(name) if is_romaji_word(name) else normalize(name)


def author_score(query_author: str, authors: tuple[str, ...]) -> tuple[float | None, str]:
    """著者の一致度。表記体系が違って比べられないとき(日本語の著者名と MangaUpdates のローマ字名)は None。"""
    qk = _author_key(query_author)
    if not qk:
        return None, "author:empty_query"
    if not authors:
        return None, "author:unknown"
    comparable = False
    for author in authors:
        ak = _author_key(author)
        if not ak:
            continue
        # 検索側が日本語で、取得側がローマ字(または逆)なら比べられない
        if is_romaji_word(query_author) != is_romaji_word(author):
            continue
        comparable = True
        if ak == qk or (min(len(ak), len(qk)) >= 2 and (ak in qk or qk in ak)):
            return 1.0, "author:match"
    if not comparable:
        return None, "author:not_comparable"
    return 0.0, "author:mismatch"


def score_item(query: SearchQuery, item: MetadataItem) -> Match:
    components: dict[str, float] = {}
    reasons: list[str] = []

    names = item.names()
    if item.reading:
        # 読み(カタカナ)をローマ字にして、ローマ字で検索された日本語名の項目とも比べられるようにする
        names = (*names, to_romaji(item.reading))
    t, why = title_score(query.title, names)
    components["title"] = t
    reasons.append(why)

    if query.author:
        a, why = author_score(query.author, item.authors)
        reasons.append(why)
        if a is not None:
            components["author"] = a

    if query.volume is not None:
        if item.scope != "volume":
            reasons.append("volume:not_applicable")
        elif item.volume is None:
            # 巻の書誌なのに巻が分からない(映画ノベライズ・全集など)。一致した項目より下、食い違った項目より上
            components["volume"] = 0.5
            reasons.append("volume:unknown")
        else:
            ok = item.volume == query.volume
            components["volume"] = 1.0 if ok else 0.0
            reasons.append("volume:match" if ok else "volume:mismatch")

    if query.kind:
        if item.kind is None:
            reasons.append("kind:unknown")
        else:
            ok = item.kind == query.kind
            components["kind"] = 1.0 if ok else 0.0
            reasons.append("kind:match" if ok else "kind:mismatch")

    if query.isbn and item.isbn:
        # ISBN が一致するなら、書誌としてはこれ以上の根拠が無い
        if _isbn_key(query.isbn) == _isbn_key(item.isbn):
            reasons.append("isbn:match")
            components["title"] = max(components["title"], 1.0)

    weight_sum = sum(WEIGHTS[k] for k in components)
    score = sum(WEIGHTS[k] * v for k, v in components.items()) / weight_sum
    return Match(score=round(score, 4), components=components, reasons=tuple(reasons))


def _isbn_key(isbn: str) -> str:
    return re.sub(r"[^0-9Xx]", "", isbn).upper()
