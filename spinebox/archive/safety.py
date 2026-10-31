"""アーカイブのエントリ名の検証と展開上限。

エントリ名はディスク上のパスに使わない(展開キャッシュはハッシュ名で置く)。それでも
`..`・絶対パス・ドライブ文字・UNC・NUL を含む名前は、壊れた/悪意あるアーカイブの印なので
目次の段階でスキップし、理由を記録する。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from ..config import ExtractionLimits

_DRIVE = re.compile(r"^[A-Za-z]:")


@dataclass(frozen=True)
class Skip:
    """スキップしたものと理由。scan_issues に記録される。"""
    entry_name: str | None
    reason: str


def entry_name_problem(name: str) -> str | None:
    """エントリ名が危険ならその理由、安全なら None。区切りは `/` と `\\` の両方を見る。"""
    if not name:
        return "空のエントリ名"
    if "\x00" in name:
        return "エントリ名に NUL を含む"
    if name.startswith(("\\\\", "//")):
        return "UNC パスのエントリ名"
    if name.startswith(("/", "\\")):
        return "絶対パスのエントリ名"
    if _DRIVE.match(name):
        return "ドライブ文字付きのエントリ名"
    if any(part == ".." for part in re.split(r"[\\/]", name)):
        return "親ディレクトリ参照(..)を含むエントリ名"
    return None


def normalize_entry_name(name: str) -> str:
    """区切りを `/` に揃え、空要素と `.` を落とす。危険な名前には使わない(先に entry_name_problem で弾く)。"""
    parts = [p for p in re.split(r"[\\/]", name) if p not in ("", ".")]
    normalized = "/".join(parts)
    if name.endswith(("/", "\\")) and normalized:
        normalized += "/"
    return normalized


def archive_limit_problem(entry_count: int, total_bytes: int, limits: ExtractionLimits) -> str | None:
    """アーカイブ全体が展開上限を超えていればその理由。"""
    if entry_count > limits.max_entries:
        return f"エントリ数が上限を超える({entry_count} > {limits.max_entries})"
    if total_bytes > limits.max_total_bytes:
        return f"展開後サイズの合計が上限を超える({total_bytes} > {limits.max_total_bytes} バイト)"
    return None


def entry_limit_problem(size: int, limits: ExtractionLimits) -> str | None:
    if size > limits.max_entry_bytes:
        return f"エントリの展開サイズが上限を超える({size} > {limits.max_entry_bytes} バイト)"
    return None


def depth_problem(depth: int, limits: ExtractionLimits) -> str | None:
    if depth > limits.max_depth:
        return f"入れ子の深さが上限を超える({depth} > {limits.max_depth})"
    return None
