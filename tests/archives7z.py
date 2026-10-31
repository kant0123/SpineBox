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
