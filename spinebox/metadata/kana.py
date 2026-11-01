"""かな → ローマ字(ヘボン式の簡易版)。

国立国会図書館サーチの項目には作品名の読み(カタカナ)がある。ローマ字で検索されたとき(ファイル名が
`Okiraku ryoshu no tanoshi ryochi` のように書いてある蔵書)に、日本語名の項目と突き合わせるために使う。
読みを正確にローマ字化するためのものではなく、比較のための近似(長音・助詞の綴りの揺れは
`match.normalize_romaji` が畳む)。漢字・記号はそのまま残す。
"""
from __future__ import annotations

import unicodedata

_BASE = {
    "あ": "a", "い": "i", "う": "u", "え": "e", "お": "o",
    "か": "ka", "き": "ki", "く": "ku", "け": "ke", "こ": "ko",
    "さ": "sa", "し": "shi", "す": "su", "せ": "se", "そ": "so",
    "た": "ta", "ち": "chi", "つ": "tsu", "て": "te", "と": "to",
    "な": "na", "に": "ni", "ぬ": "nu", "ね": "ne", "の": "no",
    "は": "ha", "ひ": "hi", "ふ": "fu", "へ": "he", "ほ": "ho",
    "ま": "ma", "み": "mi", "む": "mu", "め": "me", "も": "mo",
    "や": "ya", "ゆ": "yu", "よ": "yo",
    "ら": "ra", "り": "ri", "る": "ru", "れ": "re", "ろ": "ro",
    "わ": "wa", "ゐ": "i", "ゑ": "e", "を": "o", "ん": "n",
    "が": "ga", "ぎ": "gi", "ぐ": "gu", "げ": "ge", "ご": "go",
    "ざ": "za", "じ": "ji", "ず": "zu", "ぜ": "ze", "ぞ": "zo",
    "だ": "da", "ぢ": "ji", "づ": "zu", "で": "de", "ど": "do",
    "ば": "ba", "び": "bi", "ぶ": "bu", "べ": "be", "ぼ": "bo",
    "ぱ": "pa", "ぴ": "pi", "ぷ": "pu", "ぺ": "pe", "ぽ": "po",
    "ぁ": "a", "ぃ": "i", "ぅ": "u", "ぇ": "e", "ぉ": "o", "ゔ": "vu",
}
# 拗音(き + ゃ など)の小書き文字 → 子音の後ろに付く母音部分
_SMALL = {"ゃ": "ya", "ゅ": "yu", "ょ": "yo"}
# 特殊な拗音の綴り
_COMBO = {"し": "sh", "ち": "ch", "じ": "j", "ぢ": "j"}


def _to_hiragana(text: str) -> str:
    return "".join(chr(ord(c) - 0x60) if "ァ" <= c <= "ヶ" else c for c in text)


def to_romaji(text: str) -> str:
    text = _to_hiragana(unicodedata.normalize("NFKC", text))
    out: list[str] = []
    i = 0
    sokuon = False
    while i < len(text):
        c = text[i]
        nxt = text[i + 1] if i + 1 < len(text) else ""
        if c == "っ":
            sokuon = True
            i += 1
            continue
        if c == "ー":
            # 長音は直前の母音を重ねる
            last = next((ch for ch in reversed("".join(out)) if ch in "aiueo"), "")
            out.append(last)
            i += 1
            continue
        if c in _BASE and nxt in _SMALL:
            base = _BASE[c]
            romaji = (_COMBO[c] + _SMALL[nxt][1:]) if c in _COMBO else base[:-1] + _SMALL[nxt]
            i += 2
        elif c in _BASE:
            romaji = _BASE[c]
            i += 1
        else:
            out.append(c)
            sokuon = False
            i += 1
            continue
        if sokuon:
            romaji = ("t" if romaji.startswith("ch") else romaji[0]) + romaji
            sokuon = False
        out.append(romaji)
    return "".join(out)
