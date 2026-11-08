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
本物の日本語や、欧文の正規のアクセント文字付き単語(café, Amélie, naïve, São Paulo など)を
誤変換しないため、以下を満たすときだけ修復する:
- 制御文字や置換文字(U+FFFD)を含まない
- 復元後に全角ひらがな・カタカナが含まれる、または日本語文字(漢字等)が 2 文字以上含まれる
  (欧文単語に偶然 1 つの漢字や半角カナ 1 文字ができただけのケースを排除)
- 復元候補が複数ある場合は、文字数が短く(UTF-8 からの復元等)、カナが多い自然な候補を優先する
"""
from __future__ import annotations

import re

_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_JAPANESE = re.compile(r"[぀-ヿ㐀-䶿一-鿿ｦ-ﾟ]")
_ZEN_KANA = re.compile(r"[\u3041-\u3096\u30a1-\u30fa]")

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

    # パターン A: 日本語文字(漢字等)を既に含むテキスト (Shift_JIS / EUC-JP として誤読されたもの)
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
        fixed = _redecode(text, "cp932", "euc-jp")
        if fixed is not None and _has_japanese(fixed) and (_ZEN_KANA.search(fixed) or len(_JAPANESE.findall(fixed)) >= 2):
            return fixed, "文字化けを直しました(EUC-JP の名前を Shift_JIS として読んだもの)"

        return text, None

    # パターン B: 日本語文字を含まないテキスト (欧文・DOS コードページとして誤読されたもの)
    if any(ord(c) >= 0x80 for c in text):
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
                # 安全条件: 全角ひらがな・カタカナがあるか、漢字が2文字以上
                # (欧文単語に偶然 1 つの漢字ができただけのケースや半角カナ 1 文字を排除)
                if zen_kana or kanji_count >= 2:
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

    return text, None
