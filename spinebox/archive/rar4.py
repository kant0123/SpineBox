"""RAR4 のヘッダから、Unicode 名が化けたエントリの本来の名前(OEM 名の cp932)を取り出す(#257)。

RAR4 のファイル名は「OEM 名のバイト列」と、フラグ 0x200 のときその後ろに続く「Unicode 名(独自の圧縮形式)」の 2 本立て。
7-Zip は Unicode 名を優先する。実データに、OEM 名は正しい cp932 なのに、作成ツールが cp932 を欧文の
コードページとして読んで(さらに近似変換して `╠`→`¦` など情報を落として)Unicode 名を作った rar があった。
7-Zip の名前からの逆算では戻らない(`spinebox/mojibake.py` は `君みい５町` 止まり)うえ、cp932 の 2 バイト目の
`0x5C` を区切りとして扱うので、偽のフォルダ(`13_#ò/Äå.jpg` ← `13_#表紙.jpg`)ができる。

ここではヘッダを読むだけで、中身は取り出さない(取り出しは 7-Zip が出した名前のまま)。読めない・想定外の形
(RAR4 でない、ヘッダの暗号化、分割書庫、途中で切れている)なら空を返し、7-Zip の名前をそのまま使う。
"""
from __future__ import annotations

import re
import struct
from pathlib import Path

RAR4_MAGIC = b"Rar!\x1a\x07\x00"

_MAIN_HEAD = 0x73
_FILE_HEAD = 0x74
_END_HEAD = 0x7B
_MHD_VOLUME = 0x0001
_MHD_PASSWORD = 0x0080
_LHD_LARGE = 0x0100
_LHD_UNICODE = 0x0200
_LONG_BLOCK = 0x8000
_MAX_HEADERS = 1_000_000

_JAPANESE = re.compile(r"[ぁ-ゖァ-ヺ㐀-䶿一-鿿]")


def decode_unicode_name(oem: bytes, enc: bytes) -> str | None:
    """RAR4 の Unicode 名の圧縮形式(unrar の EncodeFileName::Decode と同じ)を復号する。壊れていれば None。"""
    try:
        out: list[int] = []
        high = enc[0]
        pos, flags, bits = 1, 0, 0
        while pos < len(enc):
            if bits == 0:
                flags, pos, bits = enc[pos], pos + 1, 8
            bits -= 2
            kind = (flags >> bits) & 3
            if kind == 0:
                out.append(enc[pos])
                pos += 1
            elif kind == 1:
                out.append(enc[pos] | (high << 8))
                pos += 1
            elif kind == 2:
                out.append(enc[pos] | (enc[pos + 1] << 8))
                pos += 2
            else:
                n = enc[pos]
                pos += 1
                if n & 0x80:
                    correction = enc[pos]
                    pos += 1
                    for _ in range((n & 0x7F) + 2):
                        out.append(((oem[len(out)] + correction) & 0xFF) | (high << 8))
                else:
                    for _ in range(n + 2):
                        out.append(oem[len(out)])
        return "".join(map(chr, out))
    except (IndexError, ValueError):
        return None


def _split_name(raw: bytes, flags: int) -> tuple[bytes, str | None]:
    """(OEM 名, Unicode 名)。Unicode 名が無ければ None。"""
    if not flags & _LHD_UNICODE:
        return raw, None
    oem, sep, enc = raw.partition(b"\x00")
    if not sep:
        # 0x200 で区切りが無いのは名前全体が UTF-8(OEM 名は無い)
        return b"", None
    return oem, decode_unicode_name(oem, enc) if enc else None


def read_names(path: Path) -> list[tuple[str, bytes]]:
    """Unicode 名を持つファイル・フォルダの (Unicode 名, OEM 名) をヘッダの順に返す。読めなければ空。"""
    try:
        with open(path, "rb") as f:
            if f.read(len(RAR4_MAGIC)) != RAR4_MAGIC:
                return []
            size_total = f.seek(0, 2)
            pos = len(RAR4_MAGIC)
            names: list[tuple[str, bytes]] = []
            for _ in range(_MAX_HEADERS):
                if pos + 7 > size_total:
                    return names
                f.seek(pos)
                head = f.read(7)
                _crc, kind, flags, size = struct.unpack("<HBHH", head)
                if size < 7 or pos + size > size_total:
                    return []
                body = head + f.read(size - 7)
                add = 0
                if kind == _MAIN_HEAD:
                    if flags & (_MHD_VOLUME | _MHD_PASSWORD):
                        return []
                elif kind == _FILE_HEAD:
                    if size < 32:
                        return []
                    pack, = struct.unpack_from("<I", body, 7)
                    name_size, = struct.unpack_from("<H", body, 26)
                    off = 32
                    if flags & _LHD_LARGE:
                        high_pack, = struct.unpack_from("<I", body, 32)
                        pack |= high_pack << 32
                        off += 8
                    raw = body[off:off + name_size]
                    if len(raw) != name_size:
                        return []
                    oem, uni = _split_name(raw, flags)
                    if uni is not None:
                        names.append((uni, oem))
                    add = pack
                elif kind == _END_HEAD:
                    return names
                elif flags & _LONG_BLOCK:
                    if size < 11:
                        return []
                    add, = struct.unpack_from("<I", body, 7)
                pos += size + add
            return []
    except (OSError, struct.error):
        return []


def recovered_name(unicode_name: str, oem: bytes) -> str | None:
    """Unicode 名が化けていて OEM 名が cp932 として読めるとき、cp932 の名前(区切りは Unicode 名と同じ `\\`)。

    直さないもの: Unicode 名に日本語がある(正しく作られた名前)、OEM 名が cp932 として読めない・日本語が
    2 文字未満(欧文の名前 `café` の OEM 名など)。
    """
    if not oem or unicode_name.isascii() or _JAPANESE.search(unicode_name):
        return None
    try:
        name = oem.decode("cp932")
    except UnicodeDecodeError:
        return None
    if name == unicode_name or len(_JAPANESE.findall(name)) < 2:
        return None
    if any(ord(c) < 0x20 for c in name):
        return None
    return name


def lookup_key(name: str) -> str:
    """7-Zip の名前と突き合わせる形。7-Zip は Windows ではバックスラッシュ、Linux では `/` で区切って出すので `/` にそろえる。"""
    return name.replace("\\", "/").rstrip("/")


def recovered_names(path: Path) -> dict[str, str]:
    """`lookup_key(7-Zip が出す名前 = Unicode 名)` → 本来の名前。直すものが無ければ空。"""
    out: dict[str, str] = {}
    for uni, oem in read_names(path):
        fixed = recovered_name(uni, oem)
        if fixed is not None:
            out.setdefault(lookup_key(uni), fixed)
    return out
