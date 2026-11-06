"""後で読む(#173): 付け外し・一覧の絞り込みと、作品の統合での引き継ぎ。"""
import pytest
from fastapi.testclient import TestClient

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


def test_toggle_work_and_list_flags(client):
    vols = {v["title"]: v for v in client.get("/api/volumes").json()}
    wid = vols["single"]["work_id"]
    listed = {w["title"]: w for w in client.get("/api/works").json()["works"]}
    assert listed["single"]["read_later"] is False

    r = client.put(f"/api/read-later/works/{wid}")
    assert r.status_code == 200 and r.json()["read_later"] is True and r.json()["work_id"] == wid
    created = r.json()["created_at"]
    # 付け直しても日時は変わらない
    assert client.put(f"/api/read-later/works/{wid}").json()["created_at"] == created
    assert client.get(f"/api/works/{wid}").json()["read_later"] is True
    assert [w["title"] for w in client.get("/api/works?read_later=1").json()["works"]] == ["single"]

    r = client.delete(f"/api/read-later/works/{wid}")
    assert r.status_code == 200 and r.json() == {"work_id": wid, "read_later": False, "created_at": None}
    # 付いていなくても成功
    assert client.delete(f"/api/read-later/works/{wid}").status_code == 200
    assert client.get("/api/works?read_later=1").json()["works"] == []


def test_unknown_work_is_404(client):
    assert client.put("/api/read-later/works/999999").status_code == 404


def test_merge_moves_read_later(make_client, library):
    c, _ = make_client(library)
    assert c.post("/api/scan").status_code == 200
    vols = {v["title"]: v for v in c.get("/api/volumes").json()}
    keep = vols["FolderBook"]["work_id"]
    drop = vols["single"]["work_id"]
    assert c.put(f"/api/read-later/works/{drop}").status_code == 200
    # 手動統合。keep に drop を統合する
    merged = c.post(f"/api/works/{keep}/merge", json={"other_id": drop})
    assert merged.status_code == 200, merged.text
    # 吸収先で印が付いている
    assert [w["title"] for w in c.get("/api/works?read_later=1").json()["works"]] == ["FolderBook"]
