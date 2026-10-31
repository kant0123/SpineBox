"""zip / cbz のリーダー(Python 標準 zipfile)。

名前の復号は次の順(GOAL.md「試作で確かめた前提」: 実データに 3 種が混在する)。

1. UTF-8 フラグ(汎用ビット 11)が立っていれば UTF-8
2. Info-ZIP Unicode Path 拡張(0x7075)があり、その CRC が生の名前と一致すれば、その UTF-8 名
3. cp932(Windows の日本語環境で作られた zip)
4. それでも駄目なら cp932 で置換文字(U+FFFD)入り

zipfile 自身も 0x7075 を解釈して `filename` を書き換えることがあるが、版によって挙動が違うため
使わず、ヘッダに書かれたままの名前(`orig_filename`)から生バイト列を復元して自前で復号する。
"""
from __future__ import annotations

import struct
import zipfile
import zlib
from pathlib import Path
from typing import BinaryIO

from .base import ArchiveEntry, ArchiveError, ArchiveReader, LimitExceeded

UTF8_FLAG = 0x800
UNICODE_PATH_EXTRA = 0x7075
_CHUNK = 1024 * 1024


def raw_name(info: zipfile.ZipInfo) -> bytes:
    """ヘッダに書かれていた名前の生バイト列。zipfile はフラグが無ければ cp437(全 256 値を可逆に写す)で読む。"""
    return info.orig_filename.encode("utf-8" if info.flag_bits & UTF8_FLAG else "cp437")


def unicode_path_extra(extra: bytes, raw: bytes) -> str | None:
    """0x7075 の UTF-8 名。無い・壊れている・CRC が生の名前と合わない(名前だけ書き換えられた)なら None。"""
    pos = 0
    while pos + 4 <= len(extra):
        tag, size = struct.unpack_from("<HH", extra, pos)
        data = extra[pos + 4:pos + 4 + size]
        pos += 4 + size
        if tag != UNICODE_PATH_EXTRA or len(data) < 5:
            continue
        version, crc = struct.unpack_from("<BL", data, 0)
        if version != 1 or crc != (zlib.crc32(raw) & 0xFFFFFFFF):
            continue
        try:
            name = data[5:].decode("utf-8")
        except UnicodeDecodeError:
            continue
        if name:
            return name
    return None


def decode_name(info: zipfile.ZipInfo) -> tuple[str, bytes, str]:
    """(復号した名前, 生バイト列, 使った文字コード)。"""
    raw = raw_name(info)
    if info.flag_bits & UTF8_FLAG:
        return info.orig_filename, raw, "utf-8"
    unicode_path = unicode_path_extra(info.extra, raw)
    if unicode_path is not None:
        return unicode_path, raw, "utf-8 (unicode path extra)"
    if raw.isascii():
        return raw.decode("ascii"), raw, "ascii"
    try:
        return raw.decode("cp932"), raw, "cp932"
    except UnicodeDecodeError:
        return raw.decode("cp932", errors="replace"), raw, "cp932 (replaced)"


class ZipReader(ArchiveReader):
    def __init__(self, path: Path):
        try:
            # ZipFile(path) は "rb" で開くので元ファイルに書き込まない
            self._zip = zipfile.ZipFile(path)
        except (zipfile.BadZipFile, zipfile.LargeZipFile, OSError, ValueError, UnicodeDecodeError, EOFError) as e:
            raise ArchiveError(f"zip として開けない: {e}") from e
        self._infos = self._zip.infolist()

    def raw_entries(self) -> list[ArchiveEntry]:
        entries = []
        for i, info in enumerate(self._infos):
            name, raw, encoding = decode_name(info)
            entries.append(ArchiveEntry(i, name, raw, encoding, info.file_size,
                                        info.is_dir() or name.endswith(("/", "\\"))))
        return entries

    def copy_entry(self, entry: ArchiveEntry, dest: BinaryIO, max_bytes: int) -> int:
        info = self._infos[entry.index]
        if info.file_size > max_bytes:
            raise LimitExceeded(f"エントリの展開サイズが上限を超える({info.file_size} > {max_bytes} バイト)")
        written = 0
        try:
            # ZipExtFile は宣言サイズ(file_size)までしか返さず、末尾で CRC を検査する
            with self._zip.open(info) as src:
                while chunk := src.read(_CHUNK):
                    written += len(chunk)
                    if written > max_bytes:
                        raise LimitExceeded(f"エントリの展開サイズが上限を超える(> {max_bytes} バイト)")
                    dest.write(chunk)
        except LimitExceeded:
            raise
        except (zipfile.BadZipFile, RuntimeError, NotImplementedError, OSError, EOFError, zlib.error, ValueError) as e:
            # RuntimeError: 暗号化、NotImplementedError: 未対応の圧縮方式
            raise ArchiveError(f"エントリを読めない: {e}") from e
        return written

    def close(self) -> None:
        self._zip.close()
