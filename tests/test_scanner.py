import os
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
    first = scan(conn, settings)
    assert first.changed is True
    opened = []
    real = scanner.open_archive
    monkeypatch.setattr(scanner, "open_archive", lambda p, *a, **kw: opened.append(p.name) or real(p, *a, **kw))
    second = scan(conn, settings)
    # 7-Zip 未設定(UnsupportedArchive)は次のスキャンで再挑戦する。読めたものと決定的な破損(broken.zip)は開かない
    assert sorted(opened) == ["solid.7z"]
    assert second.changed is False
    assert any(i["path"].endswith("/broken.zip") for i in second.issues)
    # recheck=True(全ファイルを確かめ直す)なら破損アーカイブも開き直す
    opened.clear()
    rechecked = Scanner(conn, settings, recheck=True).scan()
    assert "broken.zip" in opened and "single.cbz" in opened and rechecked.changed is True
    write_zip(library / "single.cbz", [("1.jpg", page_bytes("new"))])
    opened.clear()
    third = scan(conn, settings)
    assert "single.cbz" in opened and third.changed is True
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


def test_root_removed_from_config_stays_in_the_library(conn, library, tmp_path, make_settings):
    """蔵書フォルダは DB が正(#313): 設定は DB に無いパスを足すだけで、設定から消してもフォルダは外れない。"""
    scan(conn, make_settings(library))
    other = tmp_path / "other"
    other.mkdir()
    assert scan(conn, make_settings(other)).volumes == 7
    enabled = [r["path"] for r in conn.execute("SELECT path FROM library_roots WHERE enabled = 1 ORDER BY id")]
    assert enabled == [str(library), str(other)]
    assert scan(conn, make_settings()).volumes == 7


def test_root_removed_on_screen_is_hidden_and_not_revived_by_config(conn, library, make_settings):
    """画面で外したフォルダは、設定に残っていても戻さない。行・巻は消さない。"""
    from spinebox import libraries
    settings = make_settings(library)
    scan(conn, settings)
    root_id = conn.execute("SELECT id FROM library_roots").fetchone()[0]
    libraries.remove_folder(conn, root_id)
    assert scan(conn, settings).volumes == 0
    assert conn.execute("SELECT COUNT(*) FROM volumes").fetchone()[0] == 7
    assert conn.execute("SELECT enabled, group_id FROM library_roots").fetchone()[:] == (0, None)


def test_resolve_source_path_rejects_escape(tmp_path):
    assert scanner.resolve_source_path(tmp_path, "a/b.zip") == tmp_path / "a" / "b.zip"
    assert scanner.resolve_source_path(tmp_path, "") == tmp_path
    with pytest.raises(ValueError):
        scanner.resolve_source_path(tmp_path, "../x.zip")


def test_zip_containing_epub_container_is_scanned_as_novel(conn, tmp_path, make_settings):
    from novels import PNG, epub_bytes

    lib = tmp_path / "lib"
    lib.mkdir()
    (lib / "book.zip").write_bytes(epub_bytes(["<p>hello</p>"]))
    (lib / "manga.zip").write_bytes(epub_bytes(
        ['<div style="display:none;">.</div><img src="../images/p1.png"/>'],
        images={"images/p1.png": PNG}))
    scan(conn, make_settings(lib))
    vols = volumes(conn)
    assert ("book.zip", None, "") in vols
    assert vols[("book.zip", None, "")]["format"] == "epub"
    assert ("manga.zip", None, "") in vols
    assert vols[("manga.zip", None, "")]["format"] == "epub_images"
    assert vols[("manga.zip", None, "")]["page_count"] == 1


def test_non_content_dirs_are_excluded_from_archive_volumes(conn, tmp_path, make_settings):
    lib = tmp_path / "lib"
    inner = zip_bytes([("a.jpg", page_bytes("inner/a"))])
    write_zip(lib / "pack.zip", [
        ("__MACOSX/ghost.jpg", page_bytes("mac")),
        ("META-INF/cover.jpg", page_bytes("meta")),
        ("OEBPS/page.jpg", page_bytes("oebps")),
        ("ops/page.jpg", page_bytes("ops")),
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


def test_chapter_folders_inside_a_volume_folder_do_not_get_volume_numbers(conn, tmp_path, make_settings):
    """巻のフォルダ(v06s)の中の `110-113`・`Raw Chapter 171` は章の番号であって巻番号ではない(#248)。"""
    lib = tmp_path / "lib"
    write_zip(lib / "作品 第01-20巻.zip", [
        ("DLRAW.AC_Sakuhin v06s/DLRAW.AC_110-113/001.jpg", page_bytes("a")),
        ("DLRAW.AC_Sakuhin v06s/Raw Chapter 171/001.jpg", page_bytes("b")),
        ("DLRAW.AC_Sakuhin v07s/001.jpg", page_bytes("c")),
        ("01-04/001.jpg", page_bytes("d")),
    ])
    scan(conn, make_settings(lib))
    numbers = {k[2]: (v["number_kind"], v["number_start"], v["number_end"]) for k, v in volumes(conn).items()}
    assert numbers["DLRAW.AC_Sakuhin v06s/DLRAW.AC_110-113/"] == (None, None, None)
    assert numbers["DLRAW.AC_Sakuhin v06s/Raw Chapter 171/"] == (None, None, None)
    assert numbers["DLRAW.AC_Sakuhin v07s/"] == ("volume", 7, 7)
    assert numbers["01-04/"] == ("volume", 1, 4)  # 外側が無ければ従来どおり


def _image_folder_lib(tmp_path):
    lib = tmp_path / "lib"
    folder = lib / "Title 01"
    folder.mkdir(parents=True)
    for i in range(3):
        (folder / f"{i:03d}.jpg").write_bytes(page_bytes(f"p{i}"))
    return lib, folder


def test_unchanged_image_folder_is_not_stat_per_image_nor_rewritten(conn, tmp_path, make_settings, monkeypatch):
    lib, folder = _image_folder_lib(tmp_path)
    settings = make_settings(lib)
    scan(conn, settings)
    before = conn.execute("SELECT scanned_at FROM sources WHERE rel_path = 'Title 01'").fetchone()[0]
    stated = []
    real = os.stat

    def counting_stat(p, *a, **kw):
        if str(p).lower().endswith(".jpg"):
            stated.append(str(p))
        return real(p, *a, **kw)

    monkeypatch.setattr(os, "stat", counting_stat)
    writes = []
    conn.set_trace_callback(lambda sql: writes.append(sql) if sql.lstrip().upper().startswith(("UPDATE", "INSERT")) else None)
    scan(conn, settings)
    conn.set_trace_callback(None)
    assert stated == []  # 画像 1 枚ごとの stat をしない
    assert not [w for w in writes if "volumes" in w or "SET size" in w]  # 変化が無ければ巻・ソースを書かない
    assert conn.execute("SELECT scanned_at FROM sources WHERE rel_path = 'Title 01'").fetchone()[0] == before


def test_image_folder_changes_are_still_detected(conn, tmp_path, make_settings):
    lib, folder = _image_folder_lib(tmp_path)
    settings = make_settings(lib)
    scan(conn, settings)
    assert volumes(conn)[("Title 01", None, "")]["page_count"] == 3
    (folder / "003.jpg").write_bytes(page_bytes("added"))  # 追加
    scan(conn, settings)
    assert volumes(conn)[("Title 01", None, "")]["page_count"] == 4
    (folder / "003.jpg").unlink()  # 削除
    scan(conn, settings)
    assert volumes(conn)[("Title 01", None, "")]["page_count"] == 3
    st = (folder / "000.jpg").stat()
    (folder / "000.jpg").write_bytes(page_bytes("replaced with a longer body" * 10))  # 差し替え(サイズが変わる)
    os.utime(folder, ns=(st.st_atime_ns, st.st_mtime_ns))  # フォルダの mtime は動かさない
    before = conn.execute("SELECT size FROM sources WHERE rel_path = 'Title 01'").fetchone()[0]
    scan(conn, settings)
    assert conn.execute("SELECT size FROM sources WHERE rel_path = 'Title 01'").fetchone()[0] != before


def test_recheck_rewrites_unchanged_image_folder(conn, tmp_path, make_settings):
    lib, _ = _image_folder_lib(tmp_path)
    settings = make_settings(lib)
    scan(conn, settings)
    conn.execute("UPDATE volumes SET title = '手で書き換えた'")  # 食い違いを仕込む(同じサイズ・mtime・枚数のまま)
    scan(conn, settings)
    assert volumes(conn)[("Title 01", None, "")]["title"] == "手で書き換えた"  # 通常は読み飛ばす
    Scanner(conn, settings, recheck=True).scan()
    assert volumes(conn)[("Title 01", None, "")]["title"] == "Title 01"


def test_folder_scan_uses_parent_ancestors_and_protects_manual_number(conn, tmp_path, make_settings):
    lib = tmp_path / "lib"
    v1 = lib / "[著者] 作品名 第01巻s" / "01"
    v1.mkdir(parents=True)
    (v1 / "000.jpg").write_bytes(page_bytes("v1"))
    v_range = lib / "作品名 01-06" / "01"
    v_range.mkdir(parents=True)
    (v_range / "000.jpg").write_bytes(page_bytes("vr"))

    settings = make_settings(lib)
    scan(conn, settings)
    vols = volumes(conn)
    row1 = vols[("[著者] 作品名 第01巻s/01", None, "")]
    assert (row1["number_kind"], row1["number_start"], row1["number_end"]) == ("volume", 1, 1)
    assert row1["number_manual"] == 0
    row_range = vols[("作品名 01-06/01", None, "")]
    assert row_range["number_kind"] is None

    # 手動指定された巻番号は recheck 再スキャンでも上書きされない
    conn.execute(
        "UPDATE volumes SET number_kind = 'volume', number_start = 9, number_end = 9, "
        "number_evidence = '手動指定', number_manual = 1 WHERE id = ?",
        (row_range["id"],),
    )
    Scanner(conn, settings, recheck=True).scan()
    after = conn.execute("SELECT number_kind, number_start, number_end, number_manual FROM volumes WHERE id = ?",
                         (row_range["id"],)).fetchone()
    assert tuple(after) == ("volume", 9, 9, 1)


def _make_pdf(path, *, num_pages: int = 2, encrypted: bool = False):
    import pypdf

    path.parent.mkdir(parents=True, exist_ok=True)
    writer = pypdf.PdfWriter()
    for _ in range(num_pages):
        writer.add_blank_page(width=100, height=100)
    if encrypted:
        writer.encrypt("secret")
    with open(path, "wb") as f:
        writer.write(f)
    return path


def test_unchanged_sources_all_kinds_do_zero_reopens_stats_writes_and_progress(conn, tmp_path, make_settings, monkeypatch):
    """未変更のアーカイブ・EPUB・zip包みEPUB・TXT・PDF・画像フォルダ・表紙のみフォルダは
    開き直し 0・ファイル単位 stat 0・sources/volumes 書込 0・on_source 0 になる。"""
    from novels import PNG, epub_bytes
    from spinebox.organize import pdf as pdf_mod

    lib = tmp_path / "lib"
    write_zip(lib / "comic.cbz", [("001.jpg", page_bytes("c1"))])
    (lib / "novel.epub").write_bytes(epub_bytes(["<p>本文</p>"]))
    (lib / "fixed.epub").write_bytes(epub_bytes(
        ['<div style="display:none;">.</div><img src="../images/p1.png"/>'],
        images={"images/p1.png": PNG}))
    (lib / "wrapped.zip").write_bytes(epub_bytes(["<p>zip包み</p>"]))
    (lib / "story.txt").write_text("昔々あるところに", encoding="utf-8")
    _make_pdf(lib / "doc.pdf", num_pages=2)
    folder = lib / "FolderVol"
    folder.mkdir()
    (folder / "001.jpg").write_bytes(page_bytes("f1"))
    cover_only = lib / "CoverOnly"
    cover_only.mkdir()
    (cover_only / "cover.jpg").write_bytes(page_bytes("cv"))

    settings = make_settings(lib)
    first_seen: list[str] = []
    first = Scanner(conn, settings, on_source=first_seen.append).scan()
    assert first.changed is True
    # .zip に包まれた EPUB も on_source は 1 回だけ
    assert first_seen.count("wrapped.zip") == 1
    assert set(first_seen) == {
        "comic.cbz", "novel.epub", "fixed.epub", "wrapped.zip", "story.txt", "doc.pdf", "FolderVol", "CoverOnly",
    }

    # 2 回目: 変化なし
    opened: list[str] = []
    inspected: list[str] = []
    stated: list[str] = []
    writes: list[str] = []
    seen: list[str] = []
    real_open = scanner.open_archive
    real_pdf = pdf_mod.inspect_pdf
    real_stat = os.stat
    file_exts = (".cbz", ".zip", ".epub", ".txt", ".pdf", ".jpg")
    monkeypatch.setattr(scanner, "open_archive", lambda p, *a, **kw: opened.append(p.name) or real_open(p, *a, **kw))
    monkeypatch.setattr(pdf_mod, "inspect_pdf", lambda p, *a, **kw: inspected.append(p.name) or real_pdf(p, *a, **kw))
    monkeypatch.setattr(
        os, "stat",
        lambda p, *a, **kw: (stated.append(str(p)) if str(p).lower().endswith(file_exts) else None) or real_stat(p, *a, **kw),
    )
    conn.set_trace_callback(
        lambda sql: writes.append(sql) if sql.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")) else None
    )
    second = Scanner(conn, settings, on_source=seen.append).scan()
    conn.set_trace_callback(None)

    assert second.changed is False
    assert opened == []
    assert inspected == []
    assert stated == []
    assert seen == []
    assert not [w for w in writes if "sources" in w or "volumes" in w or "scan_issues" in w]

    # recheck=True で zip 包み EPUB を読み直しても on_source は 1 回だけ
    recheck_seen: list[str] = []
    Scanner(conn, settings, recheck=True, on_source=recheck_seen.append).scan()
    assert recheck_seen.count("wrapped.zip") == 1


def test_unchanged_scan_sql_count_is_independent_of_source_count(tmp_path, make_settings):
    """未変更スキャンの SQL 発行回数がソース件数に比例しない(ルートあたり O(1))。"""
    from novels import epub_bytes

    def measure(n: int) -> int:
        lib = tmp_path / f"lib_{n}"
        for i in range(n):
            write_zip(lib / f"a{i}.cbz", [("1.jpg", page_bytes(f"a{i}"))])
            (lib / f"e{i}.epub").write_bytes(epub_bytes([f"<p>{i}</p>"]))
            (lib / f"t{i}.txt").write_text(f"text {i}", encoding="utf-8")
            _make_pdf(lib / f"p{i}.pdf", num_pages=1)
            fdir = lib / f"f{i}"
            fdir.mkdir()
            (fdir / "1.jpg").write_bytes(page_bytes(f"f{i}"))
        c = db.open_db(":memory:")
        settings = make_settings(lib)
        Scanner(c, settings).scan()
        sqls: list[str] = []
        c.set_trace_callback(sqls.append)
        res = Scanner(c, settings).scan()
        c.set_trace_callback(None)
        c.close()
        assert res.changed is False
        assert not [s for s in sqls if s.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))
                    and ("sources" in s or "volumes" in s)]
        return len(sqls)

    assert measure(1) == measure(5)


def test_new_and_modified_sources_notify_and_open_only_changed_files(conn, tmp_path, make_settings, monkeypatch):
    lib = tmp_path / "lib"
    write_zip(lib / "stay.cbz", [("1.jpg", page_bytes("s"))])
    write_zip(lib / "edit.cbz", [("1.jpg", page_bytes("e1"))])
    settings = make_settings(lib)
    scan(conn, settings)

    write_zip(lib / "edit.cbz", [("1.jpg", page_bytes("e1")), ("2.jpg", page_bytes("e2"))])
    write_zip(lib / "added.cbz", [("1.jpg", page_bytes("new"))])
    opened: list[str] = []
    seen: list[str] = []
    real = scanner.open_archive
    monkeypatch.setattr(scanner, "open_archive", lambda p, *a, **kw: opened.append(p.name) or real(p, *a, **kw))
    res = Scanner(conn, settings, on_source=seen.append).scan()
    assert res.changed is True
    assert sorted(opened) == ["added.cbz", "edit.cbz"]
    assert sorted(seen) == ["added.cbz", "edit.cbz"]


def test_offline_and_missing_reconnection_does_not_reopen_and_avoids_noop_writes(conn, tmp_path, make_settings, monkeypatch):
    from novels import epub_bytes
    from spinebox.organize import pdf as pdf_mod

    lib = tmp_path / "lib"
    write_zip(lib / "book.cbz", [("1.jpg", page_bytes("b"))])
    (lib / "novel.epub").write_bytes(epub_bytes(["<p>ok</p>"]))
    (lib / "story.txt").write_text("hi", encoding="utf-8")
    _make_pdf(lib / "doc.pdf", num_pages=2)
    fdir = lib / "Folder"
    fdir.mkdir()
    (fdir / "1.jpg").write_bytes(page_bytes("f"))
    cover_dir = lib / "CoverOnly"
    cover_dir.mkdir()
    (cover_dir / "cover.jpg").write_bytes(page_bytes("c"))

    settings = make_settings(lib)
    scan(conn, settings)
    cbz_vid = volumes(conn)[("book.cbz", None, "")]["id"]
    folder_vid = volumes(conn)[("Folder", None, "")]["id"]
    conn.execute("INSERT INTO reading_progress (volume_id, page, finished, updated_at) VALUES (?, 2, 0, 'x')", (cbz_vid,))

    # 1. ルートがオフラインになる(1 回目は changed=True)
    moved = tmp_path / "lib-unplugged"
    lib.rename(moved)
    off1 = scan(conn, settings)
    assert off1.changed is True and off1.offline_roots == [str(lib)]
    assert all(r["offline"] == 1 for r in conn.execute("SELECT offline FROM sources"))

    # 2. オフラインのまま再スキャン(2 回目は changed=False、sources への変更行数 0)
    changes_before = conn.total_changes
    off2 = scan(conn, settings)
    assert off2.changed is False
    # library_roots の 1 行更新のみ(sources 6 行は更新されない)
    assert conn.total_changes - changes_before == 1

    # 3. 再接続: 開き直し 0・on_source 0 で offline=0 に復帰し changed=True
    moved.rename(lib)
    opened: list[str] = []
    inspected: list[str] = []
    seen: list[str] = []
    monkeypatch.setattr(scanner, "open_archive", lambda p, *a, **kw: opened.append(p.name))
    monkeypatch.setattr(pdf_mod, "inspect_pdf", lambda p, *a, **kw: inspected.append(p.name))
    back = Scanner(conn, settings, on_source=seen.append).scan()
    assert back.changed is True and back.offline_roots == []
    assert opened == [] and inspected == [] and seen == []
    assert all(r["offline"] == 0 and r["missing"] == 0 for r in conn.execute("SELECT offline, missing FROM sources"))
    assert volumes(conn)[("book.cbz", None, "")]["id"] == cbz_vid
    assert volumes(conn)[("Folder", None, "")]["id"] == folder_vid
    assert conn.execute("SELECT page FROM reading_progress WHERE volume_id = ?", (cbz_vid,)).fetchone()[0] == 2

    # 4. ソースの一時消失 -> すでに missing のままの再スキャン -> 同一 mtime/size で復帰
    cbz_path = lib / "book.cbz"
    saved_bytes = cbz_path.read_bytes()
    saved_stat = cbz_path.stat()
    cbz_path.unlink()
    miss1 = scan(conn, settings)
    assert miss1.changed is True
    assert conn.execute("SELECT missing FROM sources WHERE rel_path = 'book.cbz'").fetchone()[0] == 1

    writes: list[str] = []
    conn.set_trace_callback(lambda sql: writes.append(sql) if "sources" in sql and "UPDATE" in sql else None)
    miss2 = scan(conn, settings)
    conn.set_trace_callback(None)
    assert miss2.changed is False and writes == []  # 既に missing のソースは再 UPDATE しない

    cbz_path.write_bytes(saved_bytes)
    os.utime(cbz_path, ns=(saved_stat.st_atime_ns, saved_stat.st_mtime_ns))
    restored = Scanner(conn, settings, on_source=seen.append).scan()
    assert restored.changed is True and opened == [] and seen == []
    assert conn.execute("SELECT missing FROM sources WHERE rel_path = 'book.cbz'").fetchone()[0] == 0
    assert volumes(conn)[("book.cbz", None, "")]["id"] == cbz_vid

    # 5. 既に missing=1 のソースがある状態でルートが offline -> online になったとき:
    #    offline=1 -> 0 の遷移で changed=True になり、次のスキャンは changed=False / sources UPDATE 0
    cbz_path.unlink()
    assert scan(conn, settings).changed is True
    assert conn.execute("SELECT missing, offline FROM sources WHERE rel_path = 'book.cbz'").fetchone()[:] == (1, 0)
    lib.rename(moved)
    assert scan(conn, settings).changed is True
    assert conn.execute("SELECT missing, offline FROM sources WHERE rel_path = 'book.cbz'").fetchone()[:] == (1, 1)
    moved.rename(lib)
    cleared = scan(conn, settings)
    assert cleared.changed is True
    assert conn.execute("SELECT missing, offline FROM sources WHERE rel_path = 'book.cbz'").fetchone()[:] == (1, 0)
    writes.clear()
    conn.set_trace_callback(lambda sql: writes.append(sql) if "sources" in sql and "UPDATE" in sql else None)
    steady = scan(conn, settings)
    conn.set_trace_callback(None)
    assert steady.changed is False and writes == []


def test_deterministic_failures_are_cached_preserving_volumes_and_retried_on_recheck_or_change(
    conn, tmp_path, make_settings, monkeypatch
):
    """決定的な失敗(破損アーカイブ・LimitExceeded・破損/上限超え EPUB・暗号化/0ページ PDF)は
    既存巻を残したまま stamp を記録し、通常スキャンで開き直さない。recheck / ファイル変更で再検査する。"""
    from novels import epub_bytes
    from spinebox.organize import pdf as pdf_mod

    lib = tmp_path / "lib"
    write_zip(lib / "was_good.cbz", [("1.jpg", page_bytes("a")), ("2.jpg", page_bytes("b"))])
    (lib / "was_good.epub").write_bytes(epub_bytes(["<p>ok</p>"]))  # 4 エントリ
    _make_pdf(lib / "encrypted.pdf", num_pages=1, encrypted=True)
    _make_pdf(lib / "zero.pdf", num_pages=0)
    write_zip(lib / "too_many.cbz", [(f"{i}.jpg", page_bytes(str(i))) for i in range(6)])
    (lib / "big.epub").write_bytes(epub_bytes(["<p>1</p>", "<p>2</p>", "<p>3</p>"]))  # 6 エントリ
    settings = make_settings(lib, max_entries=5)
    scan(conn, settings)

    # 既存巻に読書位置を付けておく
    cbz_vid = volumes(conn)[("was_good.cbz", None, "")]["id"]
    epub_vid = volumes(conn)[("was_good.epub", None, "")]["id"]
    conn.execute("INSERT INTO reading_progress (volume_id, page, finished, updated_at) VALUES (?, 1, 0, 'x')", (cbz_vid,))
    conn.execute("INSERT INTO reading_progress (volume_id, page, finished, updated_at) VALUES (?, 0, 1, 'x')", (epub_vid,))

    # 正常だった cbz / epub を破損させる
    (lib / "was_good.cbz").write_bytes(b"corrupt zip")
    (lib / "was_good.epub").write_bytes(b"corrupt epub")
    fail_scan = scan(conn, settings)
    assert fail_scan.changed is True
    # 既存の巻・読書位置は保持される(PR #219 不変条件)
    assert volumes(conn)[("was_good.cbz", None, "")]["id"] == cbz_vid
    assert volumes(conn)[("was_good.epub", None, "")]["id"] == epub_vid
    assert conn.execute("SELECT page FROM reading_progress WHERE volume_id = ?", (cbz_vid,)).fetchone()[0] == 1
    assert conn.execute("SELECT finished FROM reading_progress WHERE volume_id = ?", (epub_vid,)).fetchone()[0] == 1
    # すべて stamp が記録されている
    for rel in ("was_good.cbz", "was_good.epub", "encrypted.pdf", "zero.pdf", "too_many.cbz", "big.epub"):
        row = conn.execute("SELECT size, mtime_ns, scan_version FROM sources WHERE rel_path = ?", (rel,)).fetchone()
        assert row["size"] is not None and row["mtime_ns"] is not None and row["scan_version"] == scanner.SCAN_VERSION

    # 次の通常スキャンでは 1 つも開き直さない・on_source も呼ばない・changed=False・issues は残る
    opened: list[str] = []
    inspected: list[str] = []
    seen: list[str] = []
    real_open = scanner.open_archive
    real_pdf = pdf_mod.inspect_pdf
    monkeypatch.setattr(scanner, "open_archive", lambda p, *a, **kw: opened.append(p.name) or real_open(p, *a, **kw))
    monkeypatch.setattr(pdf_mod, "inspect_pdf", lambda p, *a, **kw: inspected.append(p.name) or real_pdf(p, *a, **kw))
    skipped = Scanner(conn, settings, on_source=seen.append).scan()
    assert skipped.changed is False
    assert opened == [] and inspected == [] and seen == []
    assert skipped.issues_total == 6

    # recheck=True なら全部再検査する(それでも既存巻は消さない)
    rechecked = Scanner(conn, settings, recheck=True, on_source=seen.append).scan()
    assert rechecked.changed is True
    assert set(opened) == {"was_good.cbz", "was_good.epub", "too_many.cbz", "big.epub"}
    assert set(inspected) == {"encrypted.pdf", "zero.pdf"}
    assert volumes(conn)[("was_good.cbz", None, "")]["id"] == cbz_vid
    assert volumes(conn)[("was_good.epub", None, "")]["id"] == epub_vid

    # ファイルを修復すると通常スキャンで自動復帰する
    write_zip(lib / "was_good.cbz", [("1.jpg", page_bytes("repaired"))])
    (lib / "was_good.epub").write_bytes(epub_bytes(["<p>repaired</p>"]))
    opened.clear()
    repaired = scan(conn, settings)
    assert repaired.changed is True
    assert sorted(opened) == ["was_good.cbz", "was_good.epub"]
    assert volumes(conn)[("was_good.cbz", None, "")]["page_count"] == 1
    assert not any(i["path"].endswith(("was_good.cbz", "was_good.epub")) for i in repaired.issues)

    # 上限(max_entries)を引き上げた後は recheck=True で上限超えアーカイブも復帰する
    relaxed = make_settings(lib, max_entries=20)
    opened.clear()
    Scanner(conn, relaxed, recheck=True).scan()
    assert ("too_many.cbz", None, "") in volumes(conn)
    assert ("big.epub", None, "") in volumes(conn)


def test_unsupported_archive_and_unexpected_exception_are_not_cached(conn, tmp_path, make_settings, monkeypatch):
    """UnsupportedArchive(7-Zip 未設定の 7z や RAR マジックの .epub)と想定外例外は
    stamp を記録せず次回通常スキャンで再挑戦する。"""
    lib = tmp_path / "lib"
    lib.mkdir()
    (lib / "arc.7z").write_bytes(b"7z\xbc\xaf\x27\x1c" + b"\x00" * 16)
    # 拡張子は .epub だが中身が RAR マジック -> _scan_novel で UnsupportedArchive(ArchiveError より先に捕捉)
    (lib / "fake.epub").write_bytes(b"Rar!\x1a\x07\x01\x00" + b"\x00" * 16)
    write_zip(lib / "boom.cbz", [("1.jpg", page_bytes("1"))])
    settings = make_settings(lib)

    real_list = scanner.list_entries
    fail_boom = [True]

    def maybe_boom(reader, limits):
        if fail_boom[0] and getattr(reader, "path", None) and reader.path.name == "boom.cbz":
            raise RuntimeError("一時的な想定外エラー")
        return real_list(reader, limits)

    monkeypatch.setattr(scanner, "list_entries", maybe_boom)
    first = scan(conn, settings)
    assert first.changed is True
    for rel in ("arc.7z", "fake.epub", "boom.cbz"):
        row = conn.execute("SELECT size, scan_version FROM sources WHERE rel_path = ?", (rel,)).fetchone()
        assert row["size"] is None and row["scan_version"] is None

    # 2 回目: 想定外エラーが解消すれば boom.cbz は拾われ、arc.7z と fake.epub は再挑戦される
    fail_boom[0] = False
    opened: list[str] = []
    seen: list[str] = []
    real_open = scanner.open_archive
    monkeypatch.setattr(scanner, "open_archive", lambda p, *a, **kw: opened.append(p.name) or real_open(p, *a, **kw))
    second = Scanner(conn, settings, on_source=seen.append).scan()
    assert sorted(opened) == ["arc.7z", "boom.cbz", "fake.epub"]
    assert sorted(seen) == ["arc.7z", "boom.cbz", "fake.epub"]
    assert second.changed is True
    assert ("boom.cbz", None, "") in volumes(conn)

    # 3 回目: arc.7z / fake.epub は引き続き再挑戦するが、状態変化が無いため changed=False
    opened.clear()
    seen.clear()
    third = Scanner(conn, settings, on_source=seen.append).scan()
    assert sorted(opened) == ["arc.7z", "fake.epub"]
    assert sorted(seen) == ["arc.7z", "fake.epub"]
    assert third.changed is False

    # UnsupportedArchive が offline=1 から復帰したときは _source_state_changed により changed=True
    moved = tmp_path / "lib-offline-unsupported"
    lib.rename(moved)
    assert scan(conn, settings).changed is True
    assert conn.execute("SELECT offline FROM sources WHERE rel_path = 'arc.7z'").fetchone()[0] == 1
    assert conn.execute("SELECT offline FROM sources WHERE rel_path = 'fake.epub'").fetchone()[0] == 1
    moved.rename(lib)
    reconnected = scan(conn, settings)
    assert reconnected.changed is True
    assert conn.execute("SELECT offline FROM sources WHERE rel_path = 'arc.7z'").fetchone()[0] == 0
    assert conn.execute("SELECT offline FROM sources WHERE rel_path = 'fake.epub'").fetchone()[0] == 0
    assert scan(conn, settings).changed is False

    # PDF の inspect_pdf が想定外例外を投げた場合も stamp を更新せず次回再挑戦し、既存巻を残す
    from spinebox.organize import pdf as pdf_mod

    pdf_path = lib / "flaky.pdf"
    _make_pdf(pdf_path, num_pages=2)
    scan(conn, settings)
    pdf_vid = volumes(conn)[("flaky.pdf", None, "")]["id"]
    _make_pdf(pdf_path, num_pages=3)
    st = pdf_path.stat()
    os.utime(pdf_path, (st.st_atime + 10, st.st_mtime + 10))

    real_inspect = pdf_mod.inspect_pdf
    pdf_fail = [True]

    def flaky_inspect(p):
        if pdf_fail[0] and p.name == "flaky.pdf":
            raise RuntimeError("PDF 検査の想定外エラー")
        return real_inspect(p)

    monkeypatch.setattr(pdf_mod, "inspect_pdf", flaky_inspect)
    err_res = scan(conn, settings)
    assert any(i["path"].endswith("/flaky.pdf") and "RuntimeError" in i["reason"] for i in err_res.issues)
    assert volumes(conn)[("flaky.pdf", None, "")]["id"] == pdf_vid
    assert volumes(conn)[("flaky.pdf", None, "")]["page_count"] == 2  # 既存巻を保持
    pdf_fail[0] = False
    ok_res = scan(conn, settings)
    assert not any(i["path"].endswith("/flaky.pdf") for i in ok_res.issues)
    assert volumes(conn)[("flaky.pdf", None, "")]["page_count"] == 3  # 次回再挑戦で更新
