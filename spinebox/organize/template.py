"""命名テンプレート(`[organize] name_template`)の解釈と展開。

テンプレートは蔵書ルートからの相対パスで、`/` でフォルダを区切る。既定は `<作品名>/<作品名> 第NN巻.cbz`。

- 変数: `<作品名>` `<著者>` `<出版社>` `<番号>`(`<巻番号>` `<話番号>` も同じ)・`<番号:3>`(3 桁にゼロ埋め)・
  `<単位>`(巻 / 話)。`N` を 2 つ以上並べた `NN` は `<番号:2>` の略記
- 番号の直後の `巻` / `話` は、巻の番号の種類(名前から読んだ巻 / 話)に合わせて入れ替わる。既定の
  `第NN巻` が話単位の巻では `第NN話` になる。番号が範囲(まとめ)なら `01-03`
- 値が無い(作品に割り当てが無い・番号が読めない・著者が空欄)巻は展開せず、理由つきで対象外にする。
  空文字に置き換えて展開すると、別の巻と同じ名前・意味の無い名前になるため
- 値の中の `/`・バックスラッシュや Windows で使えない文字は `_` にする(作品名でフォルダを増やさない)。区切りごとに末尾の
  `.`・空白を落とし、予約名(CON・NUL・COM1 など)は先頭に `_` を付け、長すぎる区切りは切り詰める。
  `..`・絶対パス・ドライブ文字はテンプレートの検証で拒み、値からは作れない
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from .fs import MAX_STEM, safe_stem

DEFAULT_NAME_TEMPLATE = "<作品名>/<作品名> 第NN巻.cbz"
# Windows の MAX_PATH(260。終端の NUL を含む)。長いパスを有効にしていない環境でも開けるようにする
MAX_PATH = 259

# テンプレートの変数名 → 値の名前。番号と単位は巻ごとに決まるので別扱い
FIELD_VARIABLES = {"作品名": "title", "著者": "author", "出版社": "publisher"}
NUMBER_VARIABLES = ("番号", "巻番号", "話番号")
UNIT_VARIABLE = "単位"
MISSING_REASONS = {"title": "作品に割り当てられていません", "author": "作品の著者が空欄です",
                   "publisher": "作品の出版社が空欄です"}
UNITS = {"volume": "巻", "episode": "話"}

_TOKEN = re.compile(r"<([^<>]*)>|N{2,}")
_DRIVE = re.compile(r"^[A-Za-z]:")
_BAD_LITERAL = re.compile(r'[\x00-\x1f:"|?*<>]')


class TemplateError(ValueError):
    """テンプレートそのものが不正(設定の誤り)。"""


class ValueMissing(Exception):
    """この巻にはテンプレートの値が無い・展開した名前が使えない。メッセージは利用者向けの理由。"""


@dataclass(frozen=True)
class _Number:
    width: int


@dataclass(frozen=True)
class _Field:
    name: str


@dataclass(frozen=True)
class _Unit:
    pass


@dataclass(frozen=True)
class NameValues:
    """1 巻ぶんの値。fields は作品の項目(title / author / publisher)。number_kind は volume / episode。"""
    fields: dict
    number_kind: str | None = None
    number_start: int | None = None
    number_end: int | None = None


class NameTemplate:
    def __init__(self, text: str):
        self.text = text
        self.segments = self._parse(text)

    @staticmethod
    def _parse(text: str) -> list[list]:
        if not isinstance(text, str) or not text.strip():
            raise TemplateError("name_template は空でない文字列で指定してください")
        norm = text.strip().replace("\\", "/")
        if norm.startswith("/") or _DRIVE.match(norm):
            raise TemplateError(f"name_template は蔵書ルートからの相対パスで指定してください: {text!r}")
        if not norm.lower().endswith(".cbz"):
            raise TemplateError(f"name_template は .cbz で終わる名前にしてください: {text!r}")
        segments = []
        for raw in norm.split("/"):
            if raw.strip() in ("", ".", ".."):
                raise TemplateError(f"name_template に空・「.」・「..」の区切りは使えません: {text!r}")
            parts: list = []
            pos = 0
            for m in _TOKEN.finditer(raw):
                parts.append(raw[pos:m.start()])
                pos = m.end()
                if m.group(0).startswith("N"):
                    parts.append(_Number(len(m.group(0))))
                    continue
                name, _, width = m.group(1).partition(":")
                if name in FIELD_VARIABLES and not width:
                    parts.append(_Field(FIELD_VARIABLES[name]))
                elif name in NUMBER_VARIABLES:
                    if width and not (width.isascii() and width.isdigit() and 1 <= int(width) <= 6):
                        raise TemplateError(f"桁数は 1〜6 で指定してください: <{m.group(1)}>")
                    parts.append(_Number(int(width) if width else 1))
                elif name == UNIT_VARIABLE and not width:
                    parts.append(_Unit())
                else:
                    known = "・".join(f"<{n}>" for n in [*FIELD_VARIABLES, *NUMBER_VARIABLES, UNIT_VARIABLE])
                    raise TemplateError(f"name_template の変数 <{m.group(1)}> は使えません(使えるもの: {known})")
            parts.append(raw[pos:])
            literal = "".join(p for p in parts if isinstance(p, str))
            if _BAD_LITERAL.search(literal):
                raise TemplateError(f"name_template に Windows で使えない文字があります: {text!r}")
            segments.append([p for p in parts if p != ""])
        return segments

    @property
    def uses(self) -> set[str]:
        """使っている作品の項目(title / author / publisher)。"""
        return {p.name for seg in self.segments for p in seg if isinstance(p, _Field)}

    def render(self, values: NameValues) -> str:
        """蔵書ルートからの相対パス(`/` 区切り)。値が無い・使えない名前になるときは ValueMissing。"""
        out = []
        last = len(self.segments) - 1
        for i, seg in enumerate(self.segments):
            text = self._render_segment(seg, values)
            if i == last:
                stem = text[:-4] if text.lower().endswith(".cbz") else text
                name = f"{_safe_segment(stem)}.cbz"
            else:
                name = _safe_segment(text)
            out.append(name)
        return "/".join(out)

    def _render_segment(self, seg: list, values: NameValues) -> str:
        buf = []
        for idx, p in enumerate(seg):
            if isinstance(p, str):
                if idx > 0 and isinstance(seg[idx - 1], _Number) and p[:1] in ("巻", "話") \
                        and values.number_kind in UNITS:
                    # 番号の直後の単位は、巻の番号の種類に合わせる(第NN巻 → 話単位なら 第NN話)
                    p = UNITS[values.number_kind] + p[1:]
                buf.append(p)
            elif isinstance(p, _Field):
                value = (values.fields.get(p.name) or "").strip()
                if not value:
                    raise ValueMissing(MISSING_REASONS[p.name])
                buf.append(_clean_value(value))
            elif isinstance(p, _Number):
                if values.number_start is None:
                    raise ValueMissing("名前から巻・話の番号を読めません")
                start, end = values.number_start, values.number_end
                text = f"{start:0{p.width}d}"
                if end is not None and end != start:
                    text += f"-{end:0{p.width}d}"
                buf.append(text)
            else:  # _Unit
                if values.number_kind not in UNITS:
                    raise ValueMissing("巻か話かが分かりません")
                buf.append(UNITS[values.number_kind])
        return "".join(buf)


_VALUE_BAD = re.compile(r'[\x00-\x1f<>:"/\\|?*]')


def _clean_value(value: str) -> str:
    """値をパスの区切り 1 つの中に収める(`/` などで区切りを増やさない)。"""
    return re.sub(r"\s+", " ", _VALUE_BAD.sub("_", value)).strip()


def _safe_segment(text: str) -> str:
    """区切り 1 つを Windows でも使える名前にする(末尾の . と空白・予約名・長さ)。`..` は `_` になる。"""
    seg = safe_stem(text)
    if len(seg) > MAX_STEM:
        seg = seg[:MAX_STEM].rstrip(". ")
    if seg.strip(".") == "":
        seg = "_"
    return seg


def check_path_length(root: Path, rel_path: str) -> None:
    """蔵書ルートを含めた長さが Windows の上限を超えるなら ValueMissing。"""
    full = os.path.join(str(root), *rel_path.split("/"))
    if len(full) > MAX_PATH:
        raise ValueMissing(f"パスが長すぎます({len(full)} 文字。上限 {MAX_PATH} 文字)")
