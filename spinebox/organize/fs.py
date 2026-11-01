"""整理処理が使うファイルシステムの道具。

- 同じドライブ(ボリューム)かの判定。作業場 → 出力先・元 → 退避フォルダの確定を「改名」で行うため、
  別ドライブになる構成は実行前に拒否する(別ドライブの改名はコピー + 削除に化け、途中で落ちると両方が半端になる)
- 上書きしない移動。移動先に何かあれば必ず失敗させる(POSIX の os.rename は既存のファイルを黙って上書きする)
- 出力ファイル名の無害化(Windows で使えない文字・予約名)
"""
from __future__ import annotations

import os
import re
from pathlib import Path


class CrossDevice(Exception):
    """作業場・退避フォルダ・出力先が元と別のドライブにある。"""


def nearest_existing(path: Path) -> Path:
    """path か、その祖先のうち存在する最も近いもの(まだ作っていない作業場のドライブを調べるため)。"""
    p = Path(os.path.abspath(path))
    while not p.exists():
        if p.parent == p:
            return p
        p = p.parent
    return p


def same_drive(a: Path, b: Path) -> bool:
    """a と b が同じボリューム上か。まだ無いパスは存在する祖先で判定する。

    Windows の os.stat の st_dev はボリュームのシリアル番号なので、ドライブ文字が違えば違う値になる。
    """
    return os.stat(nearest_existing(a)).st_dev == os.stat(nearest_existing(b)).st_dev


def move_no_overwrite(src: Path, dst: Path) -> None:
    """src を dst へ改名で移す。dst に何かあれば FileExistsError。親フォルダは作る。

    Windows の os.rename は既存の移動先で失敗する。POSIX はファイルを黙って上書きするので、
    ファイルはハードリンク(既存なら失敗する)+ 元の削除で移し、フォルダは存在を確かめてから改名する
    (空でないフォルダへの改名は POSIX でも失敗する)。
    """
    src, dst = Path(src), Path(dst)
    if os.path.lexists(dst):
        raise FileExistsError(f"移動先に既にあります: {dst}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    if os.name == "nt" or src.is_dir():
        os.rename(src, dst)
        return
    try:
        os.link(src, dst)
    except FileExistsError:
        raise
    except OSError:
        # ハードリンクを張れないファイルシステム。直前に存在を確かめたうえで改名する
        if os.path.lexists(dst):
            raise FileExistsError(f"移動先に既にあります: {dst}")
        os.rename(src, dst)
        return
    os.unlink(src)


_BAD_CHARS = re.compile(r'[\x00-\x1f<>:"/\\|?*]')
_RESERVED = re.compile(r"^(con|prn|aux|nul|com[0-9]|lpt[0-9])$", re.IGNORECASE)
MAX_STEM = 150


def safe_stem(title: str) -> str:
    """巻の表示名を、Windows でも使えるファイル名の本体にする。"""
    stem = _BAD_CHARS.sub("_", title).strip().rstrip(". ")
    if len(stem) > MAX_STEM:
        stem = stem[:MAX_STEM].rstrip(". ")
    if not stem or _RESERVED.match(stem.split(".")[0]):
        stem = f"_{stem}"
    return stem


def is_within(path: Path, base: Path) -> bool:
    p = os.path.normcase(os.path.normpath(os.path.abspath(path)))
    b = os.path.normcase(os.path.normpath(os.path.abspath(base)))
    return p == b or p.startswith(b.rstrip(os.sep) + os.sep)
