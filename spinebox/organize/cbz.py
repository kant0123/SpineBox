"""CBZ の作成と検証。

CBZ は標準 ZIP。画像は再圧縮しても縮まない(JPEG / PNG は圧縮済み)うえ、読むたびの展開を省けるので
無圧縮(ZIP_STORED)で入れる。ページ順は今の並び順(表紙 → 自然順)を `0001.jpg` 形式の名前で固定する
(元の名前の並びに頼ると、読み直したときに並べ方の規則が変わっても順序が変わらないようにするため)。
"""
from __future__ import annotations

import hashlib
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable, Iterable


class VerificationFailed(Exception):
    """作った CBZ が元と一致しない。"""


@dataclass(frozen=True)
class PageRecord:
    name: str      # CBZ 内の名前(0001.jpg)
    sha256: str    # 元の画像のバイト列の sha256
    size: int


def page_entry_names(source_names: list[str]) -> list[str]:
    """元のページ名の並びから CBZ 内の名前を作る。拡張子は元のものを小文字で残す。"""
    width = max(4, len(str(len(source_names))))
    return [f"{i + 1:0{width}d}{PurePosixPath(n).suffix.lower()}" for i, n in enumerate(source_names)]


def write_cbz(path: Path, source_names: list[str], read_page: Callable[[int], bytes]) -> list[PageRecord]:
    """path に新しく CBZ を作り(既にあれば失敗する)、各ページの記録を返す。"""
    records = []
    date_time = time.localtime()[:6]
    with zipfile.ZipFile(path, "x", compression=zipfile.ZIP_STORED, allowZip64=True) as z:
        for i, name in enumerate(page_entry_names(source_names)):
            data = read_page(i)
            info = zipfile.ZipInfo(name, date_time=date_time)
            info.compress_type = zipfile.ZIP_STORED
            z.writestr(info, data)
            records.append(PageRecord(name, hashlib.sha256(data).hexdigest(), len(data)))
    return records


def verify_cbz(path: Path, expected: Iterable[PageRecord]) -> None:
    """CBZ を読み直し、ページ数・名前・各画像のバイト列の sha256 が元と一致するかを確かめる。"""
    expected = list(expected)
    try:
        with zipfile.ZipFile(path) as z:
            infos = [i for i in z.infolist() if not i.is_dir()]
            if len(infos) != len(expected):
                raise VerificationFailed(f"ページ数が一致しない(元 {len(expected)} / CBZ {len(infos)}): {path.name}")
            for info, rec in zip(infos, expected):
                if info.filename != rec.name:
                    raise VerificationFailed(f"ページの並びが一致しない({rec.name} の位置に {info.filename}): {path.name}")
                if info.compress_type != zipfile.ZIP_STORED:
                    raise VerificationFailed(f"無圧縮になっていない: {path.name}/{info.filename}")
                # read() は CRC も確かめる
                digest = hashlib.sha256(z.read(info)).hexdigest()
                if digest != rec.sha256:
                    raise VerificationFailed(f"画像のハッシュが元と一致しない: {path.name}/{info.filename}")
    except (zipfile.BadZipFile, OSError) as e:
        raise VerificationFailed(f"CBZ を読み直せない: {path.name}: {e}") from e
