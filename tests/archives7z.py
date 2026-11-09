"""7z / rar のテスト用アーカイブを作る道具。"""
from __future__ import annotations

import struct
import subprocess
import tempfile
import zlib
from pathlib import Path


def _vint(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        out.append(b | (0x80 if n else 0))
        if not n:
            return bytes(out)


def _rar5_block(btype: int, flags: int, fields: bytes, data: bytes = b"") -> bytes:
    body = _vint(btype) + _vint(flags) + (_vint(len(data)) if data else b"") + fields
    head = _vint(len(body)) + body
    return struct.pack("<L", zlib.crc32(head) & 0xFFFFFFFF) + head + data


def rar5_bytes(entries) -> bytes:
    """無圧縮(stored)の RAR5 を手で組む(rar の圧縮器が無い環境でも 7-Zip のリーダーを試せるように)。

    entries: [(name: str, data: bytes | None)]。data が None ならフォルダ。
    """
    out = bytearray(b"Rar!\x1a\x07\x01\x00")
    out += _rar5_block(1, 0, _vint(0))  # main header: archive flags
    for name, data in entries:
        raw = name.encode("utf-8")
        if data is None:
            fields = _vint(0x1) + _vint(0) + _vint(0x10) + _vint(0) + _vint(1) + _vint(len(raw)) + raw
            out += _rar5_block(2, 0, fields)
        else:
            fields = (_vint(0x4) + _vint(len(data)) + _vint(0x20) + struct.pack("<L", zlib.crc32(data) & 0xFFFFFFFF)
                      + _vint(0) + _vint(1) + _vint(len(raw)) + raw)
            out += _rar5_block(2, 0x2, fields, data)
    out += _rar5_block(5, 0, _vint(0))  # end of archive
    return bytes(out)


def make_7z(sevenzip: str, dest: Path, files: dict[str, bytes]) -> Path:
    """files({アーカイブ内パス: 中身})から 7z を作る。作成は別の一時フォルダで行い、結果だけを dest へ置く。"""
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "src"
        for rel, data in files.items():
            p = src / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.unlink(missing_ok=True)
        # `*` は -spd でも書庫作成側のワイルドカードとして働く。-spd は「名前に * を含むファイル」を
        # 取りこぼさないために付けない(src 直下の全項目を渡す)
        items = [p.name for p in sorted(src.iterdir())]
        subprocess.run([sevenzip, "a", "-bso0", "-bsp0", "-y", str(dest), "--", *items], cwd=src, check=True)
    return dest


def _rar4_unicode(name: str) -> bytes:
    """RAR4 の Unicode 名の圧縮形式(全部 16 ビットそのまま = 種類 2)で name を符号化する。"""
    out = bytearray([0])  # high byte(種類 2 では使わない)
    codes = [ord(c) for c in name]
    for i in range(0, len(codes), 4):
        group = codes[i:i + 4]
        flags = 0
        for j in range(4):
            flags = (flags << 2) | (2 if j < len(group) else 0)
        out.append(flags)
        for code in group:
            out += struct.pack("<H", code)
    return bytes(out)


def _rar4_block(kind: int, flags: int, body: bytes) -> bytes:
    head = struct.pack("<BHH", kind, flags, 7 + len(body)) + body
    return struct.pack("<H", zlib.crc32(head) & 0xFFFF) + head


def rar4_bytes(entries) -> bytes:
    """無圧縮(stored)の RAR4 を手で組む。

    entries: [(oem_name: bytes, unicode_name: str | None, data: bytes | None)]。区切りはバックスラッシュ。
    unicode_name を渡すと Unicode 名フラグ(0x200)付きで OEM 名の後ろに付ける(化けた Unicode 名を再現できる)。
    data が None ならフォルダ。
    """
    out = bytearray(b"Rar!\x1a\x07\x00")
    out += _rar4_block(0x73, 0, b"\x00" * 6)
    for oem, uni, data in entries:
        name = oem if uni is None else oem + b"\x00" + _rar4_unicode(uni)
        flags = 0x8000 | (0x200 if uni is not None else 0) | (0xE0 if data is None else 0)
        payload = data or b""
        attr = 0x10 if data is None else 0x20
        body = struct.pack("<IIBIIBBHI", len(payload), len(payload), 2, zlib.crc32(payload) & 0xFFFFFFFF,
                           0x3EC10000, 20, 0x30, len(name), attr) + name
        out += _rar4_block(0x74, flags, body) + payload
    out += _rar4_block(0x7B, 0x4000, b"")
    return bytes(out)
