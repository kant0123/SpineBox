"""ファイル名の整理(#84)。特殊記号・全角/半角の揺れ・文字化けを直した名前と、何を直したかの説明を返す。

整理画面でテンプレートを空にしたとき(と、ファイル名テンプレートが空でフォルダーだけ振り分けるとき)の名前に使う。
直すのはファイル名の本体だけで、拡張子とフォルダ名は変えない(フォルダには他の巻も入っているため)。

- 文字化け: 検出できる範囲だけ。UTF-8 の名前を Shift_JIS(cp932)として読んだもの(「縺薙ｓ縺ｫ縺｡縺ｯ」)と、
  Shift_JIS / UTF-8 の名前を Windows-1252(Latin-1)として読んだもの(「‚±‚ñ‚É‚¿‚Í」「ã“ã‚“」)。
  読み直した結果が厳密に復号でき、かなか漢字を含み、元より短い(または記号だけの名前が日本語になる)ときだけ直す。
  本物の日本語の名前を別の文字に化けさせないため、少しでも怪しければ直さない
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

from .fs import MAX_STEM, safe_stem
from .template import ValueMissing

_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_WIN_BAD = {c: chr(ord(c) + 0xFEE0) for c in '<>:"/\\|?*'}
_FULLWIDTH_ALNUM = re.compile(r"[０-９Ａ-Ｚａ-ｚ]")
_HALFWIDTH_KANA = re.compile(r"[｡-ﾟ]+")
_SPACES = re.compile(r"[ 　]+")
_JAPANESE = re.compile(r"[぀-ヿ㐀-䶿一-鿿ｦ-ﾟ]")
_LATIN1_HIGH = re.compile(r"[\u0080-ÿŒœŠšŸŽžƒˆ˜–—"
                          r"‘-„†-•…‰‹›€™]")
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


def _has_japanese(text: str) -> bool:
    return _JAPANESE.search(text) is not None


def _redecode(text: str, wrong: str, right: str) -> str | None:
    try:
        fixed = text.encode(wrong).decode(right)
    except (UnicodeEncodeError, UnicodeDecodeError):
        return None
    if fixed == text or _CONTROL.search(fixed) or "�" in fixed:
        return None
    return fixed


def fix_mojibake(text: str) -> tuple[str, str | None]:
    """(直した名前, 何を直したか)。直せない・化けていないなら (text, None)。"""
    if text.isascii():
        return text, None
    # UTF-8 の名前を Shift_JIS として読んだもの。UTF-8 の 3 バイトの文字が Shift_JIS の 1.5 文字になるので、直すと短くなる
    fixed = _redecode(text, "cp932", "utf-8")
    if fixed is not None and _has_japanese(fixed) and len(fixed) < len(text) \
            and sum(not c.isascii() for c in fixed) >= 2:
        return fixed, "文字化けを直しました(UTF-8 の名前を Shift_JIS として読んだもの)"
    # Latin-1 系として読んだもの。元は日本語の文字を含まず、Latin-1 の上半分の文字だけで化けている
    if not _has_japanese(text) and _LATIN1_HIGH.search(text):
        for wrong in ("cp1252", "latin-1"):
            for right, label in (("utf-8", "UTF-8"), ("cp932", "Shift_JIS")):
                fixed = _redecode(text, wrong, right)
                if fixed is not None and _has_japanese(fixed):
                    return fixed, f"文字化けを直しました({label} の名前を欧文の文字コードとして読んだもの)"
    return text, None


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
