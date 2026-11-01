import pytest
from fastapi.testclient import TestClient

from conftest import page_bytes, snapshot, write_zip, zip_bytes
from spinebox.app import create_app


@pytest.fixture
def make_client(make_settings):
    def make(*roots, frontend_dist=None, **kw):
        settings = make_settings(*roots, **kw)
        return TestClient(create_app(settings, frontend_dist=frontend_dist)), settings
    return make


@pytest.fixture
def client(make_client, library):
    c, _ = make_client(library)
    assert c.post("/api/scan").status_code == 200
    return c


def by_title(client):
    return {v["title"]: v for v in client.get("/api/volumes").json()}


def test_scan_and_list(make_client, library):
    c, _ = make_client(library)
    assert c.post("/api/scan").json() == {"volumes": 7, "offline_roots": []}
    vols = by_title(c)
    assert set(vols) == {"FolderBook", "single", "Vol1", "Vol2", "inner1", "inner2", "inner3"}
    v = vols["Vol1"]
    assert v == {"id": v["id"], "title": "Vol1", "source_name": "multi.zip", "page_count": 2,
                 "offline": False, "progress": None, "parent_id": None, "parent_title": None,
                 "volume_number": {"kind": "volume", "start": 1, "end": 1, "evidence": "Vol1"},
                 "findings": [], "duplicate_count": 0, "can_analyze": False}
    assert vols["inner1"]["page_count"] is None
    assert vols["inner1"]["can_analyze"] is True
    assert vols["inner1"]["source_name"] == "nested.zip"


def test_volume_pages_in_order(client):
    vols = by_title(client)
    detail = client.get(f"/api/volumes/{vols['FolderBook']['id']}").json()
    assert [p["name"] for p in detail["pages"]] == ["cover.JPG", "1.jpg", "2.jpg", "10.jpg"]
    assert [p["index"] for p in detail["pages"]] == [0, 1, 2, 3]
    assert detail["page_count"] == 4 and detail["progress"] is None
    detail = client.get(f"/api/volumes/{vols['single']['id']}").json()
    assert [p["name"] for p in detail["pages"]] == ["1.jpg", "2.jpg", "10.jpg"]


def test_page_bytes_and_cache_headers(client):
    vid = by_title(client)["Vol1"]["id"]
    r = client.get(f"/api/volumes/{vid}/pages/1")
    assert r.status_code == 200
    assert r.content == page_bytes("v1/002")
    assert r.headers["content-type"] == "image/jpeg"
    assert "max-age" in r.headers["cache-control"]
    etag = r.headers["etag"]
    r2 = client.get(f"/api/volumes/{vid}/pages/1", headers={"If-None-Match": etag})
    assert r2.status_code == 304
    assert client.get(f"/api/volumes/{vid}/pages/2").status_code == 404
    assert client.get("/api/volumes/99999").status_code == 404


def test_inner_archive_opened_on_demand(make_client, library):
    c, settings = make_client(library)
    c.post("/api/scan")
    cache_files = lambda: [p for p in settings.cache_dir.iterdir()]  # noqa: E731
    assert cache_files() == []
    vid = by_title(c)["inner1"]["id"]
    detail = c.get(f"/api/volumes/{vid}").json()
    assert [p["name"] for p in detail["pages"]] == ["DLRAW.TO_cover.jpg", "9.png", "10.png"]
    assert c.get(f"/api/volumes/{vid}/pages/2").content == page_bytes("inner1/10.png")
    assert c.get(f"/api/volumes/{vid}/pages/2").headers["content-type"] == "image/png"
    assert by_title(c)["inner1"]["page_count"] == 3
    (cached,) = cache_files()
    assert len(cached.stem) == 64  # ハッシュ名。元の名前・階層を再現しない


def test_unsupported_inner_archive_is_explicit(make_client, library):
    c, settings = make_client(library)
    c.post("/api/scan")
    r = c.get(f"/api/volumes/{by_title(c)['inner3']['id']}")
    assert r.status_code == 422 and "未対応" in r.json()["detail"]
    assert list(settings.cache_dir.iterdir()) == []  # 取り出す前に判定する


def test_progress_roundtrip(client):
    vid = by_title(client)["single"]["id"]
    r = client.put(f"/api/volumes/{vid}/progress", json={"page": 2, "finished": False})
    assert r.status_code == 200 and r.json() == {"page": 2, "finished": False}
    assert client.get(f"/api/volumes/{vid}").json()["progress"] == {"page": 2, "finished": False}
    assert by_title(client)["single"]["progress"] == {"page": 2, "finished": False}
    client.put(f"/api/volumes/{vid}/progress", json={"page": 2, "finished": True})
    assert by_title(client)["single"]["progress"] == {"page": 2, "finished": True}


@pytest.mark.parametrize("body", [{"page": 3, "finished": False}, {"page": -1, "finished": False},
                                  {"page": "1", "finished": False}, {"page": 1}])
def test_progress_validation(client, body):
    vid = by_title(client)["single"]["id"]
    assert client.put(f"/api/volumes/{vid}/progress", json=body).status_code == 422


def test_progress_unknown_volume(client):
    assert client.put("/api/volumes/99999/progress", json={"page": 0, "finished": False}).status_code == 404


def test_offline_volumes_are_409_and_progress_survives(make_client, library):
    c, _ = make_client(library)
    c.post("/api/scan")
    vid = by_title(c)["single"]["id"]
    c.put(f"/api/volumes/{vid}/progress", json={"page": 1, "finished": False})
    moved = library.with_name("unplugged")
    library.rename(moved)
    # 再スキャン前でも、ルートが見えなければ 409
    assert c.get(f"/api/volumes/{vid}").status_code == 409
    assert c.post("/api/scan").json() == {"volumes": 7, "offline_roots": [str(library)]}
    v = by_title(c)["single"]
    assert v["offline"] is True and v["progress"] == {"page": 1, "finished": False}
    assert c.get(f"/api/volumes/{vid}").status_code == 409
    assert c.get(f"/api/volumes/{vid}/pages/0").status_code == 409
    r = c.put(f"/api/volumes/{vid}/progress", json={"page": 0, "finished": False})
    assert r.status_code == 409 and r.json()["offline"] is True
    moved.rename(library)
    c.post("/api/scan")
    assert by_title(c)["single"]["offline"] is False
    assert c.get(f"/api/volumes/{vid}").json()["progress"] == {"page": 1, "finished": False}


def test_library_bytes_unchanged_by_scan_and_full_read(make_client, library):
    before = snapshot(library)
    c, _ = make_client(library)
    c.post("/api/scan")
    read = 0
    for v in c.get("/api/volumes").json():
        r = c.get(f"/api/volumes/{v['id']}")
        if r.status_code != 200:
            assert v["title"] == "inner3"  # rar の内側は未対応
            continue
        for p in r.json()["pages"]:
            page = c.get(f"/api/volumes/{v['id']}/pages/{p['index']}")
            assert page.status_code == 200
            read += 1
        c.put(f"/api/volumes/{v['id']}/progress", json={"page": 0, "finished": True})
    c.post("/api/scan")
    assert read == 4 + 3 + 2 + 1 + 3 + 2
    assert snapshot(library) == before  # バイト列・サイズ・mtime が全ファイルで同じ、ファイルの増減も無い


def test_cache_limit_evicts_oldest_inner_archive(make_client, tmp_path):
    lib = tmp_path / "lib"
    inners = [(f"in{i}.zip", zip_bytes([(f"{n}.jpg", b"x" * 300) for n in range(3)])) for i in range(3)]
    write_zip(lib / "box.zip", inners)
    size = len(inners[0][1])
    c, settings = make_client(lib, cache_limit_bytes=size * 2 + 10)
    c.post("/api/scan")
    vols = by_title(c)
    for name in ["in0", "in1", "in0", "in2"]:  # in0 を使い直したので、最古は in1
        assert c.get(f"/api/volumes/{vols[name]['id']}/pages/0").status_code == 200
    assert len(list(settings.cache_dir.iterdir())) == 2
    assert c.app.state.cache.total_bytes <= settings.cache_limit_bytes
    # 消えた in1 も、開けば作り直される
    assert c.get(f"/api/volumes/{vols['in1']['id']}/pages/0").content == b"x" * 300


def test_frontend_dist_is_served_with_spa_fallback(make_client, library, tmp_path):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>spinebox</html>", encoding="utf-8")
    (dist / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("no", encoding="utf-8")
    c, _ = make_client(library, frontend_dist=dist)
    assert "spinebox" in c.get("/").text
    assert c.get("/assets/app.js").text == "console.log(1)"
    assert "spinebox" in c.get("/volumes/3").text          # SPA のルーティング
    assert "spinebox" in c.get("/..%2Fsecret.txt").text    # dist の外は返さない
    assert c.get("/api/unknown").status_code == 404
    assert c.get("/api/volumes").status_code == 200


def test_healthz_reports_ok_and_commit(make_client, library, monkeypatch):
    monkeypatch.setattr("spinebox.app.current_commit", lambda root: "abc1234")
    c, _ = make_client(library)
    r = c.get("/healthz")
    assert r.status_code == 200 and r.json() == {"ok": True, "commit": "abc1234"}


def test_healthz_commit_is_null_when_unknown(make_client, library, monkeypatch):
    monkeypatch.setattr("spinebox.app.current_commit", lambda root: None)
    c, _ = make_client(library)
    assert c.get("/healthz").json() == {"ok": True, "commit": None}


def test_healthz_is_not_shadowed_by_spa_and_is_503_without_db(make_client, library, tmp_path, monkeypatch):
    monkeypatch.setattr("spinebox.app.current_commit", lambda root: "abc1234")
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html>spinebox</html>", encoding="utf-8")
    c, settings = make_client(library, frontend_dist=dist)
    assert c.get("/healthz").json()["ok"] is True
    # DB が開けなくなったら 200 で隠さない(ファイルの場所をフォルダで塞いで接続を失敗させる)
    for suffix in ("", "-wal", "-shm"):
        settings.db_path.with_name(settings.db_path.name + suffix).unlink(missing_ok=True)
    settings.db_path.mkdir()
    r = c.get("/healthz")
    assert r.status_code == 503 and r.json()["ok"] is False


def test_spa_fallback_serves_deep_links_but_not_unknown_api(make_client, library, tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html>spinebox</html>", encoding="utf-8")
    c, _ = make_client(library, frontend_dist=dist)
    for path in ("/read/3", "/read/3/", "/some/deep/link"):
        r = c.get(path)
        assert r.status_code == 200 and "spinebox" in r.text, path
    assert c.get("/api/nothing").status_code == 404


def test_without_frontend_dist_only_api(make_client, library, tmp_path):
    c, _ = make_client(library, frontend_dist=tmp_path / "missing")
    assert c.get("/").status_code == 404
    assert c.get("/api/volumes").json() == []


def test_archive_broken_after_scan_is_422(make_client, library):
    c, _ = make_client(library)
    c.post("/api/scan")
    vid = by_title(c)["single"]["id"]
    (library / "single.cbz").write_bytes(b"broken now")
    r = c.get(f"/api/volumes/{vid}")
    assert r.status_code == 422 and "読めません" in r.json()["detail"]


def test_inner_archive_larger_than_cache_is_422(make_client, library):
    c, settings = make_client(library, cache_limit_bytes=10)
    c.post("/api/scan")
    r = c.get(f"/api/volumes/{by_title(c)['inner1']['id']}")
    assert r.status_code == 422
    assert list(settings.cache_dir.iterdir()) == []


def test_page_over_entry_limit_is_422(make_client, library):
    c, _ = make_client(library, max_entry_bytes=5)
    c.post("/api/scan")
    for title in ["FolderBook", "single"]:
        assert c.get(f"/api/volumes/{by_title(c)[title]['id']}/pages/0").status_code == 422


def test_concurrent_scan_is_409(client):
    lock = client.app.state.scan_lock
    assert lock.acquire(blocking=False)
    try:
        assert client.post("/api/scan").status_code == 409
    finally:
        lock.release()
    assert client.post("/api/scan").status_code == 200
