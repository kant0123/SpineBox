"""巻への分解と、名前の巻範囲と中身の食い違い(#30)。蔵書は zipfile で動的生成する。"""
import sqlite3
import time

import pytest
from fastapi.testclient import TestClient

from conftest import page_bytes, snapshot, write_zip, zip_bytes
from spinebox import db
from spinebox.app import create_app
from spinebox.cache import ExtractionCache
from spinebox.library import Library


@pytest.fixture
def make_client(make_settings):
    def make(*roots, **kw):
        settings = make_settings(*roots, **kw)
        return TestClient(create_app(settings, frontend_dist=None)), settings
    return make


def listed(client):
    return client.get("/api/volumes").json()


def titles(client):
    return [v["title"] for v in listed(client)]


def pages(client, vid):
    detail = client.get(f"/api/volumes/{vid}")
    assert detail.status_code == 200, detail.text
    return [client.get(f"/api/volumes/{vid}/pages/{p['index']}").content for p in detail.json()["pages"]]


def folder_pages(label, n=2):
    return [(f"{label}/", None)] + [(f"{label}/{i:03d}.jpg", page_bytes(f"{label}/{i}")) for i in range(1, n + 1)]


def nested_set() -> bytes:
    """入れ子 3 段: set v01-03.zip → (v01.zip, v02-03.zip → (v02.zip, v03.zip))。"""
    v = {i: zip_bytes([(f"{p}.jpg", page_bytes(f"v{i}/{p}")) for p in ("002", "001")]) for i in (1, 2, 3)}
    v0203 = zip_bytes([("v02.zip", v[2]), ("v03.zip", v[3])])
    return zip_bytes([("v01.zip", v[1]), ("v02-03.zip", v0203)])


@pytest.fixture
def nested_lib(tmp_path):
    lib = tmp_path / "lib"
    write_zip(lib / "series.zip", [("set v01-03.zip", nested_set())])
    return lib


# --- 名前の巻範囲と中身の食い違い -------------------------------------------------


def test_missing_volume_in_outer_archive_is_reported(make_client, tmp_path):
    lib = tmp_path / "lib"
    write_zip(lib / "作品 v01-03.zip", folder_pages("v01") + folder_pages("v03"))
    c, _ = make_client(lib)
    c.post("/api/scan")
    vols = listed(c)
    assert [v["title"] for v in vols] == ["v01", "v03"]
    for v in vols:
        (f,) = v["findings"]
        assert f["scope"] == "source" and f["subject"] == "作品 v01-03.zip"
        assert f["name_range"] == {"kind": "volume", "start": 1, "end": 3, "evidence": "v01-03"}
        assert (f["found"], f["missing"], f["extra"]) == ([1, 3], [2], [])
        assert "2 巻が見当たらない" in f["message"]
    detail = c.get(f"/api/volumes/{vols[0]['id']}").json()
    assert detail["findings"][0]["missing"] == [2]
    assert detail["volume_number"]["start"] == 1


def test_complete_range_has_no_findings(make_client, tmp_path):
    lib = tmp_path / "lib"
    write_zip(lib / "作品 v01-03.zip", folder_pages("v01") + folder_pages("v02") + folder_pages("v03"))
    c, _ = make_client(lib)
    c.post("/api/scan")
    assert [v["findings"] for v in listed(c)] == [[], [], []]


def test_extra_volume_is_reported(make_client, tmp_path):
    lib = tmp_path / "lib"
    write_zip(lib / "第01-02巻.zip", folder_pages("第01巻") + folder_pages("第02巻") + folder_pages("第03巻"))
    c, _ = make_client(lib)
    c.post("/api/scan")
    f = listed(c)[0]["findings"][0]
    assert (f["missing"], f["extra"]) == ([], [3])


def test_duplicate_scans_of_the_same_volume_are_not_a_mismatch(make_client, tmp_path):
    """別スキャンの重複(同じ巻番号のフォルダが 2 つ)は食い違いとして騒がない。重複検出は後続の役目。"""
    lib = tmp_path / "lib"
    write_zip(lib / "Okiraku v01-02.zip", folder_pages("Okiraku v01") + folder_pages("お気楽 第01巻")
              + folder_pages("Okiraku v02"))
    c, _ = make_client(lib)
    c.post("/api/scan")
    assert [v["findings"] for v in listed(c)] == [[], [], []]


def test_single_volume_archive_is_not_compared_with_itself(make_client, tmp_path):
    lib = tmp_path / "lib"
    write_zip(lib / "v01-03.zip", [("001.jpg", page_bytes("1"))])
    c, _ = make_client(lib)
    c.post("/api/scan")
    (v,) = listed(c)
    assert v["findings"] == [] and v["volume_number"]["end"] == 3


# --- 巻への分解 -----------------------------------------------------------------


def test_scan_alone_does_not_extract_or_split(make_client, nested_lib):
    c, settings = make_client(nested_lib)
    assert c.post("/api/scan").json()["volumes"] == 1
    (v,) = listed(c)
    assert v["title"] == "set v01-03" and v["can_analyze"] is True and v["parent_id"] is None
    assert list(settings.cache_dir.iterdir()) == []


def test_analyze_splits_three_level_nesting_into_child_volumes(make_client, nested_lib):
    before = snapshot(nested_lib)
    c, settings = make_client(nested_lib)
    c.post("/api/scan")
    (parent,) = listed(c)
    r = c.post(f"/api/volumes/{parent['id']}/analyze")
    assert r.status_code == 200
    body = r.json()
    assert body["analyzed"] is True and body["findings"] == []
    assert [(ch["title"], ch["page_count"], ch["volume_number"]["start"]) for ch in body["children"]] == [
        ("v01", 2, 1), ("v02", 2, 2), ("v03", 2, 3)]
    vols = listed(c)
    assert [v["title"] for v in vols] == ["v01", "v02", "v03"]  # 親(まとめ)は出さず子を並べる
    assert {v["parent_id"] for v in vols} == {parent["id"]}
    assert {v["parent_title"] for v in vols} == {"set v01-03"}
    assert all(v["findings"] == [] and not v["can_analyze"] for v in vols)
    assert [pages(c, v["id"]) for v in vols] == [
        [page_bytes(f"v{i}/001"), page_bytes(f"v{i}/002")] for i in (1, 2, 3)]
    # 取り出しはハッシュ名で各段 1 つずつ(親と子で同じキー)。蔵書は変わらない
    assert len(list(settings.cache_dir.iterdir())) == 5
    assert all(len(p.stem) == 64 for p in settings.cache_dir.iterdir())
    assert snapshot(nested_lib) == before
    # 親は「まとめ」として残り、開けば全ページを読める
    assert len(pages(c, parent["id"])) == 6
    # 一覧の件数(スキャンの返り値)も子の数になる
    assert c.post("/api/scan").json()["volumes"] == 3


def test_opening_the_inner_archive_volume_splits_it(make_client, nested_lib):
    c, _ = make_client(nested_lib)
    c.post("/api/scan")
    (parent,) = listed(c)
    assert c.get(f"/api/volumes/{parent['id']}").status_code == 200
    assert titles(c) == ["v01", "v02", "v03"]


def test_missing_volume_inside_inner_archive_is_reported(make_client, tmp_path):
    inner = zip_bytes(folder_pages("v01") + folder_pages("v03"))
    lib = tmp_path / "lib"
    write_zip(lib / "outer.zip", [("v01-03.zip", inner), ("v04.zip", zip_bytes(folder_pages("v04")))])
    c, _ = make_client(lib)
    c.post("/api/scan")
    parent = next(v for v in listed(c) if v["title"] == "v01-03")
    body = c.post(f"/api/volumes/{parent['id']}/analyze").json()
    (f,) = body["findings"]
    assert (f["scope"], f["subject"], f["missing"]) == ("volume", "v01-03", [2])
    vols = {v["title"]: v for v in listed(c)}
    assert set(vols) == {"v01", "v03", "v04"}
    assert [g["missing"] for g in vols["v01"]["findings"]] == [[2]]
    assert vols["v04"]["findings"] == []


def test_single_unit_inner_archive_is_not_split(make_client, library):
    c, _ = make_client(library)
    c.post("/api/scan")
    inner1 = next(v for v in listed(c) if v["title"] == "inner1")
    body = c.post(f"/api/volumes/{inner1['id']}/analyze").json()
    assert body == {"id": inner1["id"], "analyzed": True, "children": [], "findings": []}
    v = next(v for v in listed(c) if v["id"] == inner1["id"])
    assert v["can_analyze"] is False and v["page_count"] == 3


def test_folder_volume_is_not_analyzable(make_client, library):
    c, _ = make_client(library)
    c.post("/api/scan")
    vid = next(v["id"] for v in listed(c) if v["title"] == "FolderBook")
    assert c.post(f"/api/volumes/{vid}/analyze").json()["analyzed"] is False
    assert c.post("/api/volumes/99999/analyze").status_code == 404


def test_reanalysis_and_rescan_keep_child_ids(make_client, nested_lib):
    c, _ = make_client(nested_lib)
    c.post("/api/scan")
    (parent,) = listed(c)
    first = [ch["id"] for ch in c.post(f"/api/volumes/{parent['id']}/analyze").json()["children"]]
    second = [ch["id"] for ch in c.post(f"/api/volumes/{parent['id']}/analyze").json()["children"]]
    assert first == second
    c.post("/api/scan")
    assert [v["id"] for v in listed(c)] == first  # スキャンは子の巻を missing にしない


def test_changed_source_is_reanalyzed_and_vanished_units_are_kept_as_missing(make_client, nested_lib, settings_db):
    c, settings = make_client(nested_lib)
    c.post("/api/scan")
    (parent,) = listed(c)
    c.post(f"/api/volumes/{parent['id']}/analyze")
    ids = {v["title"]: v["id"] for v in listed(c)}
    c.put(f"/api/volumes/{ids['v02']}/progress", json={"page": 1, "finished": False})
    # v03 を除いた中身に差し替える(サイズが変わる)
    v2 = zip_bytes([("001.jpg", page_bytes("v2/001")), ("002.jpg", page_bytes("v2/002"))])
    v1 = zip_bytes([("001.jpg", page_bytes("v1/001"))])
    write_zip(nested_lib / "series.zip", [("set v01-03.zip", zip_bytes([("v01.zip", v1), ("v02-03.zip",
                                                                          zip_bytes([("v02.zip", v2)]))]))])
    c.post("/api/scan")
    assert listed(c)[0]["can_analyze"] is False  # 子があるので親は出ない(未解析に戻っても子は残る)
    c.get(f"/api/volumes/{parent['id']}")       # 開くと解析し直す
    vols = {v["title"]: v for v in listed(c)}
    assert set(vols) == {"v01", "v02"}
    assert vols["v02"]["id"] == ids["v02"] and vols["v02"]["progress"] == {"page": 1, "finished": False}
    conn = settings_db(settings)
    assert conn.execute("SELECT missing FROM volumes WHERE id = ?", (ids["v03"],)).fetchone()[0] == 1
    assert c.get(f"/api/volumes/{ids['v03']}").status_code == 404


@pytest.fixture
def settings_db():
    conns = []

    def connect(settings):
        conn = sqlite3.connect(settings.db_path)
        conns.append(conn)
        return conn
    yield connect
    for conn in conns:
        conn.close()


# --- 読書位置 -------------------------------------------------------------------


def test_child_progress_is_saved_and_resumed(make_client, nested_lib):
    c, _ = make_client(nested_lib)
    c.post("/api/scan")
    (parent,) = listed(c)
    c.post(f"/api/volumes/{parent['id']}/analyze")
    vid = listed(c)[1]["id"]
    assert c.put(f"/api/volumes/{vid}/progress", json={"page": 1, "finished": True}).status_code == 200
    assert c.put(f"/api/volumes/{vid}/progress", json={"page": 2, "finished": False}).status_code == 422
    assert c.get(f"/api/volumes/{vid}").json()["progress"] == {"page": 1, "finished": True}
    assert listed(c)[1]["progress"] == {"page": 1, "finished": True}


def test_parent_progress_before_split_is_carried_to_children(make_client, nested_lib, settings_db):
    """分解前に親で付けた位置: 親の記録は残し、その位置のページを含む子へ写す。それより前の子は既読にする。"""
    c, settings = make_client(nested_lib)
    c.post("/api/scan")
    (parent,) = listed(c)
    # 解析前の親は開くまでページ数が分からない(範囲検査なしで保存できる)。親の順で 4 ページ目 = v02 の 2 ページ目
    assert c.put(f"/api/volumes/{parent['id']}/progress", json={"page": 3, "finished": False}).status_code == 200
    c.post(f"/api/volumes/{parent['id']}/analyze")
    assert [(v["title"], v["progress"]) for v in listed(c)] == [
        ("v01", {"page": 1, "finished": True}),
        ("v02", {"page": 1, "finished": False}),
        ("v03", None),
    ]
    conn = settings_db(settings)
    assert conn.execute("SELECT page FROM reading_progress WHERE volume_id = ?", (parent["id"],)).fetchone() == (3,)


def test_finished_parent_marks_all_children_finished_and_existing_child_progress_wins(make_client, nested_lib):
    c, _ = make_client(nested_lib)
    c.post("/api/scan")
    (parent,) = listed(c)
    c.post(f"/api/volumes/{parent['id']}/analyze")
    first = listed(c)[0]["id"]
    c.put(f"/api/volumes/{first}/progress", json={"page": 0, "finished": False})
    c.put(f"/api/volumes/{parent['id']}/progress", json={"page": 5, "finished": True})
    c.post(f"/api/volumes/{parent['id']}/analyze")
    assert [v["progress"] for v in listed(c)] == [
        {"page": 0, "finished": False}, {"page": 1, "finished": True}, {"page": 1, "finished": True}]


# --- オフライン -----------------------------------------------------------------


def test_offline_root_is_not_analyzed_and_records_survive(make_client, nested_lib, settings_db):
    c, settings = make_client(nested_lib)
    c.post("/api/scan")
    (parent,) = listed(c)
    conn = settings_db(settings)
    before = conn.execute("SELECT * FROM volumes ORDER BY id").fetchall()
    moved = nested_lib.with_name("unplugged")
    nested_lib.rename(moved)
    r = c.post(f"/api/volumes/{parent['id']}/analyze")
    assert r.status_code == 409 and r.json()["offline"] is True
    c.post("/api/scan")
    assert conn.execute("SELECT * FROM volumes ORDER BY id").fetchall() == before
    v = listed(c)[0]
    assert v["offline"] is True and v["can_analyze"] is False
    assert list(settings.cache_dir.iterdir()) == []
    moved.rename(nested_lib)
    c.post("/api/scan")
    assert c.post(f"/api/volumes/{parent['id']}/analyze").status_code == 200


def test_offline_children_stay_listed(make_client, nested_lib):
    c, _ = make_client(nested_lib)
    c.post("/api/scan")
    (parent,) = listed(c)
    c.post(f"/api/volumes/{parent['id']}/analyze")
    c.put(f"/api/volumes/{listed(c)[0]['id']}/progress", json={"page": 1, "finished": False})
    nested_lib.rename(nested_lib.with_name("unplugged"))
    c.post("/api/scan")
    vols = listed(c)
    assert [v["title"] for v in vols] == ["v01", "v02", "v03"] and all(v["offline"] for v in vols)
    assert vols[0]["progress"] == {"page": 1, "finished": False}
    assert c.get(f"/api/volumes/{vols[0]['id']}").status_code == 409


# --- 裏の少しずつ解析 -----------------------------------------------------------


def test_analyze_next_processes_one_volume_at_a_time(make_settings, library, nested_lib):
    settings = make_settings(library, nested_lib)
    conn = db.open_db(settings.db_path)
    from spinebox.scanner import Scanner
    Scanner(conn, settings).scan()
    lib = Library(settings, ExtractionCache(settings.cache_dir, settings.cache_limit_bytes))
    done = []
    while (vid := lib.analyze_next(conn)) is not None:
        done.append(vid)
        assert len(done) < 10
    rows = {r["title"]: r for r in conn.execute(
        "SELECT title, analyzed_version, analysis_error FROM volumes WHERE parent_id IS NULL "
        "AND container_path IS NOT NULL")}
    assert set(rows) == {"inner1", "inner2", "inner3", "set v01-03"} and len(done) == 4
    assert rows["inner3"]["analyzed_version"] == "error" and "未対応" in rows["inner3"]["analysis_error"]
    assert rows["inner1"]["analysis_error"] is None and rows["inner1"]["analyzed_version"].startswith("2:")
    assert conn.execute("SELECT COUNT(*) FROM volumes WHERE parent_id IS NOT NULL").fetchone()[0] == 3
    conn.close()


def test_analyze_next_skips_offline_roots_without_recording(make_settings, nested_lib):
    settings = make_settings(nested_lib)
    conn = db.open_db(settings.db_path)
    from spinebox.scanner import Scanner
    Scanner(conn, settings).scan()
    lib = Library(settings, ExtractionCache(settings.cache_dir, settings.cache_limit_bytes))
    nested_lib.rename(nested_lib.with_name("unplugged"))
    assert lib.analyze_next(conn) is None  # 再スキャン前でも、見えないルートは記録せず飛ばす
    assert [tuple(r) for r in conn.execute("SELECT analyzed_version FROM volumes")] == [(None,)]
    conn.close()


def test_background_analysis_runs_when_enabled(make_settings, nested_lib):
    import dataclasses
    settings = dataclasses.replace(make_settings(nested_lib), background_analysis=True,
                                   background_analysis_interval=1)
    with TestClient(create_app(settings, frontend_dist=None)) as c:
        c.post("/api/scan")
        deadline = time.monotonic() + 10
        while titles(c) != ["v01", "v02", "v03"]:
            assert time.monotonic() < deadline, titles(c)
            time.sleep(0.2)


def test_background_analysis_is_off_by_default(make_client, nested_lib):
    c, settings = make_client(nested_lib)
    assert settings.background_analysis is False
    with c:
        c.post("/api/scan")
        assert titles(c) == ["set v01-03"]


def test_analyze_excludes_non_content_and_cover_only_folders_in_inner_archive(make_client, tmp_path):
    """内側アーカイブの分解で非コンテンツディレクトリと表紙のみフォルダは子の巻にならない。"""
    from conftest import page_bytes, write_zip, zip_bytes
    lib = tmp_path / "lib"
    inner = zip_bytes([
        ("Vol1/001.jpg", page_bytes("v1/001")),
        ("Vol1/002.jpg", page_bytes("v1/002")),
        ("Vol2/001.jpg", page_bytes("v2/001")),
        ("Vol2/002.jpg", page_bytes("v2/002")),
        ("__MACOSX/ghost.jpg", page_bytes("mac")),
        ("CoverOnly/cover.jpg", page_bytes("cover")),
        ("CoverOnly/front_cover.jpg", page_bytes("cover2")),
    ])
    write_zip(lib / "parent.zip", [("inner.zip", inner)])
    c, _ = make_client(lib)
    c.post("/api/scan")
    (parent,) = listed(c)
    r = c.post(f"/api/volumes/{parent['id']}/analyze")
    assert r.status_code == 200
    titles = [ch["title"] for ch in r.json()["children"]]
    assert titles == ["Vol1", "Vol2"]
