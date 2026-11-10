"""複数パターンの文字コード化け(mojibake)の検出と修復。

対応する文字化けパターン:
1. 欧文・DOS コードページとして誤読されたもの:
   - 誤読元: cp437 (DOS / ZIP の標準), cp1252 (Windows 欧米), latin-1 (ISO-8859-1), cp850 (DOS 多言語)
   - 本来: cp932 (Shift_JIS), utf-8, euc-jp
   - 実例: CP932 で作成された ZIP / アーカイブ名が CP437 として展開・解釈されたもの
     (例: 'ÅTèoÅ¡öNâTâôâfü[ 2023öN09ìå' -> '週覚少年サンデー 2023年09号')
2. Shift_JIS (cp932) として誤読されたもの:
   - 誤読元: cp932
   - 本来: utf-8 (例: '縺九￥...' -> 'かくかくしかじか'), euc-jp
3. EUC-JP として誤読されたもの:
   - 誤読元: euc-jp
   - 本来: utf-8, cp932

安全設計:
本物の日本語や、欧文の正規のアクセント文字付き単語(café, Amélie, naïve, São Paulo, élégant など)、
半角カナの連続(ｱｲｳｴ など)を誤変換しないため、以下を満たすときだけ修復する:
- 制御文字や置換文字(U+FFFD)を含まない
- 欧文文字・記号のみで構成されるテキストの場合、全角かな(ひらがな・カタカナ)が復元された場合のみ修復する
  (欧文単語 'élégant' 等のアクセント文字が偶然 Shift_JIS 第2水準漢字バイト列と一致する誤爆を排除)
- 欧文単語でない場合、全角かなが含まれるか、または日本語文字(漢字等)が 2 文字以上含まれる
- EUC-JP -> CP932 誤読は、元テキストに半角カナの連続を含み、かつ復元後に全角かなが含まれることを必須とする
  (CP932 の半角カナ 2 文字が EUC-JP の漢字 1 文字として偶然デコードされる誤爆を排除)
- 復元候補が複数ある場合は、文字数が短く(UTF-8 からの復元等)、カナが多い自然な候補を優先する
"""
from __future__ import annotations

import re

_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_JAPANESE = re.compile(r"[぀-ヿ㐀-䶿一-鿿ｦ-ﾟ]")
_ZEN_KANA = re.compile(r"[\u3041-\u3096\u30a1-\u30fa]")
_HAN_KANA_SEQ = re.compile(r"[｡-ﾟ]{2,}")
_WESTERN_TEXT = re.compile(
    r"^[A-Za-z0-9\s_.,'\"!?()\[\]\-~#&+:;\\/=@%*<>{}^`|\u00a0-\u024f\u2000-\u206f\u20a0-\u20cf\u2100-\u214f]+$"
)

# 欧文コードページの候補
_WESTERN_ENCODINGS = ("cp437", "cp1252", "latin-1", "cp850")
# 復元先日本語エンコーディングの候補
_JAPANESE_ENCODINGS = (("utf-8", "UTF-8"), ("cp932", "Shift_JIS"), ("euc-jp", "EUC-JP"))


def _has_japanese(text: str) -> bool:
    return _JAPANESE.search(text) is not None


def _redecode(text: str, wrong: str, right: str) -> str | None:
    try:
        fixed = text.encode(wrong).decode(right)
    except (UnicodeEncodeError, UnicodeDecodeError):
        return None
    if fixed == text or _CONTROL.search(fixed) or "\ufffd" in fixed:
        return None
    return fixed


def fix_mojibake(text: str) -> tuple[str, str | None]:
    """文字コードの誤読による文字化けを直す。(直した文字列, 何を直したかの説明)。

    直せない・化けていないなら (text, None) を返す。
    """
    if text.isascii():
        return text, None

    # パターン A: 日本語文字(漢字・半角カナ等)を既に含むテキスト (Shift_JIS / EUC-JP として誤読されたもの)
    if _has_japanese(text):
        # 1. UTF-8 の名前を Shift_JIS として読んだもの (UTF-8 3バイト -> CP932 1.5文字で長くなっている)
        fixed = _redecode(text, "cp932", "utf-8")
        if fixed is not None and _has_japanese(fixed) and len(fixed) < len(text) and sum(not c.isascii() for c in fixed) >= 2:
            return fixed, "文字化けを直しました(UTF-8 の名前を Shift_JIS として読んだもの)"

        # 2. UTF-8 の名前を EUC-JP として読んだもの
        fixed = _redecode(text, "euc-jp", "utf-8")
        if fixed is not None and _has_japanese(fixed) and len(fixed) < len(text) and sum(not c.isascii() for c in fixed) >= 2:
            return fixed, "文字化けを直しました(UTF-8 の名前を EUC-JP として読んだもの)"

        # 3. EUC-JP の名前を Shift_JIS として読んだもの
        # EUC-JP のかな(0xA4xx, 0xA5xx)が CP932 で半角カナに化けたものだけを安全に対象とする
        # (半角カナや第2水準漢字の連続が偶然 EUC-JP の漢字としてデコードされる誤爆を防ぐため、
        #  元テキストに半角カナの連続を含み、かつ復元結果に全角かなが含まれることを必須とする)
        if re.search(r"[｡-ﾟ]{4,}", text):
            fixed = _redecode(text, "cp932", "euc-jp")
            if (fixed is not None and _has_japanese(fixed) and
                    len(_ZEN_KANA.findall(fixed)) >= 2 and sum(not c.isascii() for c in fixed) >= 2):
                return fixed, "文字化けを直しました(EUC-JP の名前を Shift_JIS として読んだもの)"

        # 4. Shift_JIS の名前を GBK(中国語の Windows)として読んだもの(`廡姧彮擭僒儞僨乕` -> `週刊少年サンデー`)。
        # 本物の日本語の漢字は GBK に戻すと半角カナ混じりになるので、かなを含まない名前が、半角カナ無しの
        # 全角かな 2 文字以上に戻るときだけ直す
        if not _ZEN_KANA.search(text):
            fixed = _redecode(text, "gbk", "cp932")
            if (fixed is not None and len(_ZEN_KANA.findall(fixed)) >= 2
                    and not re.search(r"[｡-ﾟ]", fixed)):
                return fixed, "文字化けを直しました(Shift_JIS の名前を GBK として読んだもの)"

        return text, None

    # パターン B: 日本語文字を含まないテキスト (欧文・DOS コードページとして誤読されたもの)
    if any(ord(c) >= 0x80 for c in text):
        is_western = _WESTERN_TEXT.fullmatch(text) is not None
        candidates: list[tuple[str, str, int, int]] = []
        for wrong in _WESTERN_ENCODINGS:
            for right, label in _JAPANESE_ENCODINGS:
                fixed = _redecode(text, wrong, right)
                if fixed is None:
                    continue
                zen_kana = _ZEN_KANA.findall(fixed)
                jp_chars = _JAPANESE.findall(fixed)
                # 半角カナ単体を除いた日本語文字数
                kanji_count = sum(1 for c in jp_chars if not (0xFF61 <= ord(c) <= 0xFF9F))
                # 安全条件: 全角ひらがな・カタカナがあるか、欧文単語でない場合に漢字が2文字以上
                # (欧文単語 'élégant' 等のアクセント文字が偶然 CP932 の第2水準漢字になる誤爆を排除)
                is_latin_to_sjis = wrong in ("latin-1", "cp1252") and right == "cp932"
                # 安全条件:
                # - 欧文テキスト(is_western)の場合: 全角かなが2文字以上復元された場合のみ修復
                #   (欧文単語 'château' 等の â(CP437:0x83)+ASCII小文字が偶然 CP932 のカタカナ1文字 'フ' になる誤爆を排除)
                # - 欧文テキストでない場合: 全角かなが含まれるか、漢字が2文字以上(かつ Latin-1/CP1252->CP932 誤爆でない)
                has_kana_seq = bool(re.search(r"[ぁ-ゖァ-ヺ]{2,}", fixed))
                has_kana = has_kana_seq if is_western else bool(zen_kana)
                if has_kana or (kanji_count >= 2 and not is_western and not is_latin_to_sjis):
                    candidates.append((
                        fixed,
                        f"文字化けを直しました({label} の名前を欧文の文字コードとして読んだもの)",
                        len(fixed),
                        -len(zen_kana),
                    ))
        if candidates:
            # 文字列長が短く(UTF-8などの縮み)、カナが多い自然な候補を優先
            candidates.sort(key=lambda x: (x[2], x[3]))
            return candidates[0][0], candidates[0][1]
        fixed = _redecode_words(text)
        if fixed is not None:
            return fixed, "文字化けを直しました(一部の語は元に戻せないまま)"

    return text, None


# 戻した語で英字とかな・漢字が隣り合うのは偶然の一致(`château` の `â` + `t` が `フ` になる。#225 の誤爆)
_LETTER_NEXT_TO_JAPANESE = re.compile(r"[A-Za-z][぀-ヿ一-鿿]|[぀-ヿ一-鿿][A-Za-z]")


def _redecode_words(text: str) -> str | None:
    """空白で区切った語ごとに、欧文の文字コードで読んだ Shift_JIS を戻す。

    展開ツールが一部のバイトを `+` `-` `¦` に置き換えた名前(`[é¦é+éG…] ânâôâcü[ü~âgâëâbâVâà æµ10è¬`)は
    全体では戻せない。全角かなの続く語が 1 つでも戻れば文字コードの誤読と確かめられたとみなし、
    同じ読み違いで日本語に戻る語だけを戻す(戻せない語はそのまま残す。`looks_garbled` が別に判定する)。
    """
    words = re.split(r"(\s+)", text)
    for wrong in ("cp437", "cp850"):
        out, proved, changed = [], False, False
        for w in words:
            fixed = _redecode(w, wrong, "cp932") if w.strip() and not w.isascii() else None
            if fixed is not None and _has_japanese(fixed) and not _LETTER_NEXT_TO_JAPANESE.search(fixed):
                proved |= bool(re.search(r"[ぁ-ゖァ-ヺ]{2,}", fixed))
                out.append(fixed)
                changed = True
            else:
                out.append(w)
        if proved and changed:
            return "".join(out)
    return None


_GARBLED_SJIS = re.compile(r"[縺繧繝荳逕譁蜷遘豁謾霑鬨隴蟾]")


def looks_garbled(text: str) -> bool:
    """直せなかった文字化けに見える名前か(作品名に使わない)。

    - 置換文字(U+FFFD)・私用領域の文字を含む(読めなかったバイトの跡)
    - 日本語を含まず、Latin-1 のアクセント文字・罫線が、ASCII でない文字の半分以上かつ 4 文字以上
      (`ûéôsÉ+ò¦…`。`Amélie` `café` のような欧文はアクセント文字が少ない)
    - UTF-8 の名前を Shift_JIS で読んだ跡の字(`縺` `繧` `繝` …)が 2 文字以上(本番の 1724・1419 は 2 文字)
    """
    if "\ufffd" in text or any("\ue000" <= ch <= "\uf8ff" for ch in text):
        return True
    if not _has_japanese(text):
        wide = [ch for ch in text if not ch.isascii()]
        accents = [ch for ch in wide if "\u00c0" <= ch <= "\u00ff" or ch in "\u00a6\u00ac\u00b5\u00b7"
                   or "\u2500" <= ch <= "\u259f"]
        return len(accents) >= 4 and len(accents) * 2 >= len(wide) and len(accents) * 4 >= len(text.replace(" ", ""))
    return len(_GARBLED_SJIS.findall(text)) >= 2
