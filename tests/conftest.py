"""テスト共通の道具。蔵書はすべて一時ディレクトリに動的生成する(`_参考/` に依存しない)。"""
from __future__ import annotations

import io
import os
import struct
import sys
import zipfile
import zlib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from spinebox.archive.sevenzip import find_sevenzip  # noqa: E402
from spinebox.config import ExtractionLimits, Settings  # noqa: E402

# 7-Zip を使わない(見つからない)設定。テストの既定にして、実行環境の 7z の有無に結果を左右させない
NO_SEVENZIP = "spinebox-test-no-such-7z"


class RawName(zipfile.ZipInfo):
    """ヘッダに書く名前の生バイト列と UTF-8 フラグを直接決める ZipInfo。

    zipfile の書き込みは Python の版で名前の符号化が違う(3.12 は非 ASCII を常に UTF-8 + フラグ、
    3.13 はフラグ無しなら cp437)ので、テストでは生バイト列を自分で決める。
    """

    def __init__(self, raw: bytes, *, utf8_flag: bool = False, extra: bytes = b""):
        super().__init__("x")
        self.filename = raw.decode("utf-8") if utf8_flag else raw.decode("cp437")
        self._raw = raw
        self._name_flags = 0x800 if utf8_flag else 0
        self.extra = extra
        self.compress_type = zipfile.ZIP_DEFLATED

    def _encodeFilenameFlags(self):  # noqa: N802 (zipfile の内部 API を上書き)
        # writestr は flag_bits を 0 に戻してから呼ぶので、ここで UTF-8 フラグを足す
        return self._raw, (self.flag_bits & ~0x800) | self._name_flags


def unicode_path_extra(raw: bytes, unicode_name: str, *, crc: int | None = None) -> bytes:
    data = struct.pack("<BL", 1, zlib.crc32(raw) if crc is None else crc) + unicode_name.encode("utf-8")
    return struct.pack("<HH", 0x7075, len(data)) + data


def page_bytes(label: str) -> bytes:
    """画像のふりをするバイト列(中身の判定はしないので本物の画像でなくてよい)。"""
    return b"\xff\xd8\xff\xe0FAKEJPEG:" + label.encode("utf-8")


def zip_bytes(entries) -> bytes:
    """entries: [(name: str | ZipInfo, data: bytes | None)]。data が None ならフォルダのエントリ。"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in entries:
            if data is None:
                z.writestr(name if isinstance(name, zipfile.ZipInfo) else zipfile.ZipInfo(name), b"")
            else:
                z.writestr(name, data)
    return buf.getvalue()


def write_zip(path: Path, entries) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(zip_bytes(entries))
    return path


def snapshot(root: Path) -> dict:
    """フォルダ配下の全ファイルの (バイト列, サイズ, mtime_ns)。"""
    result = {}
    for dirpath, _, files in os.walk(root):
        for f in files:
            p = Path(dirpath) / f
            st = p.stat()
            result[str(p.relative_to(root))] = (p.read_bytes(), st.st_size, st.st_mtime_ns)
    return result


@pytest.fixture
def library(tmp_path) -> Path:
    """標準の蔵書: 画像フォルダ / 単巻 cbz / 複数巻フォルダの zip / 内側アーカイブ入りの zip / 壊れた zip / 7z。"""
    lib = tmp_path / "lib"
    folder = lib / "FolderBook"
    folder.mkdir(parents=True)
    for n in ["10.jpg", "2.jpg", "1.jpg", "cover.JPG", "notes.txt"]:
        (folder / n).write_bytes(page_bytes(f"folder/{n}"))
    write_zip(lib / "single.cbz", [(n, page_bytes(f"single/{n}")) for n in ["10.jpg", "2.jpg", "1.jpg", "info.txt"]])
    write_zip(lib / "multi.zip", [
        ("Vol1/", None), ("Vol1/001.jpg", page_bytes("v1/001")), ("Vol1/002.jpg", page_bytes("v1/002")),
        ("Vol2/", None), ("Vol2/001.jpg", page_bytes("v2/001")),
    ])
    inner1 = zip_bytes([("Book1/", None)] + [(f"Book1/{n}", page_bytes(f"inner1/{n}"))
                                             for n in ["10.png", "9.png", "DLRAW.TO_cover.jpg"]])
    inner2 = zip_bytes([(n, page_bytes(f"inner2/{n}")) for n in ["a.webp", "b.webp"]])
    write_zip(lib / "series" / "nested.zip", [
        ("inner1.zip", inner1), ("inner2.cbz", inner2), ("inner3.rar", b"Rar!\x1a\x07\x00junk"), ("readme.txt", b"hi"),
    ])
    (lib / "broken.zip").write_bytes(b"this is not a zip")
    (lib / "series" / "solid.7z").write_bytes(b"7z\xbc\xaf\x27\x1c junk")
    return lib


@pytest.fixture
def make_settings(tmp_path):
    def make(*roots: Path, cache_limit_bytes: int = 2 * 1024 ** 3, sevenzip_path: str = NO_SEVENZIP,
             **limits) -> Settings:
        return Settings(library_roots=tuple(roots), data_dir=tmp_path / "data",
                        cache_limit_bytes=cache_limit_bytes, sevenzip_path=sevenzip_path,
                        limits=ExtractionLimits(**limits))
    return make


@pytest.fixture(scope="session")
def sevenzip() -> str:
    """実機の 7-Zip CLI のパス。見つからなければ 7z 依存のテストを skip する(CI の ubuntu など)。"""
    found = find_sevenzip_for_tests()
    if found is None:
        pytest.skip("7-Zip CLI が見つからない(環境変数 SPINEBOX_TEST_7Z=<パス> か PATH の 7z / 7zz。none で強制 skip)")
    return found


def find_sevenzip_for_tests() -> str | None:
    forced = os.environ.get("SPINEBOX_TEST_7Z")
    if forced == "none":
        return None  # 7z の無い環境(CI)の挙動を手元で確かめる用
    candidates = [forced, "7z", "7zz", "7za",
                  "C:/Program Files/7-Zip/7z.exe", "C:/Program Files (x86)/7-Zip/7z.exe"]
    for c in candidates:
        if c and find_sevenzip(c):
            return find_sevenzip(c)
    return None
