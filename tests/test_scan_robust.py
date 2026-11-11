"""スキャンの頑健さ(#48): 実際の蔵書にありそうな壊れ方・変わった形でも、スキャンは最後まで進む。

方針の確認:
- ソース 1 つで想定外の例外が出ても、他のソースの巻は登録され、問題が scan_issues と API の応答に残る
- 想定外の例外は握りつぶさずログ(トレースバックつき)に出る。DB が使えない例外だけはスキャンを止める
- 途中で例外が出たソースの古い巻・読書位置は消えない。見つからなかったことにもしない
テストの蔵書は一時ディレクトリの合成データ(`_参考/` に依存しない)。
"""
from __future__ import annotations

import logging
import os
import sqlite3
import struct
import subprocess
import sys
import warnings
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from conftest import page_bytes, write_zip, zip_bytes
from spinebox import db, scanner
from spinebox.app import create_app
from spinebox.scanner import MAX_REPORTED_ISSUES, Scanner


@pytest.fixture
def conn():
    return db.open_db(":memory:")


def scan(conn, settings):
    return Scanner(conn, settings).scan()


def titles(conn):
    return sorted(r["title"] for r in conn.execute(
        """SELECT v.title FROM volumes v JOIN sources s ON s.id = v.source_id
           WHERE v.missing = 0 AND s.missing = 0"""))


def issue_map(result):
    """path の末尾の名前 -> reason(同じ名前の複数があれば連結)"""
    out: dict[str, str] = {}
    for i in result.issues:
        key = i["path"].replace("\\", "/").rsplit("/", 1)[-1]
        out[key] = out.get(key, "") + i["reason"]
    return out


def good(lib: Path, name: str = "good.cbz") -> None:
    write_zip(lib / name, [("1.jpg", page_bytes(name))])


def encrypted_zip() -> bytes:
    """全エントリの「暗号化」ビットを立てた zip(目次は読めるが、中身を読むと RuntimeError)。"""
    data = bytearray(zip_bytes([("1.jpg", page_bytes("enc"))]))
    for sig, offset in ((b"PK\x03\x04", 6), (b"PK\x01\x02", 8)):
        pos = data.find(sig)
        while pos != -1:
            flags = struct.unpack_from("<H", data, pos + offset)[0]
            struct.pack_into("<H", data, pos + offset, flags | 0x1)
            pos = data.find(sig, pos + 4)
    return bytes(data)


def make_link(link: Path, target: Path, *, junction: bool = False) -> bool:
    """リンクを作る。作れない環境(権限・OS)では False。"""
    try:
        if junction:
            if sys.platform != "win32":
                return False
            r = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True)
            return r.returncode == 0
        os.symlink(target, link, target_is_directory=target.is_dir())
        return True
    except (OSError, NotImplementedError):
        return False


def remove_link(link: Path) -> None:
    try:
        os.rmdir(link) if link.is_dir() else os.unlink(link)
    except OSError:
        pass


# --- ソース 1 つの想定外の例外 ------------------------------------------------


def test_unexpected_exception_in_one_source_is_recorded_and_scan_continues(conn, tmp_path, make_settings, caplog):
    lib = tmp_path / "lib"
    good(lib, "a.cbz")
    write_zip(lib / "boom.cbz", [("1.jpg", page_bytes("boom"))])
    good(lib, "c.cbz")
    real = scanner.archive_volume_specs

    def explode(listing, archive_name, limits):
        if archive_name == "boom.cbz":
            raise ValueError("想定していなかった形")
        return real(listing, archive_name, limits)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(scanner, "archive_volume_specs", explode)
        with caplog.at_level(logging.ERROR, logger="spinebox.scanner"):
            result = scan(conn, make_settings(lib))
    assert titles(conn) == ["a", "c"]
    assert result.volumes == 2 and result.issues_total == 1
    (issue,) = result.issues
    assert issue["path"].endswith("/boom.cbz")
    assert "ValueError" in issue["reason"] and "想定していなかった形" in issue["reason"]
    # 握りつぶさない: トレースバックつきでログに出る
    records = [r for r in caplog.records if r.exc_info and r.exc_info[0] is ValueError]
    assert records and "boom.cbz" in records[0].getMessage()


def test_failing_source_keeps_its_old_volumes_and_is_retried(conn, tmp_path, make_settings):
    lib = tmp_path / "lib"
    write_zip(lib / "book.cbz", [("1.jpg", page_bytes("1")), ("2.jpg", page_bytes("2"))])
    settings = make_settings(lib)
    scan(conn, settings)
    vid = conn.execute("SELECT id FROM volumes").fetchone()["id"]
    conn.execute("INSERT INTO reading_progress (volume_id, page, finished, updated_at) VALUES (?, 1, 0, 'x')", (vid,))
    write_zip(lib / "book.cbz", [("1.jpg", page_bytes("1")), ("2.jpg", page_bytes("2")), ("3.jpg", page_bytes("3"))])
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(scanner, "list_entries", lambda *a, **k: (_ for _ in ()).throw(KeyError("boom")))
        result = scan(conn, settings)
    # 古い巻は残り(missing にならず)、読書位置も残る。壊れた状態は「読めなかったもの」に出る
    assert titles(conn) == ["book"]
    assert conn.execute("SELECT page_count FROM volumes").fetchone()["page_count"] == 2
    assert conn.execute("SELECT page FROM reading_progress WHERE volume_id = ?", (vid,)).fetchone()["page"] == 1
    assert result.issues_total == 1 and "KeyError" in result.issues[0]["reason"]
    # サイズ・mtime を更新していないので、次のスキャンで読み直して直る
    result = scan(conn, settings)
    assert result.issues == [] and conn.execute("SELECT page_count FROM volumes").fetchone()["page_count"] == 3


def test_unexpected_exception_while_registering_a_source_is_recorded(conn, tmp_path, make_settings, caplog):
    lib = tmp_path / "lib"
    good(lib, "a.cbz")
    good(lib, "b.cbz")
    real = Scanner._replace_volumes

    def explode(self, c, source_id, specs, **kw):
        if specs and specs[0].title == "b":
            raise OverflowError("Python int too large to convert to SQLite INTEGER")
        return real(self, c, source_id, specs, **kw)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(Scanner, "_replace_volumes", explode)
        with caplog.at_level(logging.ERROR, logger="spinebox.scanner"):
            result = scan(conn, make_settings(lib))
    assert titles(conn) == ["a"]  # b の途中の書き込みは巻き戻っている
    assert "OverflowError" in issue_map(result)["b.cbz"]
    assert any(r.exc_info and r.exc_info[0] is OverflowError for r in caplog.records)
    # b の記録(sources 行)は残り、次に直れば拾われる
    assert scan(conn, make_settings(lib)).issues == []
    assert titles(conn) == ["a", "b"]


def test_database_errors_stop_the_scan_instead_of_being_swallowed(conn, tmp_path, make_settings):
    lib = tmp_path / "lib"
    good(lib)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(Scanner, "_scan_archive",
                   lambda *a, **k: (_ for _ in ()).throw(sqlite3.OperationalError("database is locked")))
        with pytest.raises(sqlite3.OperationalError):
            scan(conn, make_settings(lib))


def test_one_failing_root_does_not_stop_the_other_roots(conn, tmp_path, make_settings):
    lib1, lib2 = tmp_path / "lib1", tmp_path / "lib2"
    good(lib1, "a.cbz")
    good(lib2, "b.cbz")
    real = Scanner._scan_root

    def explode(self, root_id, root):
        if root.name == "lib1":
            raise RuntimeError("ルートごと失敗")
        return real(self, root_id, root)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(Scanner, "_scan_root", explode)
        result = scan(conn, make_settings(lib1, lib2))
    assert titles(conn) == ["b"]
    assert any("RuntimeError" in i["reason"] and i["path"].endswith("lib1") for i in result.issues)


def test_walk_aborting_midway_does_not_mark_unvisited_sources_missing(conn, tmp_path, make_settings):
    lib = tmp_path / "lib"
    good(lib, "a.cbz")
    good(lib / "sub", "b.cbz")
    settings = make_settings(lib)
    scan(conn, settings)
    real_walk = scanner._walk

    def broken_walk(top, *a, **k):
        it = real_walk(top, *a, **k)
        yield next(it)  # ルート直下だけ見て
        raise RuntimeError("走査が途中で落ちた")

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(scanner, "_walk", broken_walk)
        result = scan(conn, settings)
    assert titles(conn) == ["a", "b"]  # sub/b.cbz を見ていないだけで、無くなったことにはしない
    assert any("RuntimeError" in i["reason"] for i in result.issues)
    assert scan(conn, settings).issues == []


# --- 蔵書にありそうな壊れた・変わったアーカイブ ----------------------------------


def test_broken_and_odd_archives_are_reported_not_fatal(conn, tmp_path, make_settings):
    lib = tmp_path / "lib"
    good(lib)
    (lib / "empty.zip").write_bytes(b"")  # 0 バイト
    (lib / "empty.cbz").write_bytes(b"")
    (lib / "garbage.zip").write_bytes(os.urandom(2000))
    (lib / "truncated.zip").write_bytes(zip_bytes([("1.jpg", page_bytes("t"))])[:-30])  # 末尾の目次が欠けた zip
    (lib / "fake.rar").write_bytes(b"not a rar")  # 7-Zip が無い環境では「未対応」
    (lib / "text-only.zip").write_bytes(zip_bytes([("readme.txt", b"hi"), ("data/", None)]))
    write_zip(lib / "no-entries.zip", [])  # 空の zip
    result = scan(conn, make_settings(lib))
    assert titles(conn) == ["good"]
    reasons = issue_map(result)
    assert set(reasons) == {"empty.zip", "empty.cbz", "garbage.zip", "truncated.zip", "fake.rar", "text-only.zip",
                            "no-entries.zip"}
    assert "読めない" in reasons["empty.zip"] and "未対応" in reasons["fake.rar"]
    assert "巻にできるもの" in reasons["text-only.zip"] and "巻にできるもの" in reasons["no-entries.zip"]


def test_encrypted_zip_lists_but_cannot_be_read(tmp_path, make_settings):
    lib = tmp_path / "lib"
    good(lib)
    (lib / "secret.zip").write_bytes(encrypted_zip())
    c = TestClient(create_app(make_settings(lib), frontend_dist=None))
    assert c.post("/api/scan").status_code == 200  # スキャンは目次だけ。暗号化でも落ちない
    vols = {v["title"]: v for v in c.get("/api/volumes").json()}
    assert set(vols) == {"good", "secret"}
    assert c.get(f"/api/volumes/{vols['secret']['id']}/pages/0").status_code == 422  # 開くと「読めない」


def test_same_named_inner_archives_register_one_volume_and_report(conn, tmp_path, make_settings):
    inner = zip_bytes([("1.jpg", page_bytes("in"))])
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)  # zipfile: Duplicate name
        write_zip(tmp_path / "lib" / "dup.zip", [("inner.zip", inner), ("inner.zip", inner), ("other.zip", inner)])
    good(tmp_path / "lib")
    result = scan(conn, make_settings(tmp_path / "lib"))
    assert titles(conn) == ["good", "inner", "other"]
    assert "同じ名前" in issue_map(result)["dup.zip"]


def test_archive_with_hostile_entry_names_and_many_issues_is_capped(conn, tmp_path, make_settings):
    lib = tmp_path / "lib"
    names = [f"../evil{i}.jpg" for i in range(30)] + ["C:/abs.jpg", "/root.jpg", "a\x00b.jpg", "ok.jpg"]
    write_zip(lib / "hostile.zip", [(n, page_bytes("x")) for n in names])
    for i in range(MAX_REPORTED_ISSUES):
        (lib / f"broken{i}.zip").write_bytes(b"x")
    result = scan(conn, make_settings(lib))
    assert titles(conn) == ["hostile"]
    assert len(result.issues) == MAX_REPORTED_ISSUES
    # 危険な名前は ../ 30 + 絶対パス 2(NUL を含む名前は zip の書き込みで切れるので数えない)
    assert result.issues_total == MAX_REPORTED_ISSUES + 32


def test_unsupported_extensions_and_imageless_folders_are_ignored_quietly(conn, tmp_path, make_settings):
    lib = tmp_path / "lib"
    good(lib)
    (lib / "book.docx").write_bytes(b"PK\x03\x04docx")
    (lib / "book.mobi").write_bytes(b"BOOKMOBI")  # .epub / .txt は小説、.pdf は PDF 巻になる
    (lib / "notes.md").write_text("x")
    (lib / "docfolder").mkdir()
    (lib / "docfolder" / "a.md").write_text("x")
    (lib / "empty folder").mkdir()
    result = scan(conn, make_settings(lib))
    assert titles(conn) == ["good"] and result.issues == []


# --- 名前・パス --------------------------------------------------------------


def test_names_that_cannot_be_encoded_are_reported_and_json_safe(tmp_path, make_settings):
    """Windows の孤立サロゲート・Linux の不正な UTF-8 バイト(surrogateescape)の名前。SQLite にも JSON にもそのまま入らない。"""
    lib = tmp_path / "lib"
    good(lib)
    bad_zip, bad_dir = lib / "bad\udcff.zip", lib / "bad\udcfe dir"
    try:
        bad_zip.write_bytes(zip_bytes([("1.jpg", page_bytes("b"))]))
        bad_dir.mkdir()
        (bad_dir / "1.jpg").write_bytes(page_bytes("d"))
    except (OSError, UnicodeError):
        pytest.skip("この OS はそのファイル名を作れない")
    c = TestClient(create_app(make_settings(lib), frontend_dist=None))
    r = c.post("/api/scan")
    assert r.status_code == 200
    body = r.json()  # JSON に出せる(エスケープ済み)
    assert [v["title"] for v in c.get("/api/volumes").json()] == ["good"]
    paths = [i["path"] for i in body["issues"]]
    assert any("bad\\udcff.zip" in p for p in paths) and any("bad\\udcfe dir" in p for p in paths)
    assert all("名前" in i["reason"] or "想定外" in i["reason"] for i in body["issues"])
    # 2 回目も落ちない(源ごとの記録は毎回作り直す)
    assert c.post("/api/scan").status_code == 200


def test_unreadable_folder_is_reported_and_its_books_are_not_marked_missing(conn, tmp_path, make_settings):
    lib = tmp_path / "lib"
    good(lib / "locked", "inside.cbz")
    good(lib, "outside.cbz")
    settings = make_settings(lib)
    scan(conn, settings)
    real = os.scandir

    def deny(path="."):
        if os.path.basename(os.fspath(path)) == "locked":
            raise PermissionError(13, "アクセスが拒否されました", os.fspath(path))
        return real(path)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(os, "scandir", deny)
        result = scan(conn, settings)
    assert titles(conn) == ["inside", "outside"]
    (issue,) = result.issues
    assert issue["path"].replace("\\", "/").endswith("/lib/locked") and "読めないフォルダ" in issue["reason"]
    assert scan(conn, settings).issues == []  # 読めるようになれば消える


@pytest.mark.skipif(sys.platform != "win32", reason="MAX_PATH を超えるパスは Windows 固有")
def test_path_longer_than_max_path_does_not_stop_the_scan(conn, tmp_path, make_settings):
    lib = tmp_path / "lib"
    good(lib)
    deep = Path("\\\\?\\" + str(lib)) / ("d" * 100) / ("e" * 100) / ("f" * 100)
    deep.mkdir(parents=True)
    write_zip(deep / "deep.cbz", [("1.jpg", page_bytes("deep"))])
    scan(conn, make_settings(lib))  # 読めるか(長いパス対応)は環境次第。落ちないことが条件
    assert "good" in titles(conn)


# --- リンク・読めない画像 ------------------------------------------------------


def test_junction_pointing_to_ancestor_is_not_followed(conn, tmp_path, make_settings):
    lib = tmp_path / "lib"
    good(lib / "series", "a.cbz")
    link = lib / "series" / "loop"
    if not make_link(link, lib, junction=True):
        pytest.skip("ジャンクションを作れない(Windows 以外)")
    try:
        result = scan(conn, make_settings(lib))
        # 循環を辿ると同じ本が深さの違う別のソースとして何重にも登録される
        assert [r["rel_path"] for r in conn.execute("SELECT rel_path FROM sources")] == ["series/a.cbz"]
        assert any("リンク" in i["reason"] for i in result.issues)
    finally:
        remove_link(link)


def test_symlink_to_ancestor_does_not_loop(conn, tmp_path, make_settings):
    lib = tmp_path / "lib"
    good(lib / "series", "a.cbz")
    link = lib / "series" / "loop"
    if not make_link(link, lib):
        pytest.skip("シンボリックリンクを作れない(権限・OS)")
    try:
        scan(conn, make_settings(lib))
        assert [r["rel_path"] for r in conn.execute("SELECT rel_path FROM sources")] == ["series/a.cbz"]
    finally:
        remove_link(link)


def test_dangling_symlinked_images_are_reported(conn, tmp_path, make_settings):
    lib = tmp_path / "lib"
    folder = lib / "book"
    folder.mkdir(parents=True)
    (folder / "1.jpg").write_bytes(page_bytes("1"))
    allbad = lib / "allbad"
    allbad.mkdir()
    if not (make_link(folder / "2.jpg", tmp_path / "nowhere.jpg") and make_link(allbad / "1.jpg", tmp_path / "x.jpg")):
        pytest.skip("シンボリックリンクを作れない(権限・OS)")
    result = scan(conn, make_settings(lib))
    # 一部だけ読めない: 読めるものだけで巻にして報告。全部読めない: 巻にせず報告
    assert titles(conn) == ["book"]
    assert conn.execute("SELECT page_count FROM volumes").fetchone()["page_count"] == 1
    reasons = issue_map(result)
    assert "読めない画像" in reasons["book"] and "読める画像が 1 枚も無い" in reasons["allbad"]


# --- 応答と、画面のための API ----------------------------------------------------


@pytest.fixture
def make_client(make_settings):
    def make(*roots, **kw):
        settings = make_settings(*roots, **kw)
        return TestClient(create_app(settings, frontend_dist=None)), settings
    return make


def test_scan_response_lists_issues_and_drops_them_when_the_source_is_gone(make_client, tmp_path):
    lib = tmp_path / "lib"
    good(lib)
    (lib / "broken.zip").write_bytes(b"x")
    c, _ = make_client(lib)
    body = c.post("/api/scan").json()
    assert body["volumes"] == 1 and body["issues_total"] == 1
    (issue,) = body["issues"]
    assert set(issue) == {"path", "container_path", "entry_name", "reason"}
    assert issue["path"].endswith("/lib/broken.zip") and "読めないアーカイブ" in issue["reason"]
    (lib / "broken.zip").unlink()
    again = c.post("/api/scan").json()
    assert again["issues"] == [] and again["issues_total"] == 0  # 見えなくなったソースの記録は出さない


def test_unchanged_archive_keeps_reporting_its_entry_issues(make_client, tmp_path):
    lib = tmp_path / "lib"
    write_zip(lib / "unsafe.zip", [("../evil.jpg", b"x"), ("ok.jpg", page_bytes("ok"))])
    c, _ = make_client(lib)
    first = c.post("/api/scan").json()
    second = c.post("/api/scan").json()  # 変わっていないので開き直さないが、記録は DB から出る
    assert first["issues"] == second["issues"] and second["issues_total"] == 1
    assert second["issues"][0]["entry_name"] == "../evil.jpg"


def test_scan_failure_returns_the_cause_in_detail_and_logs_traceback(make_client, tmp_path, caplog):
    lib = tmp_path / "lib"
    good(lib)
    c, _ = make_client(lib)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(Scanner, "_scan_archive",
                   lambda *a, **k: (_ for _ in ()).throw(sqlite3.OperationalError("database is locked")))
        with caplog.at_level(logging.ERROR, logger="spinebox.app"):
            r = c.post("/api/scan")
    assert r.status_code == 500
    assert "database is locked" in r.json()["detail"] and "OperationalError" in r.json()["detail"]
    assert any(rec.exc_info and rec.exc_info[0] is sqlite3.OperationalError for rec in caplog.records)
    assert c.post("/api/scan").status_code == 200  # ロックは解放されている
