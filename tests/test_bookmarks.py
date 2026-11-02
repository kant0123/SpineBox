"""ブックマーク(#86): 付け外し・一覧の API と、整理・作品の統合・オフラインでの引き継ぎ。"""
import pytest
from fastapi.testclient import TestClient

from conftest import page_bytes, write_zip
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


def listed(client, sort="added"):
    r = client.get(f"/api/bookmarks?sort={sort}")
    assert r.status_code == 200
    return [(i["kind"], (i["work"] if i["kind"] == "work" else i["volume"])["title"]) for i in r.json()["items"]]


def set_created(settings, kind, target_id, when):
    """付けた日時を決め打ちにする(秒単位なので、同じ秒に付けた順は id で決まる)。"""
    conn = db.connect(settings.db_path)
    with db.transaction(conn) as c:
        c.execute(f"UPDATE bookmarks SET created_at = ? WHERE {kind}_id = ?", (when, target_id))
    conn.close()


def test_toggle_volume_and_work_and_flags(client):
    vols = by_title(client)
    vid, wid = vols["single"]["id"], vols["FolderBook"]["work_id"]
    assert vols["single"]["bookmarked"] is False
    assert client.get("/api/bookmarks").json() == {"sort": "added", "items": []}

    r = client.put(f"/api/bookmarks/volumes/{vid}")
    assert r.status_code == 200 and r.json()["bookmarked"] is True and r.json()["volume_id"] == vid
    created = r.json()["created_at"]
    # 付け直しても 1 件のまま・日時は変わらない
    assert client.put(f"/api/bookmarks/volumes/{vid}").json()["created_at"] == created
    assert client.put(f"/api/bookmarks/works/{wid}").json()["bookmarked"] is True

    assert by_title(client)["single"]["bookmarked"] is True
    assert client.get(f"/api/volumes/{vid}").json()["bookmarked"] is True
    detail = client.get(f"/api/works/{wid}").json()
    assert detail["bookmarked"] is True
    assert all(v["bookmarked"] is False for s in detail["slots"] for v in s["volumes"])
    single_work = client.get(f"/api/works/{vols['single']['work_id']}").json()
    assert single_work["bookmarked"] is False
    assert [v["bookmarked"] for v in single_work["unnumbered"] + [x for s in single_work["slots"] for x in s["volumes"]]
            if v["id"] == vid] == [True]

    items = client.get("/api/bookmarks").json()["items"]
    assert {i["kind"] for i in items} == {"work", "volume"}
    vol_item = next(i for i in items if i["kind"] == "volume")["volume"]
    assert vol_item["id"] == vid and vol_item["offline"] is False and vol_item["work_id"] == vols["single"]["work_id"]
    assert vol_item["work_title"]
    work_item = next(i for i in items if i["kind"] == "work")["work"]
    assert work_item["id"] == wid and work_item["volume_count"] == 1

    r = client.delete(f"/api/bookmarks/volumes/{vid}")
    assert r.status_code == 200 and r.json() == {"volume_id": vid, "bookmarked": False, "created_at": None}
    assert client.delete(f"/api/bookmarks/volumes/{vid}").status_code == 200  # 付いていなくても成功
    assert client.delete(f"/api/bookmarks/works/{wid}").json()["bookmarked"] is False
    assert listed(client) == []


def test_unknown_targets_and_bad_sort(client):
    assert client.put("/api/bookmarks/volumes/99999").status_code == 404
    assert client.put("/api/bookmarks/works/99999").status_code == 404
    assert client.delete("/api/bookmarks/works/99999").status_code == 404
    assert client.get("/api/bookmarks?sort=random").status_code == 422


def test_sort_by_added_and_by_title(make_client, library):
    c, settings = make_client(library)
    c.post("/api/scan")
    vols = by_title(c)
    for name in ("Vol2", "FolderBook", "Vol1"):
        c.put(f"/api/bookmarks/volumes/{vols[name]['id']}")
    set_created(settings, "volume", vols["Vol2"]["id"], "2026-01-01T00:00:00+00:00")
    set_created(settings, "volume", vols["FolderBook"]["id"], "2026-01-03T00:00:00+00:00")
    set_created(settings, "volume", vols["Vol1"]["id"], "2026-01-02T00:00:00+00:00")
    assert listed(c) == [("volume", "FolderBook"), ("volume", "Vol1"), ("volume", "Vol2")]
    by_name = listed(c, "title")
    # 作品名の順で、同じ作品の巻は巻番号の順
    assert by_name.index(("volume", "Vol1")) + 1 == by_name.index(("volume", "Vol2"))


def test_offline_volume_keeps_bookmark_and_is_listed(make_client, library):
    c, _ = make_client(library)
    c.post("/api/scan")
    vols = by_title(c)
    vid, wid = vols["single"]["id"], vols["single"]["work_id"]
    c.put(f"/api/bookmarks/volumes/{vid}")
    c.put(f"/api/bookmarks/works/{wid}")
    moved = library.with_name("unplugged")
    library.rename(moved)
    c.post("/api/scan")
    items = c.get("/api/bookmarks").json()["items"]
    vol = next(i["volume"] for i in items if i["kind"] == "volume")
    assert vol["id"] == vid and vol["offline"] is True
    assert any(i["kind"] == "work" and i["work"]["id"] == wid for i in items)
    # オフラインでも外せる・付け直せる(ファイルは開かない)
    assert c.delete(f"/api/bookmarks/volumes/{vid}").status_code == 200
    assert c.put(f"/api/bookmarks/volumes/{vid}").status_code == 200
    moved.rename(library)
    c.post("/api/scan")
    assert by_title(c)["single"]["bookmarked"] is True


def test_work_merge_moves_bookmark_to_survivor(make_client, library):
    c, settings = make_client(library)
    c.post("/api/scan")
    vols = by_title(c)
    keep, drop = vols["FolderBook"]["work_id"], vols["single"]["work_id"]
    assert keep != drop
    c.put(f"/api/bookmarks/works/{drop}")
    r = c.post(f"/api/works/{keep}/merge", json={"other_id": drop})
    assert r.status_code == 200 and r.json()["bookmarked"] is True
    works = [i["work"]["id"] for i in c.get("/api/bookmarks").json()["items"] if i["kind"] == "work"]
    assert works == [keep]
    # 吸収された作品の id で付け外しすると吸収先が対象になる
    assert c.delete(f"/api/bookmarks/works/{drop}").json() == {"work_id": keep, "bookmarked": False, "created_at": None}


def test_work_merge_keeps_one_bookmark_with_earlier_date(make_client, library):
    c, settings = make_client(library)
    c.post("/api/scan")
    vols = by_title(c)
    keep, drop = vols["FolderBook"]["work_id"], vols["single"]["work_id"]
    c.put(f"/api/bookmarks/works/{keep}")
    c.put(f"/api/bookmarks/works/{drop}")
    set_created(settings, "work", keep, "2026-02-01T00:00:00+00:00")
    set_created(settings, "work", drop, "2026-01-01T00:00:00+00:00")
    c.post(f"/api/works/{keep}/merge", json={"other_id": drop})
    items = [i for i in c.get("/api/bookmarks").json()["items"] if i["kind"] == "work"]
    assert [(i["work"]["id"], i["created_at"]) for i in items] == [(keep, "2026-01-01T00:00:00+00:00")]


def test_cbz_organize_moves_bookmark_and_undo_returns_it(client):
    old_id = by_title(client)["Vol1"]["id"]
    client.put(f"/api/bookmarks/volumes/{old_id}")
    created = client.get("/api/bookmarks").json()["items"][0]["created_at"]
    plan = client.post("/api/organize/plan", json={"volume_id": old_id}).json()
    done = client.post(f"/api/organize/{plan['op_id']}/execute").json()
    assert done["state"] == "done"
    new_id = next(v["new_volume_id"] for v in done["volumes"] if v["volume_id"] == old_id)
    items = client.get("/api/bookmarks").json()["items"]
    assert [(i["volume"]["id"], i["created_at"]) for i in items] == [(new_id, created)]
    assert by_title(client)["Vol1"]["bookmarked"] is True

    undone = client.post(f"/api/organize/{plan['op_id']}/undo").json()
    assert undone["state"] == "undone"
    items = client.get("/api/bookmarks").json()["items"]
    assert [(i["volume"]["id"], i["created_at"]) for i in items] == [(old_id, created)]


def test_bookmark_removed_after_organize_stays_removed_on_undo(client):
    old_id = by_title(client)["Vol1"]["id"]
    client.put(f"/api/bookmarks/volumes/{old_id}")
    plan = client.post("/api/organize/plan", json={"volume_id": old_id}).json()
    done = client.post(f"/api/organize/{plan['op_id']}/execute").json()
    new_id = next(v["new_volume_id"] for v in done["volumes"] if v["volume_id"] == old_id)
    client.delete(f"/api/bookmarks/volumes/{new_id}")
    client.post(f"/api/organize/{plan['op_id']}/undo")
    assert listed(client) == []
    assert by_title(client)["Vol1"]["bookmarked"] is False


def test_rename_organize_keeps_bookmark_on_same_volume(make_client, tmp_path):
    root = tmp_path / "lib"
    title = "ある作品"
    write_zip(root / "受け入れ" / f"{title} 1巻.cbz", [("001.jpg", page_bytes("v1/1")), ("002.jpg", page_bytes("v1/2"))])
    c, _ = make_client(root)
    c.post("/api/scan")
    v = by_title(c)[f"{title} 1巻"]
    c.put(f"/api/bookmarks/volumes/{v['id']}")
    plan = c.post("/api/organize/rename-plan", json={"work_id": v["work_id"]}).json()
    assert plan["state"] == "planned"
    assert c.post(f"/api/organize/{plan['op_id']}/execute").json()["state"] == "done"
    items = c.get("/api/bookmarks").json()["items"]
    assert [(i["volume"]["id"], i["volume"]["title"]) for i in items] == [(v["id"], f"{title} 第01巻")]
    assert c.post(f"/api/organize/{plan['op_id']}/undo").json()["state"] == "undone"
    assert listed(c) == [("volume", f"{title} 1巻")]


def test_split_parent_bookmark_stays_listed(client):
    parent = by_title(client)["inner1"]["id"]
    client.put(f"/api/bookmarks/volumes/{parent}")
    client.post(f"/api/volumes/{parent}/analyze")
    assert [i["volume"]["id"] for i in client.get("/api/bookmarks").json()["items"]] == [parent]
