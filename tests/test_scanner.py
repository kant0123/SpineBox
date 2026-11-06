import zipfile

import pytest

from conftest import RawName, page_bytes, snapshot, write_zip, zip_bytes
from spinebox import db, scanner
from spinebox.archive.zipreader import ZipReader
from spinebox.scanner import Scanner


@pytest.fixture
def conn(make_settings):
    return db.open_db(":memory:")


def scan(conn, settings):
    return Scanner(conn, settings).scan()


def volumes(conn, *, include_missing=False):
    sql = """SELECT v.*, s.rel_path, s.offline FROM volumes v JOIN sources s ON s.id = v.source_id"""
    if not include_missing:
        sql += " WHERE v.missing = 0 AND s.missing = 0"
    return {(r["rel_path"], r["container_path"], r["entry_prefix"]): r for r in conn.execute(sql)}


def issues(conn):
    return [(r["rel_path"], r["entry_name"], r["reason"]) for r in conn.execute(
        "SELECT i.*, s.rel_path FROM scan_issues i JOIN sources s ON s.id = i.source_id")]


def test_volume_candidates(conn, library, make_settings):
    result = scan(conn, make_settings(library))
    vols = volumes(conn)
    assert set(vols) == {
        ("FolderBook", None, ""),
        ("single.cbz", None, ""),
        ("multi.zip", None, "Vol1/"),
        ("multi.zip", None, "Vol2/"),
        ("series/nested.zip", "inner1.zip", ""),
        ("series/nested.zip", "inner2.cbz", ""),
        ("series/nested.zip", "inner3.rar", ""),
    }
    assert result.volumes == 7 and result.offline_roots == []
    assert vols[("FolderBook", None, "")]["page_count"] == 4
    assert vols[("single.cbz", None, "")]["title"] == "single"
    assert vols[("multi.zip", None, "Vol1/")]["title"] == "Vol1"
    assert vols[("multi.zip", None, "Vol1/")]["page_count"] == 2
    inner = vols[("series/nested.zip", "inner1.zip", "")]
    assert inner["page_count"] is None and inner["title"] == "inner1"
    assert inner["name_raw"] == b"inner1.zip" and inner["name_encoding"] == "ascii"
    reasons = {rel: reason for rel, _, reason in issues(conn)}
    assert "未対応" in reasons["series/solid.7z"]
    assert "読めない" in reasons["broken.zip"]


def test_scan_never_extracts_inner_archives(conn, library, make_settings, monkeypatch):
    calls = []
    monkeypatch.setattr(ZipReader, "copy_entry", lambda *a, **k: calls.append(a) or pytest.fail("取り出した"))
    monkeypatch.setattr(zipfile.ZipFile, "open", lambda *a, **k: calls.append(a) or pytest.fail("エントリを開いた"))
    settings = make_settings(library)
    scan(conn, settings)
    assert calls == []
    assert not settings.cache_dir.exists() or not any(settings.cache_dir.iterdir())


def test_unchanged_archive_is_not_reopened(conn, library, make_settings, monkeypatch):
    settings = make_settings(library)
    scan(conn, settings)
    opened = []
    real = scanner.open_archive
    monkeypatch.setattr(scanner, "open_archive", lambda p, *a, **kw: opened.append(p.name) or real(p, *a, **kw))
    scan(conn, settings)
    # 読めなかったもの(壊れた zip・7z)は次のスキャンで再挑戦する。読めたものは開かない
    assert sorted(opened) == ["broken.zip", "solid.7z"]
    write_zip(library / "single.cbz", [("1.jpg", page_bytes("new"))])
    opened.clear()
    scan(conn, settings)
    assert "single.cbz" in opened
    assert volumes(conn)[("single.cbz", None, "")]["page_count"] == 1


def test_unsafe_entry_names_are_skipped_and_recorded(conn, tmp_path, make_settings):
    lib = tmp_path / "lib"
    write_zip(lib / "evil.zip", [
        (RawName(b"../x.jpg"), b"x"), (RawName(b"/abs.jpg"), b"x"), (RawName(b"C:\\x.jpg"), b"x"),
        (RawName(b"../../escape/y.jpg"), b"x"), ("ok/1.jpg", b"x"),
    ])
    before = snapshot(tmp_path)
    scan(conn, make_settings(lib))
    assert set(volumes(conn)) == {("evil.zip", None, "ok/")}
    skipped = sorted(name for _, name, _ in issues(conn))
    assert skipped == ["../../escape/y.jpg", "../x.jpg", "/abs.jpg", "C:\\x.jpg"]
    # 一時ディレクトリの中に増えたのは data/ だけ(エントリ名のパスにファイルは作られない)
    after = {k for k in snapshot(tmp_path) if not k.startswith("data")}
    assert after == set(before)
    assert not (tmp_path / "x.jpg").exists() and not (tmp_path.parent / "escape").exists()


@pytest.mark.parametrize("limits, expect", [
    ({"max_entries": 2}, "エントリ数"),
    ({"max_total_bytes": 5}, "合計"),
])
def test_archive_over_limits_is_skipped_with_reason(conn, tmp_path, make_settings, limits, expect):
    lib = tmp_path / "lib"
    write_zip(lib / "big.zip", [(f"{i}.jpg", b"x" * 10) for i in range(3)])
    write_zip(lib / "small.zip", [("1.jpg", b"x")])
    scan(conn, make_settings(lib, **limits))
    assert set(volumes(conn)) == {("small.zip", None, "")}
    ((rel, _, reason),) = issues(conn)
    assert rel == "big.zip" and expect in reason and "展開上限" in reason


def test_nesting_depth_limit(conn, library, make_settings):
    scan(conn, make_settings(library, max_depth=0))
    assert not [k for k in volumes(conn) if k[1]]
    depth = [name for rel, name, reason in issues(conn) if "入れ子" in reason]
    assert sorted(depth) == ["inner1.zip", "inner2.cbz", "inner3.rar"]


def test_offline_root_keeps_records_and_comes_back(conn, library, make_settings):
    settings = make_settings(library)
    scan(conn, settings)
    vid = volumes(conn)[("single.cbz", None, "")]["id"]
    conn.execute("INSERT INTO reading_progress (volume_id, page, finished, updated_at) VALUES (?, 3, 0, 'x')", (vid,))
    count = len(volumes(conn))

    moved = library.with_name("lib-unplugged")
    library.rename(moved)
    result = scan(conn, settings)
    assert result.offline_roots == [str(library)]
    vols = volumes(conn)
    assert len(vols) == count and all(r["offline"] for r in vols.values())
    assert conn.execute("SELECT page FROM reading_progress WHERE volume_id = ?", (vid,)).fetchone()[0] == 3
    assert conn.execute("SELECT online FROM library_roots").fetchone()[0] == 0

    moved.rename(library)
    result = scan(conn, settings)
    assert result.offline_roots == []
    vols = volumes(conn)
    assert len(vols) == count and not any(r["offline"] for r in vols.values())
    assert vols[("single.cbz", None, "")]["id"] == vid
    assert conn.execute("SELECT page FROM reading_progress WHERE volume_id = ?", (vid,)).fetchone()[0] == 3


def test_vanished_source_is_marked_missing_not_deleted(conn, library, make_settings):
    settings = make_settings(library)
    scan(conn, settings)
    vid = volumes(conn)[("single.cbz", None, "")]["id"]
    conn.execute("INSERT INTO reading_progress (volume_id, page, finished, updated_at) VALUES (?, 1, 1, 'x')", (vid,))
    data = (library / "single.cbz").read_bytes()
    (library / "single.cbz").unlink()
    scan(conn, settings)
    assert ("single.cbz", None, "") not in volumes(conn)
    assert ("single.cbz", None, "") in volumes(conn, include_missing=True)
    assert conn.execute("SELECT COUNT(*) FROM reading_progress").fetchone()[0] == 1
    (library / "single.cbz").write_bytes(data)
    scan(conn, settings)
    assert volumes(conn)[("single.cbz", None, "")]["id"] == vid


def test_volume_removed_from_archive_is_marked_missing(conn, library, make_settings):
    settings = make_settings(library)
    scan(conn, settings)
    write_zip(library / "multi.zip", [("Vol1/001.jpg", page_bytes("v1"))])
    scan(conn, settings)
    assert ("multi.zip", None, "Vol2/") not in volumes(conn)
    assert volumes(conn, include_missing=True)[("multi.zip", None, "Vol2/")]["missing"] == 1


def test_root_removed_from_config_is_kept_but_hidden(conn, library, tmp_path, make_settings):
    scan(conn, make_settings(library))
    other = tmp_path / "other"
    other.mkdir()
    result = scan(conn, make_settings(other))
    assert result.volumes == 0
    assert conn.execute("SELECT COUNT(*) FROM volumes").fetchone()[0] == 7
    assert scan(conn, make_settings(library, other)).volumes == 7


def test_resolve_source_path_rejects_escape(tmp_path):
    assert scanner.resolve_source_path(tmp_path, "a/b.zip") == tmp_path / "a" / "b.zip"
    assert scanner.resolve_source_path(tmp_path, "") == tmp_path
    with pytest.raises(ValueError):
        scanner.resolve_source_path(tmp_path, "../x.zip")


def test_zip_containing_epub_container_is_scanned_as_novel(conn, tmp_path, make_settings):
    from novels import epub_bytes

    lib = tmp_path / "lib"
    lib.mkdir()
    (lib / "book.zip").write_bytes(epub_bytes(["<p>hello</p>"]))
    scan(conn, make_settings(lib))
    vols = volumes(conn)
    assert ("book.zip", None, "") in vols
    assert vols[("book.zip", None, "")]["format"] == "epub"


def test_non_content_dirs_are_excluded_from_archive_volumes(conn, tmp_path, make_settings):
    lib = tmp_path / "lib"
    inner = zip_bytes([("a.jpg", page_bytes("inner/a"))])
    write_zip(lib / "pack.zip", [
        ("__MACOSX/ghost.jpg", page_bytes("mac")),
        ("META-INF/cover.jpg", page_bytes("meta")),
        (".DS_Store/x.jpg", page_bytes("ds")),
        ("__MACOSX/inner.zip", inner),
        ("Vol1/001.jpg", page_bytes("v1/001")),
        ("Vol1/002.jpg", page_bytes("v1/002")),
    ])
    scan(conn, make_settings(lib))
    vols = volumes(conn)
    assert set(vols) == {("pack.zip", None, "Vol1/")}
    # 非コンテンツディレクトリ内の内側アーカイブも巻にならない
    assert not any(v[1] for v in vols)


def test_cover_only_folder_in_archive_is_skipped(conn, tmp_path, make_settings):
    lib = tmp_path / "lib"
    write_zip(lib / "pack.zip", [
        ("CoverOnly/cover.jpg", page_bytes("cover")),
        ("CoverOnly/front_cover.jpg", page_bytes("cover2")),
        ("RealVol/001.jpg", page_bytes("real/001")),
    ])
    scan(conn, make_settings(lib))
    vols = volumes(conn)
    assert set(vols) == {("pack.zip", None, "RealVol/")}


def test_cover_only_filesystem_folder_is_skipped(conn, tmp_path, make_settings):
    lib = tmp_path / "lib"
    folder = lib / "CoverOnlyFolder"
    folder.mkdir(parents=True)
    (folder / "cover.jpg").write_bytes(page_bytes("cover"))
    (folder / "front_cover.jpg").write_bytes(page_bytes("cover2"))
    # 混在フォルダは巻になる
    mixed = lib / "MixedFolder"
    mixed.mkdir(parents=True)
    (mixed / "cover.jpg").write_bytes(page_bytes("cover"))
    (mixed / "001.jpg").write_bytes(page_bytes("page"))
    scan(conn, make_settings(lib))
    vols = volumes(conn)
    assert ("CoverOnlyFolder", None, "") not in vols
    assert ("MixedFolder", None, "") in vols
