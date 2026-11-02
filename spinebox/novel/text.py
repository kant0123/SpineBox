"""テキストの小説: 文字コードの判定、区切りへの分割、表示用 HTML への変換。

- 文字コード: UTF-8 BOM → UTF-16 BOM(LE / BE)→ UTF-8 → cp932 の順に、厳密に復号できたものを使う。
  どれでも復号できない・BOM の無い UTF-16 らしい(NUL を含む)ものは TextDecodeError(理由つき)
- 分割: 巨大なテキストでも 1 回に送る量が増えないよう、行の切れ目でおよそ SECTION_CHARS 文字ごとに区切る。
  区切りは中身だけから決まるので、文字サイズや画面幅が変わっても同じ区切りになる(読書位置の基準)
- 青空文庫形式: `｜漢字《かんじ》`(`|` も可)と、区切り記号の無い `漢字《かんじ》`(直前の漢字の並びが親文字)をルビにする。
  `［＃…］` の注記は表示しない。HTML はここで組み立てる(テキストは必ずエスケープする)ので、本文に HTML は入らない
"""
from __future__ import annotations

import html
import re

SECTION_CHARS = 20_000

_UTF16_BOMS = (b"\xff\xfe", b"\xfe\xff")


class TextDecodeError(ValueError):
    """文字コードを判定できない(理由はメッセージ)。"""


def decode_text(data: bytes) -> tuple[str, str]:
    """(本文, 使った文字コード) を返す。判定できなければ TextDecodeError。"""
    if data.startswith(b"\xef\xbb\xbf"):
        return _strict(data[3:], "utf-8", "UTF-8(BOM 付き)"), "utf-8-sig"
    if data.startswith(_UTF16_BOMS):
        return _strict(data, "utf-16", "UTF-16(BOM 付き)"), "utf-16"
    if b"\x00" in data:
        raise TextDecodeError("NUL を含む(BOM の無い UTF-16 かバイナリ。BOM の無い UTF-16 は判定しない)")
    for enc in ("utf-8", "cp932"):
        try:
            return data.decode(enc), enc
        except UnicodeDecodeError:
            continue
    raise TextDecodeError("文字コードを判定できない(UTF-8 / cp932 / BOM 付き UTF-16 のどれでも復号できない)")


def _strict(data: bytes, enc: str, label: str) -> str:
    try:
        return data.decode(enc)
    except UnicodeDecodeError as e:
        raise TextDecodeError(f"{label} として復号できない({e.reason}、{e.start} バイト目)") from e


def split_sections(text: str, limit: int = SECTION_CHARS) -> list[str]:
    """行の切れ目でおよそ limit 文字ごとに区切る。1 行が limit を超えるときだけ行の途中で切る。"""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    sections: list[str] = []
    buf: list[str] = []
    size = 0
    for line in text.split("\n"):
        while len(line) > limit:
            if buf:
                sections.append("\n".join(buf))
                buf, size = [], 0
            cut = _safe_cut(line, limit)
            sections.append(line[:cut])
            line = line[cut:]
        if buf and size + len(line) > limit:
            sections.append("\n".join(buf))
            buf, size = [], 0
        buf.append(line)
        size += len(line) + 1
    if buf and (any(buf) or not sections):
        sections.append("\n".join(buf))
    return sections or [""]


def _safe_cut(line: str, limit: int) -> int:
    """ルビ(`《…》`)の途中で切らない位置。"""
    cut = limit
    opened = line.rfind("《", 0, cut)
    if opened > 0 and line.find("》", opened) >= cut:
        bar = max(line.rfind("｜", 0, opened), line.rfind("|", 0, opened))
        cut = bar if bar > 0 else opened
    return max(cut, 1)


_NOTE = re.compile(r"［＃[^］\n]*］")
# 親文字の区切り記号(｜ / |)つき、または直前の漢字の並び
_RUBY = re.compile(r"[｜|]([^｜|《》\n]+)《([^《》\n]+)》|([々〆一-鿿㐀-䶿豈-﫿々〆ヶ]+)《([^《》\n]+)》")


def line_to_html(line: str) -> str:
    line = _NOTE.sub("", line)
    out: list[str] = []
    pos = 0
    for m in _RUBY.finditer(line):
        out.append(html.escape(line[pos:m.start()], quote=False))
        base, ruby = (m.group(1), m.group(2)) if m.group(1) is not None else (m.group(3), m.group(4))
        out.append(f"<ruby>{html.escape(base, quote=False)}<rt>{html.escape(ruby, quote=False)}</rt></ruby>")
        pos = m.end()
    out.append(html.escape(line[pos:], quote=False))
    return "".join(out)


def section_to_html(section: str) -> str:
    """1 区切りを段落の並びにする。空行は空の段落(改行の見た目を保つ)。"""
    parts = []
    for line in section.split("\n"):
        body = line_to_html(line)
        parts.append(f"<p>{body}</p>" if body.strip() else "<p><br></p>")
    return "\n".join(parts)
