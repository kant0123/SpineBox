"""命名テンプレートの解釈と展開。

2 つの書き方がある。

- 設定 `[organize] name_template`(`NameTemplate`): 蔵書ルートからの相対パスで、`/` でフォルダを区切り `.cbz` で終わる。
  既定は `<作品名>/<作品名> 第NN巻.cbz`。CBZ 化の出力先と、作品単位の改名・移動が使う(#58)
- 整理画面のファイル名テンプレートとフォルダーテンプレート(`RangeTemplate`。#84): `{title}` の形(Peach と同じ)で書き、
  `[...]` は中の項目が空なら丸ごと省略する。`<作品名>` の書き方もそのまま使える。拡張子は元のものを保つ

共通の規則:

- 変数: 作品名 `title`・著者 `author`・出版社 `publisher`・レーベル(巻の書誌) `label`・種別 `kind`(漫画 / 小説)・発売年 `year`・
  番号 `number`(`<番号>` `<巻番号>` `<話番号>` も同じ。`:3` で 3 桁にゼロ埋め)・単位 `unit`(巻 / 話)。
  `N` を 2 つ以上並べた `NN` は 2 桁の番号の略記
- 番号の直後の `巻` / `話` は、巻の番号の種類(名前から読んだ巻 / 話)に合わせて入れ替わる。既定の
  `第NN巻` が話単位の巻では `第NN話` になる。番号が範囲(まとめ)なら `01-03`
- 著者・レーベル・発売年が空の巻は、`UNKNOWN_VALUES` の「著者不明」「レーベル不明」「発売年不明」で置き換えて展開する
- そのほかの値が無い(作品に割り当てが無い・番号が読めない・出版社・種別が空欄)巻は展開せず、理由つきで対象外にする。
  空文字に置き換えて展開すると、別の巻と同じ名前・意味の無い名前になるため。`[...]` の中だけは省略してよいと書いた所
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

# 値の名前(NameValues.fields の鍵)→ 画面での名前。`<作品名>` も `{作品名}` も `{title}` も同じ変数
FIELDS = {"title": "作品名", "author": "著者", "publisher": "出版社", "label": "レーベル", "kind": "種別",
          "year": "発売年"}
FIELD_VARIABLES = {**{ja: key for key, ja in FIELDS.items()}, **{key: key for key in FIELDS}}
# 空のとき対象外にせず、この文字列で置き換える項目(整理で「不明」の名前にする)
UNKNOWN_VALUES = {"author": "著者不明", "label": "レーベル不明", "year": "発売年不明"}
NUMBER_VARIABLES = ("番号", "巻番号", "話番号", "number")
UNIT_VARIABLES = ("単位", "unit")
MISSING_REASONS = {"title": "作品に割り当てられていません", "author": "作品の著者が空欄です",
                   "publisher": "作品の出版社が空欄です", "label": "巻のレーベルが空欄です",
                   "kind": "作品の種別(漫画 / 小説)が空欄です", "year": "巻の発売日が空欄です"}
UNITS = {"volume": "巻", "episode": "話"}

_TOKEN = re.compile(r"<([^<>]*)>|N{2,}")
_DRIVE = re.compile(r"^[A-Za-z]:")
_BAD_LITERAL = re.compile(r'[\x00-\x1f:"|?*<>]')


class TemplateError(ValueError):
    """テンプレートそのものが不正(設定・入力の誤り)。"""


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
class _Group:
    """`[...]`。中の値が 1 つでも無ければ丸ごと省略する。"""
    parts: tuple


@dataclass(frozen=True)
class NameValues:
    """1 巻ぶんの値。fields は FIELDS の鍵(title / author / …)。number_kind は volume / episode。"""
    fields: dict
    number_kind: str | None = None
    number_start: int | None = None
    number_end: int | None = None


def _known() -> str:
    return "・".join(f"{{{k}}}({ja})" for k, ja in FIELDS.items()) + "・{number}(番号。{number:3} で 3 桁)・{unit}(単位)"


def _variable(body: str, *, legacy: bool):
    """`<…>` / `{…}` の中身を変数にする。知らない名前・桁数の誤りは TemplateError。"""
    name, colon, width = body.partition(":")
    name = name.strip()
    if name in NUMBER_VARIABLES:
        if colon and not (width.isascii() and width.isdigit() and 1 <= int(width) <= 6):
            raise TemplateError(f"桁数は 1〜6 で指定してください: {body}")
        return _Number(int(width) if colon else 1)
    if not colon and name in FIELD_VARIABLES:
        return _Field(FIELD_VARIABLES[name])
    if not colon and name in UNIT_VARIABLES:
        return _Unit()
    if legacy:
        known = "・".join(f"<{n}>" for n in [*FIELDS.values(), *NUMBER_VARIABLES[:3], UNIT_VARIABLES[0]])
        raise TemplateError(f"name_template の変数 <{body}> は使えません(使えるもの: {known})")
    raise TemplateError(f"変数 {body} は使えません(使えるもの: {_known()})")


def _parse_legacy_segment(raw: str, text: str) -> list:
    parts: list = []
    pos = 0
    for m in _TOKEN.finditer(raw):
        parts.append(raw[pos:m.start()])
        pos = m.end()
        parts.append(_Number(len(m.group(0))) if m.group(0).startswith("N") else _variable(m.group(1), legacy=True))
    parts.append(raw[pos:])
    literal = "".join(p for p in parts if isinstance(p, str))
    if _BAD_LITERAL.search(literal):
        raise TemplateError(f"name_template に Windows で使えない文字があります: {text!r}")
    return [p for p in parts if p != ""]


_CLOSERS = {"{": "}", "<": ">"}


def _parse_brace_segment(raw: str) -> list:
    """`{title}`・`<作品名>`・`NN`・`[...]`(省略できる組)。`[[` `]]` `{{` `}}` はその文字そのもの。"""
    top: list = []
    group: list | None = None
    buf: list[str] = []

    def flush():
        if buf:
            (group if group is not None else top).append("".join(buf))
            buf.clear()

    i, n = 0, len(raw)
    while i < n:
        c = raw[i]
        two = raw[i:i + 2]
        if two in ("[[", "]]", "{{", "}}"):
            buf.append(c)
            i += 2
            continue
        if c == "[":
            if group is not None:
                raise TemplateError("[...] の中に [ は書けません(角括弧そのものは [[ ]] と書きます)")
            flush()
            group = []
            i += 1
            continue
        if c == "]":
            if group is None:
                raise TemplateError("対応する [ の無い ] があります(角括弧そのものは [[ ]] と書きます)")
            flush()
            top.append(_Group(tuple(group)))
            group = None
            i += 1
            continue
        if c in _CLOSERS:
            end = raw.find(_CLOSERS[c], i + 1)
            if end < 0:
                raise TemplateError(f"{c} が閉じていません")
            flush()
            (group if group is not None else top).append(_variable(raw[i + 1:end], legacy=False))
            i = end + 1
            continue
        if c in "}>":
            raise TemplateError(f"対応する開きの無い {c} があります")
        if c == "N" and raw[i + 1:i + 2] == "N":
            j = i
            while j < n and raw[j] == "N":
                j += 1
            flush()
            (group if group is not None else top).append(_Number(j - i))
            i = j
            continue
        buf.append(c)
        i += 1
    if group is not None:
        raise TemplateError("[ が閉じていません")
    flush()
    literal = "".join(_literals(top))
    if _BAD_LITERAL.search(literal):
        raise TemplateError(f"Windows で使えない文字があります: {raw!r}")
    return top


def _literals(parts) -> list[str]:
    out = []
    for p in parts:
        if isinstance(p, str):
            out.append(p)
        elif isinstance(p, _Group):
            out.extend(_literals(p.parts))
    return out


def _check_relative(norm: str, text: str, label: str) -> None:
    if norm.startswith("/") or _DRIVE.match(norm):
        raise TemplateError(f"{label} は蔵書ルートからの相対パスで指定してください: {text!r}")


class NameTemplate:
    """設定 `[organize] name_template`。`[` `]` `{` `}` はその文字そのもの(#58 からの書き方を変えない)。"""

    def __init__(self, text: str):
        self.text = text
        self.segments = self._parse(text)

    @staticmethod
    def _parse(text: str) -> list[list]:
        if not isinstance(text, str) or not text.strip():
            raise TemplateError("name_template は空でない文字列で指定してください")
        norm = text.strip().replace("\\", "/")
        _check_relative(norm, text, "name_template")
        if not norm.lower().endswith(".cbz"):
            raise TemplateError(f"name_template は .cbz で終わる名前にしてください: {text!r}")
        segments = []
        for raw in norm.split("/"):
            if raw.strip() in ("", ".", ".."):
                raise TemplateError(f"name_template に空・「.」・「..」の区切りは使えません: {text!r}")
            segments.append(_parse_legacy_segment(raw, text))
        return segments

    @property
    def uses(self) -> set[str]:
        """使っている作品の項目(title / author / publisher …)。"""
        return {p.name for seg in self.segments for p in seg if isinstance(p, _Field)}

    def render(self, values: NameValues) -> str:
        """蔵書ルートからの相対パス(`/` 区切り)。値が無い・使えない名前になるときは ValueMissing。"""
        out = []
        last = len(self.segments) - 1
        for i, seg in enumerate(self.segments):
            text = _render_parts(seg, values)
            if i == last:
                stem = text[:-4] if text.lower().endswith(".cbz") else text
                name = f"{_safe_segment(stem)}.cbz"
            else:
                name = _safe_segment(text)
            out.append(name)
        return "/".join(out)

    def range_templates(self) -> tuple[str, str]:
        """整理画面の (ファイル名テンプレート, フォルダーテンプレート) の書き方にしたもの。同じ名前に展開される。"""
        *folders, last = self.segments
        file_parts = list(last)
        if file_parts and isinstance(file_parts[-1], str) and file_parts[-1].lower().endswith(".cbz"):
            file_parts[-1] = file_parts[-1][:-4]
        return _emit(file_parts), "/".join(_emit(seg) for seg in folders)


def _emit(parts) -> str:
    out = []
    for p in parts:
        if isinstance(p, str):
            out.append(re.sub(r"([\[\]{}])", r"\1\1", p))
        elif isinstance(p, _Field):
            out.append(f"{{{p.name}}}")
        elif isinstance(p, _Number):
            out.append("{number}" if p.width == 1 else f"{{number:{p.width}}}")
        elif isinstance(p, _Unit):
            out.append("{unit}")
        else:
            out.append(f"[{_emit(p.parts)}]")
    return "".join(out)


class RangeTemplate:
    """整理画面のファイル名テンプレートとフォルダーテンプレート(#84)。

    ファイル名は拡張子を含まない 1 つの名前(拡張子は元のものを保つ。末尾の `.cbz` は書いても無視する)。
    フォルダーは範囲のパスからの相対で、`/` で区切れる。どちらも空なら None(ファイル名は整理だけ・フォルダーはそのまま)。
    フォルダーの区切りが `[...]` の省略で空になったら、その区切りごと無くす(`[{publisher}]/{title}` は出版社が空なら
    `{title}` だけ)。ファイル名が空になる巻は対象外。
    """

    def __init__(self, file_template: str = "", folder_template: str = ""):
        self.file_text = (file_template or "").strip()
        self.folder_text = (folder_template or "").strip()
        self.file = self._parse_file(self.file_text) if self.file_text else None
        self.folder = self._parse_folder(self.folder_text) if self.folder_text else None

    @staticmethod
    def _parse_file(text: str) -> list:
        if "/" in text or "\\" in text:
            raise TemplateError("ファイル名テンプレートに / は使えません(フォルダーはフォルダーテンプレートに書きます)")
        if text.lower().endswith(".cbz"):
            text = text[:-4]
        if text.strip() in ("", ".", ".."):
            raise TemplateError("ファイル名テンプレートが空です")
        return _parse_brace_segment(text)

    @staticmethod
    def _parse_folder(text: str) -> list[list]:
        norm = text.replace("\\", "/")
        _check_relative(norm, text, "フォルダーテンプレート")
        segments = []
        for raw in norm.strip("/").split("/"):
            if raw.strip() in ("", ".", ".."):
                raise TemplateError(f"フォルダーテンプレートに空・「.」・「..」の区切りは使えません: {text!r}")
            segments.append(_parse_brace_segment(raw))
        return segments

    def render_file(self, values: NameValues) -> str | None:
        """拡張子を除いたファイル名。ファイル名テンプレートが空なら None。"""
        if self.file is None:
            return None
        text = _render_parts(self.file, values).strip()
        if not text:
            raise ValueMissing("ファイル名が空になります")
        return _safe_segment(text)

    def render_folder(self, values: NameValues) -> list[str] | None:
        """範囲のパスからのフォルダの区切り。フォルダーテンプレートが空なら None(元と同じフォルダ)。"""
        if self.folder is None:
            return None
        out = []
        for seg in self.folder:
            text = _render_parts(seg, values).strip()
            if text:
                out.append(_safe_segment(text))
        return out


def _render_parts(parts, values: NameValues) -> str:
    buf = []
    for idx, p in enumerate(parts):
        if isinstance(p, str):
            if idx > 0 and isinstance(parts[idx - 1], _Number) and p[:1] in ("巻", "話") \
                    and values.number_kind in UNITS:
                # 番号の直後の単位は、巻の番号の種類に合わせる(第NN巻 → 話単位なら 第NN話)
                p = UNITS[values.number_kind] + p[1:]
            buf.append(p)
        elif isinstance(p, _Group):
            try:
                buf.append(_render_parts(p.parts, values))
            except ValueMissing:
                pass  # 中の値が空なら丸ごと省略
        elif isinstance(p, _Field):
            value = str(values.fields.get(p.name) or "").strip() or UNKNOWN_VALUES.get(p.name, "")
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


# --- 整理画面の選択肢(#84) -------------------------------------------------------------

# 「テンプレートを適用」のプリセット。(名前, ファイル名テンプレート, フォルダーテンプレート)
_VOLUME_NAME = "{title} 第{number:2}{unit}"
PRESETS = (
    ("作品名と巻数", _VOLUME_NAME, ""),
    ("作品名と巻数(著者つき)", _VOLUME_NAME + "[ ({author})]", ""),
    ("作品別フォルダー", _VOLUME_NAME, "{title}"),
    ("著者別フォルダー", _VOLUME_NAME, "{author}/{title}"),
    ("出版社別フォルダー", _VOLUME_NAME, "{publisher}/{title}"),
    ("レーベル別フォルダー", _VOLUME_NAME, "{label}/{title}"),
    ("発売年別フォルダー", _VOLUME_NAME, "{year}/{title}"),
    ("種別(漫画 / 小説)別フォルダー", _VOLUME_NAME, "{kind}/{title}"),
)

# 「パラメーターを挿入」のチップ。(挿入する文字, 画面の名前)
PARAMETERS = (
    ("{title}", "作品名"), ("{author}", "著者"), ("{publisher}", "出版社"), ("{label}", "レーベル"),
    ("{number:2}", "巻番号(2 桁)"), ("{number:3}", "巻番号(3 桁)"), ("{unit}", "単位(巻 / 話)"),
    ("{kind}", "種別(漫画 / 小説)"), ("{year}", "発売年"),
)

# パスの例に使う値。2 つ目はレーベル・発売年・著者が空の巻(`[...]` の省略と、空欄で対象外になるのを見せる)
EXAMPLE_VALUES = (
    ("値がそろった巻", NameValues({"title": "お気楽領主の楽しい領地防衛", "author": "作者 太郎", "publisher": "出版社X",
                                 "label": "レーベルY", "kind": "漫画", "year": "2020"}, "volume", 3, 3)),
    ("レーベル・発売年・著者が空の話", NameValues({"title": "ある作品", "author": None, "publisher": "出版社X",
                                            "label": None, "kind": "漫画", "year": None}, "episode", 12, 12)),
)


def range_examples(file_template: str, folder_template: str, path: str = "") -> list[dict]:
    """パスの例(範囲のパスからの相対ではなく、蔵書ルートからの相対)。テンプレートが不正なら TemplateError。

    ファイル名テンプレートが空なら元の名前を整理したもの、フォルダーテンプレートが空なら元と同じフォルダになる。
    """
    template = RangeTemplate(file_template, folder_template)
    out = []
    base = [p for p in (path or "").strip("/").split("/") if p]
    for label, values in EXAMPLE_VALUES:
        try:
            stem = template.render_file(values)
            folder = template.render_folder(values)
        except ValueMissing as e:
            out.append({"label": label, "path": None, "reason": str(e)})
            continue
        dirs = ["(元のフォルダ)"] if folder is None else [*base, *folder]
        name = f"{stem}.cbz" if stem is not None else "(元の名前を整理したもの).cbz"
        out.append({"label": label, "path": "/".join([*dirs, name]), "reason": None})
    return out
