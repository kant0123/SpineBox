"""アーカイブリーダーの共通インターフェース。

各形式のリーダーは「目次の列挙」と「1 エントリの読み出し」だけを実装する。読み出しはメモリか
呼び出し側が渡したファイルへ行い、エントリ名をディスク上のパスに使わない。
エントリ名の検証と展開上限の判定は形式によらないので `list_entries()` でまとめて行う。

形式は先頭のマジックバイトで選び、判定できなければ拡張子で選ぶ(`detect_format()`。rar / 7z を .cbz に
改名したものが実データにある。#49)。zip は標準 zipfile、7z / rar は 7-Zip CLI(`sevenzip.py`)。
7-Zip が見つからないときは UnsupportedArchive になり、スキャンは「未対応」として記録して一覧から外す。
"""
from __future__ import annotations

import abc
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Callable

from ..config import ExtractionLimits
from .safety import Skip, archive_limit_problem, entry_limit_problem, entry_name_problem, normalize_entry_name

# スキャンでアーカイブ(= 巻の候補)として扱う拡張子
ARCHIVE_EXTS = frozenset({".zip", ".cbz", ".rar", ".cbr", ".7z", ".cb7"})


class ArchiveError(Exception):
    """アーカイブを読めない(壊れている・暗号化・未対応の圧縮方式など)。"""


class UnsupportedArchive(ArchiveError):
    """この形式のリーダーがまだ無い。"""


class LimitExceeded(ArchiveError):
    """展開上限を超える。"""


@dataclass(frozen=True)
class ArchiveEntry:
    index: int          # リーダー内での識別子(同名エントリがあっても一意)
    name: str           # 復号・正規化済みの名前(区切りは `/`)
    raw_name: bytes     # アーカイブに書かれていた名前の生バイト列
    encoding: str       # 復号に使った文字コード
    size: int           # 展開後サイズ(目次の宣言値)
    is_dir: bool

    @property
    def parent(self) -> str:
        """親フォルダ(末尾 `/` 付き。直下なら空文字)。"""
        parent = str(PurePosixPath(self.name.rstrip("/")).parent)
        return "" if parent == "." else parent + "/"

    @property
    def basename(self) -> str:
        return PurePosixPath(self.name.rstrip("/")).name


class ArchiveReader(abc.ABC):
    def __enter__(self):
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    @abc.abstractmethod
    def raw_entries(self) -> list[ArchiveEntry]:
        """目次の全エントリ(検証前。name は復号しただけ)。"""

    @abc.abstractmethod
    def copy_entry(self, entry: ArchiveEntry, dest: BinaryIO, max_bytes: int) -> int:
        """1 エントリの中身を dest に書き、書いたバイト数を返す。max_bytes を超えたら LimitExceeded。"""

    @abc.abstractmethod
    def close(self) -> None: ...

    def read_entry(self, entry: ArchiveEntry, limits: ExtractionLimits) -> bytes:
        """1 エントリをメモリに読む(ディスクに書かない)。"""
        import io

        problem = entry_limit_problem(entry.size, limits)
        if problem:
            raise LimitExceeded(problem)
        buf = io.BytesIO()
        self.copy_entry(entry, buf, limits.max_entry_bytes)
        return buf.getvalue()

    # 複数エントリをまとめて読むと速くなる形式か(7-Zip CLI は 1 回の起動で複数を取り出せる)
    batch_reads = False

    def read_entries(self, entries: list[ArchiveEntry], limits: ExtractionLimits) -> dict[int, bytes]:
        """複数エントリをメモリに読む(index → 中身)。既定は 1 つずつ。"""
        return {e.index: self.read_entry(e, limits) for e in entries}


@dataclass
class Listing:
    """検証済みの目次。entries はファイルのみ(フォルダは除く)。"""
    entries: list[ArchiveEntry] = field(default_factory=list)
    dirs: list[ArchiveEntry] = field(default_factory=list)
    skipped: list[Skip] = field(default_factory=list)


def list_entries(reader: ArchiveReader, limits: ExtractionLimits) -> Listing:
    """目次を読み、危険な名前をスキップする。アーカイブ全体が上限を超えていれば LimitExceeded。"""
    raw = reader.raw_entries()
    problem = archive_limit_problem(len(raw), sum(e.size for e in raw if not e.is_dir), limits)
    if problem:
        raise LimitExceeded(problem)
    listing = Listing()
    for e in raw:
        bad = entry_name_problem(e.name)
        if bad:
            listing.skipped.append(Skip(e.name, bad))
            continue
        normalized = normalize_entry_name(e.name)
        if not normalized:
            continue
        e = ArchiveEntry(e.index, normalized, e.raw_name, e.encoding, e.size, e.is_dir or normalized.endswith("/"))
        (listing.dirs if e.is_dir else listing.entries).append(e)
    return listing


# factory(path, sevenzip_path) -> reader。available(sevenzip_path) -> この形式を今読めるか
ReaderFactory = Callable[[Path, str], ArchiveReader]
ReaderAvailable = Callable[[str], bool]
_READERS: dict[str, tuple[ReaderFactory, ReaderAvailable]] = {}
DEFAULT_SEVENZIP = "7z"


def register_reader(ext: str, factory: ReaderFactory, available: ReaderAvailable | None = None) -> None:
    _READERS[ext.lower()] = (factory, available or (lambda sevenzip_path: True))


def is_archive_name(name: str) -> bool:
    return _ext(name) in ARCHIVE_EXTS


def _ext(name: str) -> str:
    return PurePosixPath(name.replace("\\", "/")).suffix.lower()


def reader_available(name: str, sevenzip_path: str = DEFAULT_SEVENZIP) -> bool:
    """name の形式を今読めるか(7z / rar は 7-Zip が見つかるときだけ)。"""
    entry = _READERS.get(_ext(name))
    return entry is not None and entry[1](sevenzip_path)


# 先頭のマジックバイト → 形式。zip は通常のローカルヘッダ・空の zip・分割の印
_MAGIC = (
    (b"PK\x03\x04", "zip"),
    (b"PK\x05\x06", "zip"),
    (b"PK\x07\x08", "zip"),
    (b"Rar!\x1a\x07\x00", "rar"),       # RAR4
    (b"Rar!\x1a\x07\x01\x00", "rar"),   # RAR5
    (b"7z\xbc\xaf\x27\x1c", "7z"),
)
MAGIC_BYTES = max(len(m) for m, _ in _MAGIC)
# 形式 → その形式のリーダーを登録している拡張子
_FORMAT_EXT = {"zip": ".zip", "rar": ".rar", "7z": ".7z"}
_EXT_FORMAT = {".zip": "zip", ".cbz": "zip", ".rar": "rar", ".cbr": "rar", ".7z": "7z", ".cb7": "7z"}


def detect_format(path: Path) -> str | None:
    """先頭のマジックバイトから形式("zip" / "rar" / "7z")を返す。判定できない・読めないなら None。

    読むのは先頭 MAGIC_BYTES バイトだけ(読み取り専用)。自己解凍形式(先頭が実行ファイル)や
    短すぎるファイルは None になり、呼び出し側が拡張子で選ぶ。
    """
    try:
        with open(path, "rb") as f:
            head = f.read(MAGIC_BYTES)
    except OSError:
        return None
    for magic, fmt in _MAGIC:
        if head.startswith(magic):
            return fmt
    return None


def file_reader_available(path: Path, sevenzip_path: str = DEFAULT_SEVENZIP) -> bool:
    """ディスク上の path を今読めるか。中身の形式が分かればそれで、分からなければ拡張子で判定する。"""
    fmt = detect_format(path)
    return reader_available("x" + _FORMAT_EXT[fmt] if fmt else path.name, sevenzip_path)


def open_archive(path: Path, name: str | None = None, *, sevenzip_path: str = DEFAULT_SEVENZIP) -> ArchiveReader:
    """path のアーカイブを読み取り専用で開く。

    形式は先頭のマジックバイトで選び、判定できなければ name(省略時は path)の拡張子で選ぶ。
    拡張子と中身が食い違う(rar を .cbz に改名した等)ときは中身に従う。内側アーカイブは取り出した後の
    ファイルを渡すので、同じ判定になる。
    """
    ext = _ext(name or path.name)
    fmt = detect_format(path)
    if fmt is None:
        entry = _READERS.get(ext)
        if entry is None:
            raise UnsupportedArchive(f"未対応の形式({ext or '拡張子なし'})")
        if _EXT_FORMAT.get(ext) == "zip":
            return _open_zip_or_other(path, entry[0], sevenzip_path)
        return entry[0](path, sevenzip_path)
    factory, available = _READERS[_FORMAT_EXT[fmt]]
    if fmt != _EXT_FORMAT.get(ext) and not available(sevenzip_path):
        raise UnsupportedArchive(f"未対応(中身は {fmt}。拡張子は {ext or 'なし'}): "
                                 f"7-Zip が見つからない(sevenzip_path = {sevenzip_path!r})")
    return factory(path, sevenzip_path)


def _open_zip_or_other(path: Path, zip_factory: ReaderFactory, sevenzip_path: str) -> ArchiveReader:
    """先頭で形式が分からない zip / cbz。zipfile で開けなければ 7-Zip でも試す(自己解凍形式など)。

    7-Zip でも開けなければ「本当に壊れている」として両方の理由を ArchiveError にする。
    """
    try:
        return zip_factory(path, sevenzip_path)
    except UnsupportedArchive:
        raise
    except ArchiveError as zip_error:
        factory, available = _READERS[_FORMAT_EXT["7z"]]
        if not available(sevenzip_path):
            raise
        reader = factory(path, sevenzip_path)
        try:
            reader.raw_entries()  # 開けるかをここで確かめる(目次は読んだ結果がリーダーに残る)
        except ArchiveError as other_error:
            reader.close()
            raise ArchiveError(f"{zip_error}。7-Zip でも開けない: {other_error}") from other_error
        return reader


def _register_builtin() -> None:
    from .sevenzip import EXTS as SEVENZIP_EXTS
    from .sevenzip import make_reader, sevenzip_available
    from .zipreader import ZipReader

    # EPUB も zip として読む(巻の候補にするかはスキャナーが拡張子で決める。spinebox/novel/)
    for ext in (".zip", ".cbz", ".epub"):
        register_reader(ext, lambda path, sevenzip_path: ZipReader(path))
    for ext in SEVENZIP_EXTS:
        register_reader(ext, make_reader, sevenzip_available)


_register_builtin()
