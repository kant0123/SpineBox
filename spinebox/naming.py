"""名前から巻・話の番号の**候補**を取り出す。

ファイル名・フォルダ名は候補でしかない(CLAUDE.md「蔵書を扱う不変条件」)。ここで取れた番号は
「名前がそう言っている」という根拠つきの値で、確定には中身と確認済みのソースを使う。

拾う表記(全角数字・全角記号は NFKC で半角にしてから見る):

- 巻: `第01巻` `第01-04巻` `01巻` `1~4巻` `v01` `v01s` `v05A` `v01-06` `v01-v06` `Vol.1` `vol 2` `Volume 3`
- 話: `第12話` `第1-5話` `12話` `ch.12` `ch12` `Chapter 3` `ep.4`
- 印の無い範囲 `01-03`(巻の候補として最も弱い根拠)

拾わないもの: 印の無い単独の数字(`DLRAW.TO_001.jpg` の 001 はページ番号)、`全3巻`(総巻数)、
語の途中の `v`(`Dev01`)、年月日らしい 4 桁の数字。
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


def parse_number(name: str) -> NumberCandidate | None:
    """名前(パスなら最後の要素)から巻・話の番号の候補を 1 つ返す。見つからなければ None。"""
    text = unicodedata.normalize("NFKC", strip_known_ext(name))
    for kind, rule, pattern in _RULES:
        for m in pattern.finditer(text):
            start = int(m.group(1))
            end = int(m.group(2)) if m.group(2) is not None else start
            if end < start or end - start > _MAX_SPAN:
                continue
            if rule == "N-M" and (start == 0 or len(m.group(1)) > 3 or len(m.group(2)) > 3):
                continue
            return NumberCandidate(kind, start, end, m.group(0).strip(), rule)
    return None
