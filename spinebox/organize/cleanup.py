"""ファイル名の整理(#84)。特殊記号・全角/半角の揺れ・文字化けを直した名前と、何を直したかの説明を返す。

整理画面でテンプレートを空にしたとき(と、ファイル名テンプレートが空でフォルダーだけ振り分けるとき)の名前に使う。
直すのはファイル名の本体だけで、拡張子とフォルダ名は変えない(フォルダには他の巻も入っているため)。

- 文字化け: 複数パターンの文字コード誤読(spinebox/mojibake.py)を検出して修復する。
- 濁点・半濁点の分離(macOS 由来の NFD)を結合する
- 制御文字は取り除き、タブは空白にする
- Windows で使えない文字(`< > : " / \\ | ? *`)は全角にする(「:」→「:」。意味を残すため `_` にはしない)
- 全角の英数字は半角に、半角カナは全角にする。全角の記号(「!」「(」など)はそのまま
- 全角の空白は半角にし、続いた空白は 1 つにする
- 前後の空白とドットを落とす(先頭のドットは隠しファイルになる・末尾は Windows で落ちる)
- 予約名(CON・NUL など)は先頭に `_`
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from ..mojibake import fix_mojibake
from .fs import MAX_STEM, safe_stem
from .template import ValueMissing

_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_WIN_BAD = {c: chr(ord(c) + 0xFEE0) for c in '<>:"/\\|?*'}
_FULLWIDTH_ALNUM = re.compile(r"[０-９Ａ-Ｚａ-ｚ]")
_HALFWIDTH_KANA = re.compile(r"[｡-ﾟ]+")
_SPACES = re.compile(r"[ 　]+")
# 拡張子とみなす末尾(英数字 1〜5 文字)。「第1.5巻」のような名前の途中のドットは拡張子にしない
_EXT = re.compile(r"\.[A-Za-z0-9]{1,5}$")


@dataclass(frozen=True)
class Cleaned:
    name: str
    changes: tuple[str, ...]


def split_ext(name: str) -> tuple[str, str]:
    m = _EXT.search(name)
    if not m or m.start() == 0:
        return name, ""
    return name[:m.start()], m.group(0)


def clean_stem(stem: str) -> Cleaned:
    """拡張子を除いた名前を整理する。整理すると空になるなら ValueMissing。"""
    changes: list[str] = []
    text, note = fix_mojibake(stem)
    if note:
        changes.append(note)

    def step(new: str, message: str) -> None:
        nonlocal text
        if new != text:
            text = new
            changes.append(message)

    step(unicodedata.normalize("NFC", text), "濁点・半濁点の分離を結合しました")
    step(_CONTROL.sub("", text.replace("\t", " ")), "制御文字を取り除きました")
    step("".join(_WIN_BAD.get(c, c) for c in text), "Windows で使えない文字を全角にしました")
    step(_FULLWIDTH_ALNUM.sub(lambda m: chr(ord(m.group(0)) - 0xFEE0), text), "全角の英数字を半角にしました")
    step(_HALFWIDTH_KANA.sub(lambda m: unicodedata.normalize("NFKC", m.group(0)), text), "半角カナを全角にしました")
    step(_SPACES.sub(" ", text), "空白を整えました(全角の空白・続いた空白)")
    step(text.strip(" ."), "前後の空白・ドットを取り除きました")
    if not text:
        raise ValueMissing("整理すると名前が空になります")
    if len(text) > MAX_STEM:
        step(text[:MAX_STEM].rstrip(" ."), f"長すぎる名前を {MAX_STEM} 文字に切り詰めました")
    step(safe_stem(text), "Windows の予約名なので先頭に _ を付けました")
    return Cleaned(text, tuple(changes))


def clean_name(name: str) -> Cleaned:
    """ファイル名(拡張子つき)を整理する。拡張子はそのまま。"""
    stem, ext = split_ext(name)
    cleaned = clean_stem(stem)
    return Cleaned(cleaned.name + ext, cleaned.changes)
