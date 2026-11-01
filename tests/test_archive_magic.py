"""拡張子ではなく先頭のマジックバイトで形式を選ぶ(#49)。

rar / 7z を .zip / .cbz に改名したもの・壊れたもの・先頭が短すぎるもの。7z 依存のテストは
`sevenzip` fixture が見つからなければ skip する(CI の ubuntu など)。
"""
import re

import pytest
from fastapi.testclient import TestClient

from archives7z import make_7z, rar5_bytes
from conftest import NO_SEVENZIP, page_bytes, snapshot, write_zip, zip_bytes
from spinebox import db
from spinebox.app import create_app
from spinebox.archive import ArchiveError, UnsupportedArchive, list_entries, open_archive
from spinebox.archive import base
from spinebox.archive.base import detect_format, file_reader_available
from spinebox.archive.sevenzip import SevenZipReader
from spinebox.archive.zipreader import ZipReader
from spinebox.config import ExtractionLimits
from spinebox.scanner import Scanner

LIMITS = ExtractionLimits()
PAGES = {"Vol/1.jpg": page_bytes("p1"), "Vol/2.jpg": page_bytes("p2")}


def rar_pages() -> bytes:
    return rar5_bytes([("Vol/", None)] + [(name, data) for name, data in PAGES.items()])


def read_all(path, sevenzip, name=None):
    with open_archive(path, name, sevenzip_path=sevenzip) as r:
        return {e.name: r.read_entry(e, LIMITS) for e in list_entries(r, LIMITS).entries}


# --- 判定(7z 不要) -------------------------------------------------------------


@pytest.mark.parametrize("head, fmt", [
    (b"PK\x03\x04rest", "zip"),
    (b"PK\x05\x06" + b"\0" * 18, "zip"),       # 空の zip
    (b"Rar!\x1a\x07\x00rest", "rar"),           # RAR4
    (b"Rar!\x1a\x07\x01\x00rest", "rar"),       # RAR5
    (b"7z\xbc\xaf\x27\x1crest", "7z"),
    (b"MZ\x90\x00 self-extracting", None),      # 自己解凍形式は先頭では分からない
    (b"Rar!\x1a\x07\x02\x00", None),            # 未知の rar 版
    (b"PK", None),                              # 先頭が短すぎる
    (b"", None),
])
def test_detect_format(tmp_path, head, fmt):
    path = tmp_path / "x.cbz"
    path.write_bytes(head)
    assert detect_format(path) == fmt


def test_detect_format_reads_only_the_head(tmp_path, monkeypatch):
    path = tmp_path / "x.cbz"
    path.write_bytes(b"7z\xbc\xaf\x27\x1c" + b"x" * 100_000)
    sizes = []
    real_open = open

    def spy_open(p, mode="r", *a, **kw):
        assert mode == "rb"  # 読み取り専用
        f = real_open(p, mode, *a, **kw)
        real_read = f.read
        f.read = lambda n=-1: sizes.append(n) or real_read(n)
        return f

    monkeypatch.setattr("builtins.open", spy_open)
    assert detect_format(path) == "7z"
    assert sizes == [base.MAGIC_BYTES] and base.MAGIC_BYTES <= 8


def test_detect_format_of_missing_file_is_none(tmp_path):
    assert detect_format(tmp_path / "absent.cbz") is None


def test_zip_renamed_to_rar_is_read_by_zipfile_without_7zip(tmp_path):
    path = tmp_path / "renamed.cbr"
    write_zip(path, [("1.jpg", page_bytes("z"))])
    with open_archive(path, sevenzip_path=NO_SEVENZIP) as r:
        assert isinstance(r, ZipReader)
        assert [e.name for e in list_entries(r, LIMITS).entries] == ["1.jpg"]
    assert file_reader_available(path, NO_SEVENZIP)


@pytest.mark.parametrize("name, head, kind", [
    ("a.cbz", b"Rar!\x1a\x07\x01\x00junk", "rar"),
    ("a.zip", b"Rar!\x1a\x07\x00junk", "rar"),
    ("a.cbz", b"7z\xbc\xaf\x27\x1cjunk", "7z"),
])
def test_renamed_rar_or_7z_without_7zip_names_the_real_format(tmp_path, name, head, kind):
    path = tmp_path / name
    path.write_bytes(head)
    with pytest.raises(UnsupportedArchive, match=re.escape(f"未対応(中身は {kind}")):
        open_archive(path, sevenzip_path=NO_SEVENZIP)
    assert not file_reader_available(path, NO_SEVENZIP)


@pytest.mark.parametrize("data", [b"this is not a zip", b"PK", b""])
def test_broken_or_short_zip_without_7zip_is_an_archive_error(tmp_path, data):
    path = tmp_path / "broken.cbz"
    path.write_bytes(data)
    with pytest.raises(ArchiveError, match="zip として開けない") as err:
        open_archive(path, sevenzip_path=NO_SEVENZIP)
    assert not isinstance(err.value, UnsupportedArchive)


def test_unknown_head_falls_back_to_7zip_only_when_zipfile_fails(tmp_path, monkeypatch):
    # 7-Zip が見つかる設定にして、7-Zip の呼び出しを差し替える
    monkeypatch.setattr("spinebox.archive.sevenzip.find_sevenzip", lambda configured: "7z")
    listed = []
    monkeypatch.setattr(SevenZipReader, "raw_entries", lambda self: listed.append(self) or [])
    good = tmp_path / "prefixed.cbz"
    good.write_bytes(b"MZ stub " + zip_bytes([("1.jpg", page_bytes("z"))]))  # 先頭に別物が付いた zip
    with open_archive(good, sevenzip_path="7z") as r:
        assert isinstance(r, ZipReader)  # zipfile で開ければ 7-Zip は呼ばない
    assert listed == []
    sfx = tmp_path / "sfx.cbz"
    sfx.write_bytes(b"MZ\x90\x00 not a zip")
    with open_archive(sfx, sevenzip_path="7z") as r:
        assert isinstance(r, SevenZipReader) and listed == [r]


def test_unknown_head_that_7zip_cannot_open_either_records_both_reasons(tmp_path, monkeypatch):
    monkeypatch.setattr("spinebox.archive.sevenzip.find_sevenzip", lambda configured: "7z")

    def fail(self):
        raise ArchiveError("7-Zip が失敗した(終了コード 2)")

    monkeypatch.setattr(SevenZipReader, "raw_entries", fail)
    path = tmp_path / "broken.cbz"
    path.write_bytes(b"garbage")
    with pytest.raises(ArchiveError, match="zip として開けない.*7-Zip でも開けない"):
        open_archive(path, sevenzip_path="7z")


# --- 実機の 7z を使う ----------------------------------------------------------


@pytest.mark.parametrize("name", ["renamed.cbz", "renamed.zip"])
def test_rar_renamed_to_zip_is_read_by_7zip(sevenzip, tmp_path, name):
    path = tmp_path / name
    path.write_bytes(rar_pages())
    before = snapshot(tmp_path)
    assert read_all(path, sevenzip) == PAGES
    assert snapshot(tmp_path) == before  # 元ファイルに書き込まない・改名しない・横に展開しない


@pytest.mark.parametrize("name", ["renamed.cbz", "renamed.zip"])
def test_7z_renamed_to_zip_is_read_by_7zip(sevenzip, tmp_path, name):
    path = make_7z(sevenzip, tmp_path / "src.7z", PAGES)
    path = path.rename(tmp_path / name)
    before = snapshot(tmp_path)
    assert read_all(path, sevenzip) == PAGES
    assert snapshot(tmp_path) == before


def test_broken_cbz_with_7zip_is_still_an_archive_error(sevenzip, tmp_path):
    path = tmp_path / "broken.cbz"
    path.write_bytes(b"this is not an archive at all")
    with pytest.raises(ArchiveError, match="7-Zip でも開けない"):
        open_archive(path, sevenzip_path=sevenzip)


def test_scan_and_read_renamed_archives(sevenzip, make_settings, tmp_path):
    lib = tmp_path / "lib"
    lib.mkdir()
    (lib / "rar_inside.cbz").write_bytes(rar_pages())
    make_7z(sevenzip, lib / "x.7z", PAGES).rename(lib / "sevenzip_inside.cbz")
    (lib / "broken.cbz").write_bytes(b"not an archive")
    # 外側の zip の中に、中身が rar の .cbz(内側は取り出した後に判定する)
    write_zip(lib / "outer.zip", [("inner.cbz", rar_pages())])
    before = snapshot(lib)
    c = TestClient(create_app(make_settings(lib, sevenzip_path=sevenzip), frontend_dist=None))
    c.post("/api/scan")
    vols = c.get("/api/volumes").json()
    assert sorted(v["title"] for v in vols) == ["Vol", "Vol", "inner"]  # broken は一覧から外れる
    for v in vols:
        detail = c.get(f"/api/volumes/{v['id']}")
        assert detail.status_code == 200, detail.text
        contents = [c.get(f"/api/volumes/{v['id']}/pages/{p['index']}").content for p in detail.json()["pages"]]
        assert contents == list(PAGES.values()), v["title"]
    assert snapshot(lib) == before  # 元ファイルに書き込まない・改名しない


def test_scan_without_7zip_records_the_real_format(make_settings, tmp_path):
    lib = tmp_path / "lib"
    lib.mkdir()
    (lib / "rar_inside.cbz").write_bytes(rar_pages())
    conn = db.open_db(":memory:")
    Scanner(conn, make_settings(lib)).scan()
    (reason,) = [r["reason"] for r in conn.execute("SELECT reason FROM scan_issues")]
    assert "中身は rar" in reason and "7-Zip が見つからない" in reason
