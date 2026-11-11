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

import bz2
import lzma
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


_LOCAL_HEADER = struct.Struct("<4s5H3L2H")
_ENCRYPTED_FLAG = 0x1


def _lzma_decompressor(raw: bytes) -> tuple[lzma.LZMADecompressor, bytes]:
    """zip の LZMA(2 バイトの版・2 バイトのプロパティ長・プロパティ)を読み、生の展開器と残りを返す。"""
    if len(raw) < 4:
        raise ArchiveError("LZMA のヘッダが途中で切れている")
    props_size, = struct.unpack_from("<H", raw, 2)
    if props_size < 5 or len(raw) < 4 + props_size:
        raise ArchiveError("LZMA のヘッダが途中で切れている")
    filters = [lzma._decode_filter_properties(lzma.FILTER_LZMA1, raw[4:4 + props_size])]
    return lzma.LZMADecompressor(lzma.FORMAT_RAW, filters=filters), raw[4 + props_size:]


def _decompress(raw: bytes, method: int, limit: int) -> bytes:
    """圧縮データを展開する。limit を超えたら LimitExceeded(宣言サイズが嘘の zip でも展開しきらない)。"""
    if method == zipfile.ZIP_STORED:
        if len(raw) > limit:
            raise LimitExceeded(f"エントリの展開サイズが上限を超える(> {limit} バイト)")
        return raw
    if method == zipfile.ZIP_DEFLATED:
        decompressor = zlib.decompressobj(-15)
    elif method == zipfile.ZIP_BZIP2:
        decompressor = bz2.BZ2Decompressor()
    elif method == zipfile.ZIP_LZMA:
        # zipfile.LZMADecompressor は最大長を指定できない。宣言サイズが嘘でも limit までで止める
        decompressor, raw = _lzma_decompressor(raw)
    else:
        raise NotImplementedError(f"未対応の圧縮方式({method})")
    data = decompressor.decompress(raw, limit + 1)
    if len(data) > limit or getattr(decompressor, "unconsumed_tail", b""):
        raise LimitExceeded(f"エントリの展開サイズが上限を超える(> {limit} バイト)")
    if hasattr(decompressor, "eof") and not decompressor.eof:
        raise ArchiveError("圧縮データが途中で切れている")
    return data


def _read_local_entry(fp: BinaryIO, info: zipfile.ZipInfo, limit: int) -> bytes:
    """ローカルヘッダからエントリのデータを読み、展開して返す。

    中央ディレクトリ(ZipInfo)を持っていれば、zipfile.ZipFile を作り直さずに読める。サイズは中央ディレクトリの
    宣言値を使う(データ記述子つきの zip はローカルヘッダのサイズが 0 のため)。
    """
    if info.flag_bits & _ENCRYPTED_FLAG:
        raise ArchiveError("暗号化されたエントリは読めません")
    fp.seek(info.header_offset)
    header = fp.read(_LOCAL_HEADER.size)
    if len(header) != _LOCAL_HEADER.size:
        raise ArchiveError("zip のローカルヘッダが途中で切れている")
    fields = _LOCAL_HEADER.unpack(header)
    if fields[0] != b"PK\x03\x04":
        raise ArchiveError("zip のローカルヘッダの印が合わない")
    name_len, extra_len = fields[-2], fields[-1]
    fp.seek(name_len + extra_len, 1)
    raw = fp.read(info.compress_size)
    if len(raw) != info.compress_size:
        raise ArchiveError(f"zip のエントリが途中で切れている({len(raw)} != {info.compress_size} バイト)")
    try:
        data = _decompress(raw, info.compress_type, limit)
    except NotImplementedError:
        raise
    except (zlib.error, lzma.LZMAError, OSError, EOFError, ValueError, zipfile.BadZipFile) as e:
        raise ArchiveError(f"圧縮データを展開できない: {e}") from e
    if len(data) != info.file_size:
        raise ArchiveError(f"取り出したサイズが目次と合わない({len(data)} != {info.file_size} バイト)")
    if zlib.crc32(data) & 0xFFFFFFFF != info.CRC:
        raise ArchiveError("取り出した中身の CRC が目次と合わない")
    return data


class ZipReader(ArchiveReader):
    def __init__(self, path: Path):
        try:
            # ZipFile(path) は "rb" で開くので元ファイルに書き込まない
            self._zip = zipfile.ZipFile(path)
        except (zipfile.BadZipFile, zipfile.LargeZipFile, OSError, ValueError, UnicodeDecodeError, EOFError) as e:
            raise ArchiveError(f"zip として開けない: {e}") from e
        self._path = Path(path)
        self._infos = self._zip.infolist()

    @classmethod
    def from_cache_state(cls, state) -> "ZipReader":
        """中央ディレクトリを読み直さずに作る(ページ表のキャッシュ用。spinebox/library.py)。"""
        path, infos = state
        self = cls.__new__(cls)
        self._path = Path(path)
        self._infos = infos
        self._zip = None
        return self

    def cache_state(self):
        return self._path, self._infos

    def cache_size(self) -> int:
        return sum(len(i.filename) * 2 + len(i.extra) + len(i.comment) + 128 for i in self._infos)

    @property
    def path(self) -> Path:
        return self._path

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
        if self._zip is None:
            return self._copy_cached(entry, info, max_bytes, dest)
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

    def _copy_cached(self, entry: ArchiveEntry, info: zipfile.ZipInfo, max_bytes: int,
                     dest: BinaryIO) -> int:
        """キャッシュした ZipInfo から、中央ディレクトリを読み直さずに 1 エントリを読む。"""
        try:
            with open(self._path, "rb") as fp:
                data = _read_local_entry(fp, info, max_bytes)
        except LimitExceeded:
            raise
        except ArchiveError:
            raise
        except NotImplementedError:
            # 未対応の圧縮方式だけは zipfile に任せる(中央ディレクトリを読み直すが、読めるものは読む)
            with ZipReader(self._path) as full:
                return full.copy_entry(entry, dest, max_bytes)
        except (OSError, EOFError, ValueError) as e:
            raise ArchiveError(f"エントリを読めない: {e}") from e
        dest.write(data)
        return len(data)

    def read_head(self, entry: ArchiveEntry, size: int) -> bytes | None:
        if self._zip is None:
            # 先頭だけでも展開の途中まで読む必要があるため、通常の ZipReader に任せる
            # (ページ表の読み出しでは通らない。子の巻を作り直すときだけ)
            try:
                with ZipReader(self._path) as full:
                    return full.read_head(entry, size)
            except (ArchiveError, OSError):
                return None
        # 先頭だけを伸長する(CRC は最後まで読まないと検査されないが、形式の判定には十分)
        try:
            with self._zip.open(self._infos[entry.index]) as src:
                return src.read(size)
        except (zipfile.BadZipFile, RuntimeError, NotImplementedError, OSError, EOFError, zlib.error, ValueError):
            return None  # 暗号化・未対応の圧縮方式など。名前で判定し、取り出し時に理由が出る

    def close(self) -> None:
        if self._zip is not None:
            self._zip.close()
