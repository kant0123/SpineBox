"""しおり(#136): 漫画リーダー(manga-viewer)の bookmarkApi の形の API と、整理・巻への分解での引き継ぎ。"""
import sqlite3

import pytest
from fastapi.testclient import TestClient

from conftest import page_bytes, write_zip, zip_bytes
from spinebox import db
from spinebox.app import create_app


@pytest.fixture
def make_client(make_settings):
    def make(*roots, **kw):
        settings = make_settings(*roots, **kw)
        return TestClient(create_app(settings, frontend_dist=None)), settings
    return make


@pytest.fixture
def client(make_client, library):
    c, _ = make_client(library)
    assert c.post("/api/scan").status_code == 200
    return c


def by_title(client):
    return {v["title"]: v for v in client.get("/api/volumes").json()}


def marks(client, work_id):
    r = client.get("/api/page-marks", params={"work_id": work_id})
    assert r.status_code == 200
    return r.json()


def add(client, work_id, page_number, **kw):
    return client.post("/api/page-marks", json={"work_id": work_id, "page_number": page_number, "title": "x", **kw})


def rows(settings, volume_id):
    conn = sqlite3.connect(settings.db_path)
    try:
        return [r[0] for r in conn.execute("SELECT page FROM page_marks WHERE volume_id = ? ORDER BY page", (volume_id,))]
    finally:
        conn.close()


def test_add_list_remove_with_one_based_pages_and_string_work_id(client):
    vid = by_title(client)["Vol1"]["id"]  # 2 ページ
    assert marks(client, str(vid)) == {"success": True, "bookmarks": []}
    assert add(client, str(vid), 2).json() == {"success": True}  # 文字列の work_id
    assert add(client, vid, 1).json() == {"success": True}       # 整数の work_id
    assert add(client, vid, 1).json() == {"success": True}       # 二重に挟んでも増えない
    assert marks(client, vid) == {"success": True, "bookmarks": [
        {"page_number": 1, "title": "ページ1"}, {"page_number": 2, "title": "ページ2"}]}
    r = client.request("DELETE", "/api/page-marks", json={"work_id": str(vid), "page_number": 1})
    assert r.json() == {"success": True}
    assert client.request("DELETE", "/api/page-marks", json={"work_id": vid, "page_number": 1}).json() == {"success": True}
    assert marks(client, vid)["bookmarks"] == [{"page_number": 2, "title": "ページ2"}]


def test_unknown_volume_is_404_json(client):
    for r in (client.get("/api/page-marks", params={"work_id": "99999"}), add(client, "99999", 1),
              client.request("DELETE", "/api/page-marks", json={"work_id": 99999, "page_number": 1})):
        assert r.status_code == 404
        assert r.json() == {"success": False, "error": "巻が見つかりません"}


def test_out_of_range_page_is_422(client):
    vid = by_title(client)["Vol1"]["id"]
    for page in (0, -1, 3):
        r = add(client, vid, page)
        assert r.status_code == 422 and r.json()["success"] is False and "範囲外" in r.json()["error"]
    assert marks(client, vid)["bookmarks"] == []


def test_bad_work_id_and_bodies_are_422_json(client):
    vid = by_title(client)["Vol1"]["id"]
    bad = [{"work_id": "abc", "page_number": 1}, {"work_id": 1.5, "page_number": 1}, {"work_id": True, "page_number": 1},
           {"work_id": "", "page_number": 1}, {"page_number": 1},
           {"work_id": vid, "page_number": "1"}, {"work_id": vid, "page_number": True}, {"work_id": vid}]
    for body in bad:
        r = client.post("/api/page-marks", json=body)
        assert r.status_code == 422 and r.json()["success"] is False and r.json()["error"], body
    r = client.post("/api/page-marks", content=b"not json", headers={"content-type": "application/json"})
    assert r.status_code == 422 and r.json()["success"] is False
    r = client.post("/api/page-marks", json=[1, 2])
    assert r.status_code == 422 and r.json()["success"] is False
    for params in ({"work_id": "abc"}, {}):
        r = client.get("/api/page-marks", params=params)
        assert r.status_code == 422 and r.json()["success"] is False


def test_too_many_marks_is_409(make_client, tmp_path):
    root = tmp_path / "big"
    write_zip(root / "long.cbz", [(f"{i:03d}.jpg", page_bytes(f"long/{i}")) for i in range(1, 26)])
    c, _ = make_client(root)
    c.post("/api/scan")
    vid = by_title(c)["long"]["id"]
    for p in range(1, 21):
        assert add(c, vid, p).status_code == 200
    r = add(c, vid, 21)
    assert r.status_code == 409 and r.json() == {"success": False, "error": "しおりは20個までです"}
    assert add(c, vid, 20).status_code == 200  # 既にある所は上限でも成功
    assert c.request("DELETE", "/api/page-marks", json={"work_id": vid, "page_number": 1}).status_code == 200
    assert add(c, vid, 21).status_code == 200
    assert len(marks(c, vid)["bookmarks"]) == 20


def test_offline_volume_keeps_and_edits_marks(make_client, library):
    c, settings = make_client(library)
    c.post("/api/scan")
    vid = by_title(c)["Vol1"]["id"]
    add(c, vid, 2)
    moved = library.with_name("unplugged")
    library.rename(moved)
    c.post("/api/scan")
    assert marks(c, vid)["bookmarks"] == [{"page_number": 2, "title": "ページ2"}]
    assert add(c, vid, 1).status_code == 200
    assert c.request("DELETE", "/api/page-marks", json={"work_id": vid, "page_number": 1}).status_code == 200
    moved.rename(library)
    c.post("/api/scan")
    assert rows(settings, vid) == [1]


def test_organize_moves_marks_to_new_volume_and_undo_moves_back(make_client, library):
    c, settings = make_client(library)
    c.post("/api/scan")
    old_id = by_title(c)["Vol1"]["id"]
    add(c, old_id, 2)
    plan = c.post("/api/organize/plan", json={"volume_id": old_id}).json()
    done = c.post(f"/api/organize/{plan['op_id']}/execute").json()
    new_id = next(v["new_volume_id"] for v in done["volumes"] if v["volume_id"] == old_id)
    assert new_id != old_id
    assert rows(settings, new_id) == [1] and rows(settings, old_id) == []
    assert marks(c, new_id)["bookmarks"] == [{"page_number": 2, "title": "ページ2"}]
    assert c.post(f"/api/organize/{plan['op_id']}/undo").json()["state"] == "undone"
    assert rows(settings, old_id) == [1] and rows(settings, new_id) == []


@pytest.fixture
def nested_lib(tmp_path):
    v = {i: zip_bytes([(f"{p}.jpg", page_bytes(f"v{i}/{p}")) for p in ("002", "001")]) for i in (1, 2, 3)}
    inner = zip_bytes([("v01.zip", v[1]), ("v02-03.zip", zip_bytes([("v02.zip", v[2]), ("v03.zip", v[3])]))])
    lib = tmp_path / "lib"
    write_zip(lib / "series.zip", [("set v01-03.zip", inner)])
    return lib


def test_split_copies_parent_marks_to_children(make_client, nested_lib):
    c, settings = make_client(nested_lib)
    c.post("/api/scan")
    (parent,) = c.get("/api/volumes").json()
    pid = parent["id"]
    # 解析前の親は 6 ページ(親の順で 1・4 ページ目 = v01 の 1 ページ目・v02 の 2 ページ目)。範囲外(7)は残るが写らない
    for p in (1, 4):
        assert add(c, pid, p).status_code == 200
    conn = sqlite3.connect(settings.db_path)
    conn.execute("INSERT INTO page_marks (volume_id, page, created_at) VALUES (?, 50, 'x')", (pid,))
    conn.commit()
    conn.close()
    c.post(f"/api/volumes/{pid}/analyze")
    ids = {v["title"]: v["id"] for v in c.get("/api/volumes").json()}
    assert rows(settings, ids["v01"]) == [0]
    assert rows(settings, ids["v02"]) == [1]
    assert rows(settings, ids["v03"]) == []
    assert rows(settings, pid) == [0, 3, 50]  # 親の記録は消さない
    # 子に付けたしおりは触らず、再解析で重複もしない
    add(c, ids["v03"], 2)
    c.post(f"/api/volumes/{pid}/analyze")
    assert rows(settings, ids["v03"]) == [1] and rows(settings, ids["v02"]) == [1]
