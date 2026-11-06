"""名前から巻・話の番号の**候補**を取り出す。

ファイル名・フォルダ名は候補でしかない(CLAUDE.md「蔵書を扱う不変条件」)。ここで取れた番号は
「名前がそう言っている」という根拠つきの値で、確定には中身と確認済みのソースを使う。

拾う表記(全角数字・全角記号は NFKC で半角にしてから見る):

- 巻: `第01巻` `第01-04巻` `01巻` `1~4巻` `v01` `v01s` `v05A` `v01-06` `v01-v06` `Vol.1` `vol 2` `Volume 3`
  (`卷` は `巻` と同じに扱う)
- 話: `第12話` `第1-5話` `12話` `ch.12` `ch12` `Chapter 3` `ep.4` (`话` は `話` と同じに扱う)
- 印の無い範囲 `01-03`(巻の候補として最も弱い根拠)

作品名・著者の候補(`parse_work_name`): 巻番号・配布元の印・タグ・拡張子を落とした残りを、作品名の候補と
その正規化(`normalize_title`。作品の名寄せの突き合わせ用)にする。ここでも結果は候補でしかない。

拾わないもの: 印の無い単独の数字(`DLRAW.TO_001.jpg` の 001 はページ番号)、`全3巻`(総巻数)、
語の途中の `v`(`Dev01`)、年月日らしい 4 桁の数字、数えた数を表す名前(`57 files`。下記)、
小数点の巻(`第04.5巻` は整数の番号で持てないので番号にせず、作品名に残さない)。

`作品名 01` `作品名(3)` の形の末尾の番号は、名前だけでは巻番号と決められない(`Kitchen 2` が
作品名のこともある)ので `parse_number()` では拾わない。代わりに `trailing_number()` が候補を返し、
**同じ名前で末尾の番号だけが違う巻が 2 つ以上並んでいるとき**だけ、呼ぶ側(`spinebox/works.py`)が
巻番号として使う(ファイル名は候補、という不変条件の延長。根拠は `wiki/concepts/work-grouping.md`)。
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import PurePosixPath

# 名前の末尾から外す拡張子(`Vol.1` の `.1` を拡張子として落とさないため、知っているものだけ)
_KNOWN_EXTS = frozenset({
    ".zip", ".cbz", ".rar", ".cbr", ".7z", ".cb7", ".pdf", ".epub", ".txt",
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".avif",
})

# 範囲の区切り(NFKC 後)。`~` は全角の `~`、`〜` は NFKC で変わらないので並べる
_SEP = r"\s*[-~〜–—]\s*"
_NUM = r"(\d{1,4})"
# 英字の印の前は英字でないこと(`Dev01` の v を拾わない)
_LB = r"(?<![A-Za-z])"
# 数字の後ろは数字でないこと
_RB = r"(?!\d)"

# (種類, 規則名, 正規表現)。上から順に強い根拠。最初に当たった規則を採る
_RULES: list[tuple[str, str, re.Pattern]] = [
    ("volume", "第N巻", re.compile(rf"第\s*{_NUM}(?:{_SEP}{_NUM})?\s*巻")),
    ("episode", "第N話", re.compile(rf"第\s*{_NUM}(?:{_SEP}{_NUM})?\s*話")),
    ("volume", "N巻", re.compile(rf"(?<![\d全第]){_NUM}(?:{_SEP}{_NUM})?\s*巻")),
    ("episode", "N話", re.compile(rf"(?<![\d全第]){_NUM}(?:{_SEP}{_NUM})?\s*話")),
    ("volume", "Vol.N", re.compile(rf"{_LB}vol(?:ume)?\.?\s*{_NUM}(?:{_SEP}(?:vol(?:ume)?\.?\s*)?{_NUM})?{_RB}",
                                   re.IGNORECASE)),
    ("volume", "vN", re.compile(rf"{_LB}v\.?\s?{_NUM}(?:{_SEP}v?{_NUM})?(?=[A-Za-z]?(?![A-Za-z\d]))",
                                re.IGNORECASE)),
    ("episode", "ch.N", re.compile(rf"{_LB}(?:ch(?:apter)?|ep(?:isode)?)\.?\s*{_NUM}(?:{_SEP}{_NUM})?{_RB}",
                                   re.IGNORECASE)),
    ("volume", "N-M", re.compile(rf"(?<![\d.\-]){_NUM}{_SEP}{_NUM}(?![\d.\-])")),
]

# 印の無い範囲の上限。これより広い・逆順の範囲は年号や型番とみなして捨てる
_MAX_SPAN = 200


@dataclass(frozen=True)
class NumberCandidate:
    """名前から読んだ巻・話の番号の候補。evidence は名前の中で当たった部分(NFKC 後)。"""
    kind: str       # "volume" | "episode"
    start: int
    end: int
    evidence: str
    rule: str

    @property
    def numbers(self) -> range:
        return range(self.start, self.end + 1)

    def as_dict(self) -> dict:
        return {"kind": self.kind, "start": self.start, "end": self.end, "evidence": self.evidence}


def strip_known_ext(name: str) -> str:
    base = PurePosixPath(name.replace("\\", "/").rstrip("/")).name
    suffix = PurePosixPath(base).suffix
    return base[: -len(suffix)] if suffix.lower() in _KNOWN_EXTS else base


def _fold_chars(text: str) -> str:
    """簡体字の巻・話の表示を正規の字に揃える(`卷`→`巻`、`话`→`話`)。

    `第01卷` のように簡体字で書かれた巻表示を、巻番号として読める形にする。NFKC では変わらない
    ので、正規表現に掛ける前に適用する。
    """
    return text.replace("卷", "巻").replace("话", "話")


def _in_decimal(text: str, m: re.Match) -> bool:
    """当たった数字が小数点の巻(`第04.5巻`)の内側か。整数の巻番号として扱わない。"""
    s, e = m.span(1)
    return (s >= 2 and text[s - 1] == "." and text[s - 2].isdigit()) or \
        (e + 1 < len(text) and text[e] == "." and text[e + 1].isdigit())


def _find_number(text: str) -> tuple[NumberCandidate, tuple[int, int]] | None:
    """NFKC 済みの text から巻・話の番号の候補と、名前の中の位置(span)を返す。"""
    for kind, rule, pattern in _RULES:
        for m in pattern.finditer(text):
            if _in_decimal(text, m):
                continue  # `第04.5巻` の `5巻` は小数点の巻。番号なしとして扱う(作品名へは残さない)
            start = int(m.group(1))
            end = int(m.group(2)) if m.group(2) is not None else start
            if end < start or end - start > _MAX_SPAN:
                continue
            if rule == "N-M" and (start == 0 or len(m.group(1)) > 3 or len(m.group(2)) > 3):
                continue
            return NumberCandidate(kind, start, end, m.group(0).strip(), rule), m.span()
    return None


def parse_number(name: str) -> NumberCandidate | None:
    """名前(パスなら最後の要素)から巻・話の番号の候補を 1 つ返す。見つからなければ None。"""
    found = _find_number(_fold_chars(unicodedata.normalize("NFKC", strip_known_ext(name))))
    return found[0] if found else None


# --- 作品名・著者の候補(#43) ----------------------------------------------------------------------
#
# `[著者] 作品名 第NN巻`、`[作画×原作] 作品名 v01s`、`20260112RAW.TO_Title_v01` のような名前から、
# 作品名と著者の**候補**を取り出す。巻の番号より前の部分を作品名とし、配布元の接頭辞・日付・
# 先頭の `[...]`(著者か、種別・版などのタグ)・末尾のタグを外す。どの部分を何と読んだかは
# `parts` に残す(画面で根拠を見せるため)。

# 名前の先頭の配布元の接頭辞: 日付(6〜8 桁)や `RAW.TO`、`DLRAW.TO`、`site.net` など。直後は区切り
_SITE = r"(?:[A-Za-z]*RAW(?:\.[A-Za-z]{2,3})?|[A-Za-z0-9-]+\.(?:to|se|net|com|org|cc|me|io|jp|info|tv|xyz|club|site|top))"
_DISTRIBUTOR = re.compile(rf"^(?:(?P<date>\d{{8}}|\d{{6}})(?=\D|$)\s*)?(?P<site>{_SITE})?(?:[_\s-]+|$)", re.IGNORECASE)

# 先頭・末尾の括弧 1 組([] 【】 ())
_LEADING_BRACKET = re.compile(r"^\s*(?:\[([^\]]*)\]|【([^】]*)】|\(([^)]*)\))\s*")
_TRAILING_BRACKET = re.compile(r"\s*(?:\[([^\]]*)\]|【([^】]*)】|\(([^)]*)\))\s*$")

# 著者ではなく種別・版・年などを表す括弧の中身(全体が一致するとき)
_TAG = re.compile(
    r"(?:一般|成年|成人|一般向け|成人向け)?(?:コミック|漫画|マンガ|小説|ラノベ|ライトノベル|雑誌|画集|写真集|同人誌|商業誌|アンソロジー)"
    r"|dl版|digital|高画質|高解像度|完結(?:済み?)?|完|全\d+巻|無修正|zip|rar|7z|cbz|cbr|pdf|epub|scan(?:ned)?|hd"
    r"|[a-z0-9-]*raw(?:\.[a-z]{2,3})?|\d{4}(?:[-./]?\d{1,2}){0,2}|v\d+.*",
    re.IGNORECASE)

# 著者の区切り(`[作画×原作]`)。`・` は外国人名の中に出るので割らない
_AUTHOR_SEP = re.compile(r"\s*[×／/、,＆&]\s*")

# 末尾の「全3巻」「完結」
_TRAILING_NOTE = re.compile(r"[\s(\[【]*(?:全\s*\d+\s*巻|完結(?:済み?)?)[\s)\]】]*$")

# 末尾の小数点の巻(`ようこそ実力至上主義の教室へ 第04.5巻`)。整数の番号で持てないので番号にせず、
# 作品名に残して別作品にしないよう、末尾の「第N.M巻/話」ごと落とす(_find_number で整数部に一致させない)
_DECIMAL_VOLUME = re.compile(r"\s*(?:第\s*)?\d{1,4}\.\d{1,3}\s*[巻話]\s*$")

# 作品名として使えない、中身の無い名前
_JUNK_RAW = (
    "cover", "covers", "image", "images", "img", "raw", "scan", "scans", "page", "pages", "jpg", "jpeg", "png",
    "newfolder", "新しいフォルダ", "新しいフォルダー", "無題", "untitled", "download", "downloads", "comic", "comics",
    "manga", "漫画", "マンガ", "コミック",
)


@dataclass(frozen=True)
class WorkName:
    """名前から読んだ作品名・著者の候補。parts は (役割, 名前の中の該当部分) の並び(根拠)。"""
    title: str                         # 表示用に区切りを整えた作品名
    norm: str                          # 突き合わせ用の正規化(normalize_title)
    authors: tuple[str, ...]
    number: NumberCandidate | None
    parts: tuple[tuple[str, str], ...]

    def as_dict(self) -> dict:
        return {"title": self.title, "authors": list(self.authors), "parts": [list(p) for p in self.parts]}


def normalize_title(text: str) -> str:
    """作品名の突き合わせ用の形。NFKC・大小文字・カタカナ/ひらがな・空白や記号の違いを無くす。

    `Okiraku ryoshu no` と `Okiraku_ryoshu_no`、`ＯＫＩ` と `oki` が同じになる。意味の同じ別名
    (ローマ字と日本語)は同じにならない。それは重複候補の証拠や別名で結ぶ。
    """
    t = _fold_chars(unicodedata.normalize("NFKC", text)).casefold()
    t = "".join(chr(ord(ch) - 0x60) if "ァ" <= ch <= "ヶ" else ch for ch in t)
    return "".join(ch for ch in t if ch.isalnum())


_JUNK_NAMES = frozenset(normalize_title(x) for x in _JUNK_RAW)

# 数を数えただけの名前(`57 files` `4 cover`)。配布元の接頭辞を落とすと残る、ファイル数・ページ数の
# 見出しで、作品名ではない。実データでは `DLRAW.AC_57_files` が「57 files」になり、1〜109 files などの
# 74 作品ができていた(#158)。数だけの名前と違い、汎用語が付いているので `_JUNK_NAMES` では拾えない。
_COUNT_NOUNS = (
    "files?", "pages?", "pgs?", "images?", "imgs?", "pics?", "photos?", "pictures?", "covers?",
    "books?", "volumes?", "vols?", "items?", "folders?", "entries?", "sheets?", "frames?", "scans?",
)
_COUNT_NAME = re.compile(rf"^\d{{1,5}}(?:{'|'.join(_COUNT_NOUNS)})$")


def _bracket_content(m: re.Match) -> str:
    return next(g for g in m.groups() if g is not None).strip()


def _clean_title(text: str) -> str:
    text = re.sub(r"\s+", " ", text.replace("_", " ")).strip()
    return text.strip(" -–—:;,.~〜/|\\").strip()


def parse_work_name(name: str) -> WorkName | None:
    """名前(パスなら最後の要素)から作品名・著者の候補を取り出す。作品名と読める部分が無ければ None。"""
    text = _fold_chars(unicodedata.normalize("NFKC", strip_known_ext(name))).strip()
    parts: list[tuple[str, str]] = []

    m = _DISTRIBUTOR.match(text)
    if m and (m.group("date") or m.group("site")) and m.end() > 0:
        parts.append(("distributor", text[:m.end()].strip(" _-")))
        text = text[m.end():]

    authors: list[str] = []
    first_other: str | None = None
    while True:
        m = _LEADING_BRACKET.match(text)
        if not m:
            break
        content = _bracket_content(m)
        if not content or _TAG.fullmatch(content):
            parts.append(("tag", content))
        elif not authors and first_other is None:
            authors = [a.strip() for a in _AUTHOR_SEP.split(content) if a.strip()]
            first_other = content
            parts.append(("author", content))
        else:
            parts.append(("tag", content))
        text = text[m.end():]

    found = _find_number(text)
    number = found[0] if found else None
    if found:
        span = found[1]
        title_raw = text[:span[0]]
        if not _clean_title(title_raw):
            title_raw = text[span[1]:]  # 番号が先頭にある名前(`01巻 作品名`)は後ろを使う
            number_part = text[span[0]:span[1]]
        else:
            number_part = text[span[0]:span[1]]
    else:
        title_raw, number_part = text, None

    # 末尾のタグ括弧と「全N巻」「完結」を外す
    title = title_raw
    while True:
        m = _TRAILING_BRACKET.search(title)
        if not m or not _TAG.fullmatch(_bracket_content(m)):
            break
        title = title[:m.start()]
    title = _TRAILING_NOTE.sub("", title)
    title = _DECIMAL_VOLUME.sub("", title)
    title = _clean_title(title)

    # 作品名が空で、先頭の括弧が著者として読まれていたなら、その括弧が作品名(`[作品名] 第1巻`)
    if not title and first_other is not None:
        title, authors = _clean_title(first_other), []
        parts = [(("title", t) if r == "author" else (r, t)) for r, t in parts]
    elif title:
        parts.append(("title", title))
    norm = normalize_title(title)
    if not norm or norm.isdigit() or norm in _JUNK_NAMES or _COUNT_NAME.match(norm):
        return None
    if number_part is not None:
        parts.append(("number", number_part.strip()))
    return WorkName(title, norm, tuple(authors), number, tuple(parts))


# --- 末尾の番号(同じ名前の組が証拠) -----------------------------------------------------------
#
# `作品名 01` `作品名_01` `作品名(3)` の形。名前だけでは巻番号と決められない(`Kitchen 2` が
# 作品名のこともある)ので `_RULES` には入れない。**同じ名前で末尾の番号だけが違う巻が 2 つ以上**の
# ときに限り、`spinebox/works.py` が巻番号として使う。

_N3 = r"\d{1,3}"
_TRAILING_NUMBER = re.compile(
    rf"^(?P<base>.*?)(?P<tail>[\s_\-－:]+(?P<sep>{_N3})|\s*[\(（]\s*(?P<paren>{_N3})\s*[\)）])\s*$")
# 番号の直前がこの語なら末尾の番号と読まない(印の付いた番号は `_RULES` が先に拾う)
_TRAILING_MARK = re.compile(r"(?:第|no\.?|vol\.?|v|ch\.?|ep\.?|巻|卷|話|话|全)$", re.IGNORECASE)

# 末尾の番号の後ろの装飾。数字でない末尾の括弧(`[レーベル]` `(レーベル)` `[AVIF]` など)を落としてから読む。
# 数字の括弧(`(3)`)は番号として残し、装飾の括弧だけを落とす
_TRAILING_BRACKET_GROUP = re.compile(r"\s*(?:\[([^\]]*)\]|【([^】]*)】|\(([^)]*)\))\s*$")
# 番号の後ろの著者列(`作品名 1 - 著者 & 著者`)。番号とダッシュで区切られた後ろを装飾と読む
_TRAILING_AUTHOR_TAIL = re.compile(
    rf"^(?P<base>.*?)(?P<tail>[\s_\-－:]+(?P<num>{_N3})\s*[-－—]\s*\S.*?)\s*$")


def _strip_trailing_decor(text: str) -> tuple[str, str]:
    """末尾の括弧の装飾を落とし、(残った名前, 落とした装飾)を返す。装飾は根拠(evidence)に残す。"""
    dropped: list[str] = []
    while True:
        m = _TRAILING_BRACKET_GROUP.search(text)
        if m is None:
            break
        content = next(g for g in m.groups() if g is not None).strip()
        if re.fullmatch(r"\d+", content):
            break  # (3) は装飾ではなく番号として読ませる
        dropped.insert(0, text[m.start():].strip())
        text = text[:m.start()]
    return text, " ".join(dropped)


@dataclass(frozen=True)
class TrailingNumber:
    """名前の末尾の番号。単独では巻番号と決めない(同じ名前の組が証拠)。"""
    base: str       # 番号を落とした名前(作品名の候補。正規化は呼ぶ側で)
    number: int     # 1〜999
    evidence: str   # 名前の中で当たった部分(`01` `(3)`)


def trailing_number(name: str) -> TrailingNumber | None:
    """`作品名 01` `作品名(3)` の末尾の番号の候補。巻番号と決めるのは呼ぶ側。

    印の無い単独の数字(`DLRAW.TO_001` の 001)と、作品名として読めない残りは返さない。
    末尾の番号の後ろの括弧・著者列(`作品名 2 [レーベル][AVIF]` `作品名 1 - 著者 & 著者`)は装飾として
    落としてから読み、落とした部分は evidence に残す。
    """
    text = _fold_chars(unicodedata.normalize("NFKC", strip_known_ext(name))).strip()
    text, dropped = _strip_trailing_decor(text)
    m = _TRAILING_NUMBER.match(text)
    if m is not None:
        number = int(m.group("sep") or m.group("paren"))
        base = m.group("base").strip()
        if number == 0 or not _readable_base(base):
            return None
        evidence = m.group("tail").strip()
        if dropped:
            evidence = f"{evidence} {dropped}"
        return TrailingNumber(base, number, evidence)
    m = _TRAILING_AUTHOR_TAIL.match(text)
    if m is None:
        return None
    number = int(m.group("num"))
    base = m.group("base").strip()
    if number == 0 or not _readable_base(base):
        return None
    return TrailingNumber(base, number, m.group("tail").strip())


def _readable_base(base: str) -> bool:
    """末尾の番号の前が、作品名として読める残りか。"""
    norm = normalize_title(base)
    if not norm or norm.isdigit():
        return False
    if base[-1].isdigit():
        return False  # `v01-03` の `03` のように、範囲の後ろを末尾の番号と読まない
    if re.fullmatch(_SITE, base, re.IGNORECASE):  # 配布元の名前の直後の数字(ページ番号など)
        return False
    return not _TRAILING_MARK.search(base)
