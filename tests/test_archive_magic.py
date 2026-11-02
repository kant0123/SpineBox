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


# --- 内側アーカイブの取り出す前の判定(#62。7z 不要) ------------------------------


def inner_pages(prefix: str) -> dict[str, bytes]:
    return {f"{prefix}/1.jpg": page_bytes(f"{prefix}1"), f"{prefix}/2.jpg": page_bytes(f"{prefix}2")}


@pytest.fixture
def renamed_inner_lib(tmp_path):
    """7-Zip なしで: 名前は rar / 7z で中身が zip の内側と、名前は cbz / rar で中身が rar の内側。"""
    lib = tmp_path / "lib"
    lib.mkdir()
    write_zip(lib / "outer.zip", [
        ("zip_as.rar", zip_bytes(list(inner_pages("A").items()))),
        ("zip_as.7z", zip_bytes(list(inner_pages("B").items()))),
        ("rar_as.cbz", rar_pages()),
        ("rar_as.rar", rar_pages()),
    ])
    return lib


def test_zip_inner_named_rar_is_read_without_7zip(make_settings, renamed_inner_lib):
    settings = make_settings(renamed_inner_lib)
    before = snapshot(renamed_inner_lib)
    c = TestClient(create_app(settings, frontend_dist=None))
    c.post("/api/scan")
    readable = [v for v in c.get("/api/volumes").json() if v["title"] == "zip_as"]
    assert len(readable) == 2
    got = []
    for v in readable:
        detail = c.get(f"/api/volumes/{v['id']}")
        assert detail.status_code == 200, detail.text
        got.append([c.get(f"/api/volumes/{v['id']}/pages/{p['index']}").content for p in detail.json()["pages"]])
    assert sorted(got) == sorted([list(inner_pages("A").values()), list(inner_pages("B").values())])
    assert snapshot(renamed_inner_lib) == before  # 元ファイルに書き込まない


def test_rar_inner_without_7zip_names_the_real_format_before_extracting(make_settings, renamed_inner_lib):
    settings = make_settings(renamed_inner_lib)
    c = TestClient(create_app(settings, frontend_dist=None))
    c.post("/api/scan")
    rar_vols = [v for v in c.get("/api/volumes").json() if v["title"] == "rar_as"]
    assert len(rar_vols) == 2
    for v in rar_vols:
        r = c.get(f"/api/volumes/{v['id']}")
        assert r.status_code == 422
        assert "中身は rar" in r.json()["detail"] and "7-Zip が見つからない" in r.json()["detail"]
    assert list(settings.cache_dir.iterdir()) == []  # 中身が読めないものは取り出さない


def test_analysis_records_the_real_format_of_inner_archives(make_settings, renamed_inner_lib):
    from spinebox.cache import ExtractionCache
    from spinebox.library import Library

    settings = make_settings(renamed_inner_lib)
    conn = db.open_db(settings.db_path)
    Scanner(conn, settings).scan()
    lib = Library(settings, ExtractionCache(settings.cache_dir, settings.cache_limit_bytes))
    while lib.analyze_next(conn) is not None:
        pass
    rows = conn.execute("SELECT container_path, analyzed_version, analysis_error FROM volumes "
                        "WHERE parent_id IS NULL ORDER BY container_path").fetchall()
    by_name = {r["container_path"]: r for r in rows}
    for name in ("zip_as.rar", "zip_as.7z"):
        assert by_name[name]["analysis_error"] is None and by_name[name]["analyzed_version"] != "error"
    for name in ("rar_as.cbz", "rar_as.rar"):
        assert by_name[name]["analyzed_version"] == "error"
        assert "中身は rar" in by_name[name]["analysis_error"]
    conn.close()


def test_organize_plan_judges_inner_archives_by_content(make_settings, renamed_inner_lib):
    from spinebox.cache import ExtractionCache
    from spinebox.library import Library
    from spinebox.organize.organizer import Organizer

    settings = make_settings(renamed_inner_lib)
    conn = db.open_db(settings.db_path)
    Scanner(conn, settings).scan()
    lib = Library(settings, ExtractionCache(settings.cache_dir, settings.cache_limit_bytes))
    while lib.analyze_next(conn) is not None:  # 整理の前提(中の巻を調べる)
        pass
    vid = conn.execute("SELECT id FROM volumes WHERE container_path = 'zip_as.rar'").fetchone()["id"]
    plan = Organizer(conn, settings, lib).plan(volume_id=vid)
    # 同じソースの rar_as.* は読めないので計画は止まるが、zip_as.* は問題に挙がらない
    assert plan["state"] == "blocked"
    assert not any("zip_as" in p for p in plan["problems"]), plan["problems"]
    for name in ("rar_as.cbz", "rar_as.rar"):
        assert f"内側アーカイブを読めません: {name}: 未対応(中身は rar" in "\n".join(plan["problems"])
    conn.close()


def test_organize_inspect_reports_a_missing_inner_archive(make_settings, renamed_inner_lib):
    from spinebox.cache import ExtractionCache
    from spinebox.library import Library
    from spinebox.organize.organizer import Organizer

    settings = make_settings(renamed_inner_lib)
    conn = db.open_db(settings.db_path)
    o = Organizer(conn, settings, Library(settings, ExtractionCache(settings.cache_dir, settings.cache_limit_bytes)))
    problems, warnings = [], []
    vols = [{"container_path": "zip_as.rar", "entry_prefix": ""}, {"container_path": "gone.zip", "entry_prefix": ""}]
    o._inspect_archive(renamed_inner_lib / "outer.zip", vols, problems, warnings)
    assert problems == ["内側アーカイブを読めません: gone.zip: アーカイブの中に見つからない"]
    conn.close()


def test_organize_plan_of_renamed_zip_inner_is_not_blocked(make_settings, tmp_path):
    from spinebox.cache import ExtractionCache
    from spinebox.library import Library
    from spinebox.organize.organizer import Organizer

    lib_root = tmp_path / "lib"
    lib_root.mkdir()
    write_zip(lib_root / "outer.zip", [("zip_as.rar", zip_bytes(list(inner_pages("A").items())))])
    settings = make_settings(lib_root)
    conn = db.open_db(settings.db_path)
    Scanner(conn, settings).scan()
    lib = Library(settings, ExtractionCache(settings.cache_dir, settings.cache_limit_bytes))
    while lib.analyze_next(conn) is not None:  # 整理の前提(中の巻を調べる)
        pass
    vid = conn.execute("SELECT id FROM volumes WHERE container_path = 'zip_as.rar'").fetchone()["id"]
    plan = Organizer(conn, settings, lib).plan(volume_id=vid)
    assert plan["state"] == "planned" and plan["problems"] == [], plan["problems"]
    conn.close()


class _NoHeadReader(base.ArchiveReader):
    """先頭を安く読めない親(7-Zip CLI 相当)。"""

    def raw_entries(self):
        return []

    def copy_entry(self, entry, dest, max_bytes):
        raise AssertionError("判定で取り出さない")

    def close(self):
        pass


def _entry(name: str) -> base.ArchiveEntry:
    return base.ArchiveEntry(0, name, name.encode(), "ascii", 10, False)


@pytest.mark.parametrize("name, ok", [("a.zip", True), ("a.cbz", True), ("a.rar", False), ("a.7z", False),
                                      ("a.txt", False)])
def test_inner_reader_problem_falls_back_to_the_name_when_head_is_unknown(name, ok):
    problem = base.inner_reader_problem(_NoHeadReader(), _entry(name), NO_SEVENZIP)
    assert (problem is None) == ok
    if name.endswith((".rar", ".7z")):
        assert "7-Zip が見つからない" in problem


def test_inner_reader_problem_reads_only_the_head_of_a_zip_entry(tmp_path, monkeypatch):
    path = tmp_path / "outer.zip"
    write_zip(path, [("zip_as.rar", zip_bytes([("1.jpg", page_bytes("z"))])),
                     ("rar_as.cbz", rar_pages()), ("unknown.rar", b"x" * 100)])
    monkeypatch.setattr(ZipReader, "copy_entry", lambda *a: pytest.fail("判定で取り出さない"))
    with open_archive(path, sevenzip_path=NO_SEVENZIP) as r:
        entries = {e.name: e for e in list_entries(r, LIMITS).entries}
        assert base.inner_reader_problem(r, entries["zip_as.rar"], NO_SEVENZIP) is None
        assert "中身は rar。拡張子は .cbz" in base.inner_reader_problem(r, entries["rar_as.cbz"], NO_SEVENZIP)
        # 先頭で分からなければ名前で(取り出した後の open_archive と同じ)
        assert "拡張子は .rar" in base.inner_reader_problem(r, entries["unknown.rar"], NO_SEVENZIP)


def test_inner_reader_problem_allows_rar_content_when_7zip_is_found(tmp_path, monkeypatch):
    monkeypatch.setattr("spinebox.archive.sevenzip.find_sevenzip", lambda configured: "7z")
    path = tmp_path / "outer.zip"
    write_zip(path, [("rar_as.cbz", rar_pages())])
    with open_archive(path, sevenzip_path="7z") as r:
        (entry,) = list_entries(r, LIMITS).entries
        assert base.inner_reader_problem(r, entry, "7z") is None
