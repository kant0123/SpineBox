"""蔵書(グループ)と蔵書フォルダの管理(#313)。蔵書は合成データ。"""
import os
import threading

import pytest
from fastapi.testclient import TestClient

from conftest import page_bytes, snapshot, write_zip
from spinebox import db, libraries
from spinebox.app import create_app
from spinebox.config import InboxSetting
from spinebox.scanner import Scanner


def cbz(folder, name, pages=2):
    write_zip(folder / name, [(f"{i:03d}.jpg", page_bytes(f"{name}/{i}")) for i in range(pages)])


@pytest.fixture
def dirs(tmp_path):
    """本 2 冊の a、本 1 冊の b、空の c。"""
    a, b, c = tmp_path / "本", tmp_path / "追加" / "本", tmp_path / "同人"
    for d in (a, b, c):
        d.mkdir(parents=True)
    cbz(a, "作品A 第01巻.cbz")
    cbz(a, "作品A 第02巻.cbz")
    cbz(b, "作品B 第01巻.cbz", pages=3)
    return a, b, c


@pytest.fixture
def conn(make_settings):
    c = db.open_db(make_settings().db_path)
    yield c
    c.close()


def client_for(settings):
    return TestClient(create_app(settings, frontend_dist=None))


def groups(client):
    return {g["name"]: g for g in client.get("/api/libraries").json()}


# --- 移行・設定からの取り込み --------------------------------------------------------------------

def test_migration_18_gives_each_existing_folder_its_own_group(tmp_path, make_settings):
    """版 17 の DB(1 蔵書 = 1 フォルダ)を開くと、有効な蔵書フォルダがそれぞれ同名の蔵書になる。
    同じフォルダ名は連番で分ける。外してあったフォルダ・受け入れフォルダは蔵書に入れない。巻・読書位置は残る。"""
    settings = make_settings()
    conn = db.connect(settings.db_path)
    with db.transaction(conn) as c:
        for migration in db.MIGRATIONS[:17]:
            for statement in migration:
                c.execute(statement)
        c.execute("PRAGMA user_version = 17")
        c.executemany("INSERT INTO library_roots (id, path, online, enabled, inbox) VALUES (?, ?, 1, ?, ?)", [
            (1, "D:\\本", 1, 0), (2, "E:\\同人", 1, 0), (3, "F:\\backup\\本", 1, 0), (4, "G:\\旧", 0, 0),
            (5, "D:\\受け入れ", 1, 1)])
        c.execute("INSERT INTO sources (id, root_id, rel_path, kind) VALUES (1, 1, 'a.cbz', 'archive')")
        c.execute("INSERT INTO volumes (id, source_id, entry_prefix, title, created_at, updated_at) "
                  "VALUES (7, 1, '', 'a', 't', 't')")
        c.execute("INSERT INTO reading_progress (volume_id, page, finished, updated_at) VALUES (7, 5, 0, 't')")
    conn.close()

    client = client_for(settings)
    libs = client.get("/api/libraries").json()
    assert [(g["name"], [f["path"] for f in g["folders"]]) for g in libs] == [
        ("本", ["D:\\本"]), ("同人", ["E:\\同人"]), ("本 (2)", ["F:\\backup\\本"])]
    assert libs[0]["volume_count"] == 1
    conn = db.connect(settings.db_path)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == len(db.MIGRATIONS) >= 18
        assert [r[0] for r in conn.execute("SELECT group_id FROM library_roots WHERE id IN (4, 5) ORDER BY id")] == [None, None]
        assert conn.execute("SELECT page FROM reading_progress WHERE volume_id = 7").fetchone()[0] == 5
    finally:
        conn.close()


def test_sync_is_idempotent_and_writes_nothing_the_second_time(conn, dirs, make_settings):
    a, b, _ = dirs
    settings = make_settings(a, b)
    first = libraries.sync(conn, settings)
    assert [str(p) for _, p in first] == [str(a), str(b)]
    before = conn.total_changes
    assert libraries.sync(conn, settings) == first
    assert conn.total_changes == before
    assert [g["name"] for g in libraries.list_groups(conn)] == ["本", "本 (2)"]


def test_startup_imports_config_roots_before_any_scan(dirs, make_settings):
    a, _, c = dirs
    client = client_for(make_settings(a, c))
    libs = groups(client)
    assert set(libs) == {"本", "同人"} and libs["本"]["volume_count"] == 0 and libs["本"]["online"] is False


def test_inbox_folder_never_joins_a_group(conn, dirs, make_settings):
    """蔵書フォルダだったパスを設定で受け入れフォルダへ付け替えると、蔵書からは外れる(受け入れは設定が正)。"""
    import dataclasses
    a, b, _ = dirs
    libraries.sync(conn, make_settings(a, b))
    settings = dataclasses.replace(make_settings(a), inboxes=(InboxSetting(b, a),))
    Scanner(conn, settings).scan_inbox()
    Scanner(conn, settings).scan()
    row = conn.execute("SELECT inbox, enabled, group_id FROM library_roots WHERE path = ?", (str(b),)).fetchone()
    assert tuple(row) == (1, 1, None)
    assert [[f["path"] for f in g["folders"]] for g in libraries.list_groups(conn)] == [[str(a)], []]


# --- 蔵書(グループ) ---------------------------------------------------------------------------

def test_group_crud(dirs, make_settings):
    a, _, _ = dirs
    client = client_for(make_settings(a))
    made = client.post("/api/library-groups", json={"name": "  同人  "})
    assert made.status_code == 201 and made.json() == {
        "id": made.json()["id"], "name": "同人", "online": True, "volume_count": 0, "folders": []}
    gid = made.json()["id"]
    assert client.post("/api/library-groups", json={"name": "同人"}).status_code == 409
    assert client.post("/api/library-groups", json={"name": "   "}).status_code == 422
    assert client.post("/api/library-groups", json={"name": "あ" * 101}).status_code == 422
    assert client.post("/api/library-groups", json={"name": 3}).status_code == 422

    assert client.patch(f"/api/library-groups/{gid}", json={"name": "薄い本"}).json()["name"] == "薄い本"
    assert client.patch(f"/api/library-groups/{gid}", json={"name": "薄い本"}).status_code == 200  # 同じ名前のまま
    assert client.patch(f"/api/library-groups/{gid}", json={"name": "本"}).status_code == 409
    assert client.patch("/api/library-groups/9999", json={"name": "x"}).status_code == 404

    # フォルダが残っている蔵書は消せない。空なら消せる
    hon = groups(client)["本"]["id"]
    r = client.delete(f"/api/library-groups/{hon}")
    assert r.status_code == 409 and "フォルダ" in r.json()["detail"]
    assert client.delete(f"/api/library-groups/{gid}").status_code == 204
    assert client.delete(f"/api/library-groups/{gid}").status_code == 404
    assert set(groups(client)) == {"本"}


# --- 蔵書フォルダ ------------------------------------------------------------------------------

def test_two_folders_in_one_group_are_counted_and_filtered_together(dirs, make_settings):
    a, b, c = dirs
    client = client_for(make_settings(a, c))
    hon = groups(client)["本"]["id"]
    added = client.post("/api/library-folders", json={"path": str(b), "group_id": hon})
    assert added.status_code == 201
    assert [f["path"] for f in added.json()["folders"]] == [str(a), str(b)]
    assert added.json()["folders"][1]["online"] is True
    assert client.post("/api/scan").status_code == 200

    libs = groups(client)
    assert set(libs) == {"本", "同人"}
    assert libs["本"]["volume_count"] == 3 and [f["volume_count"] for f in libs["本"]["folders"]] == [2, 1]
    assert libs["同人"]["volume_count"] == 0
    assert {w["title"] for w in client.get("/api/works", params={"library": hon}).json()["works"]} == {"作品A", "作品B"}
    assert len(client.get("/api/volumes", params={"library": hon}).json()) == 3
    assert client.get("/api/stats", params={"library": hon}).json()["volumes"]["total"] == 3
    assert client.get("/api/works", params={"library": libs["同人"]["id"]}).json()["works"] == []


def test_first_folder_without_any_group_creates_the_default_group(dirs, make_settings):
    a, b, _ = dirs
    client = client_for(make_settings())
    assert client.get("/api/libraries").json() == []
    first = client.post("/api/library-folders", json={"path": str(a)})
    assert first.status_code == 201 and first.json()["name"] == libraries.DEFAULT_GROUP_NAME
    # 蔵書があるのに省いたら、勝手に選ばない
    second = client.post("/api/library-folders", json={"path": str(b)})
    assert second.status_code == 422 and "蔵書を選んで" in second.json()["detail"]
    assert client.post("/api/library-folders", json={"path": str(b), "group_id": 9999}).status_code == 404
    assert [len(g["folders"]) for g in client.get("/api/libraries").json()] == [1]


def test_removed_folder_keeps_records_and_comes_back_with_progress(dirs, make_settings):
    """フォルダを外しても行・ソース・巻・読書位置は消えず、同じパスを足し直すとそのまま戻る。"""
    a, _, c = dirs
    settings = make_settings(a, c)
    client = client_for(settings)
    client.post("/api/scan")
    vid = client.get("/api/volumes").json()[0]["id"]
    client.put(f"/api/volumes/{vid}/progress", json={"page": 1, "finished": False})
    folder = groups(client)["本"]["folders"][0]["id"]
    before = snapshot(a)

    assert client.delete(f"/api/library-folders/{folder}").status_code == 204
    assert client.delete(f"/api/library-folders/{folder}").status_code == 404
    assert groups(client)["本"]["folders"] == [] and client.get("/api/volumes").json() == []
    assert client.get("/api/works").json()["works"] == []
    assert client.post("/api/scan").json()["volumes"] == 0  # 設定に残っていても戻らない
    conn = db.connect(settings.db_path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM volumes WHERE missing = 0").fetchone()[0] == 2
        assert conn.execute("SELECT page FROM reading_progress WHERE volume_id = ?", (vid,)).fetchone()[0] == 1
    finally:
        conn.close()

    # 別の蔵書へ足し直す(大文字小文字が違っても同じ行に戻る)。スキャンの前から巻が見える
    dojin = groups(client)["同人"]["id"]
    back = client.post("/api/library-folders", json={"path": str(a).upper() if os.name == "nt" else str(a),
                                                     "group_id": dojin})
    assert back.status_code == 201 and folder in [f["id"] for f in back.json()["folders"]]
    assert back.json()["volume_count"] == 2
    vols = client.get("/api/volumes", params={"library": dojin}).json()
    assert {v["id"] for v in vols} >= {vid} and len(vols) == 2
    assert [w["title"] for w in client.get("/api/works").json()["works"]] == ["作品A"]
    assert snapshot(a) == before  # 蔵書には何も書いていない


def test_move_folder_between_groups(dirs, make_settings):
    a, _, c = dirs
    client = client_for(make_settings(a, c))
    client.post("/api/scan")
    libs = groups(client)
    folder = libs["本"]["folders"][0]["id"]
    moved = client.patch(f"/api/library-folders/{folder}", json={"group_id": libs["同人"]["id"]})
    assert moved.status_code == 200 and len(moved.json()["folders"]) == 2 and moved.json()["volume_count"] == 2
    assert groups(client)["本"]["folders"] == []
    assert client.patch(f"/api/library-folders/{folder}", json={"group_id": 9999}).status_code == 404
    assert client.patch("/api/library-folders/9999", json={"group_id": libs["本"]["id"]}).status_code == 404
    assert client.delete(f"/api/library-groups/{libs['本']['id']}").status_code == 204


def test_add_folder_rejects_bad_paths(dirs, tmp_path, make_settings):
    import dataclasses
    a, b, c = dirs
    inbox = tmp_path / "受け入れ"
    inbox.mkdir()
    (a / "sub").mkdir()
    settings = dataclasses.replace(make_settings(a), inboxes=(InboxSetting(inbox, a),))
    client = client_for(settings)
    hon = groups(client)["本"]["id"]

    def add(path):
        return client.post("/api/library-folders", json={"path": str(path), "group_id": hon})

    assert add(a).status_code == 409                                  # 登録済み
    for path, word in [("", "入力"), ("本", "絶対パス"), (tmp_path / "無い", "見つからない"),
                       (a / "作品A 第01巻.cbz", "見つからない"),      # ファイル
                       (a / "sub", "重なって"), (tmp_path, "重なって"),  # 蔵書フォルダの中・外側
                       (inbox, "受け入れフォルダ"), (settings.data_dir, "データの置き場")]:
        r = add(path)
        assert r.status_code == 422 and word in r.json()["detail"], (path, r.json())
    assert client.post("/api/library-folders", json={"path": 3}).status_code == 422
    assert [f["path"] for f in groups(client)["本"]["folders"]] == [str(a)]
    assert add(b).status_code == 201 and add(c).status_code == 201


@pytest.mark.skipif(os.name != "nt", reason="大文字小文字を区別しないファイルシステムの話")
def test_added_path_is_stored_in_its_disk_spelling(tmp_path, make_settings):
    target = tmp_path / "Books" / "Manga"
    target.mkdir(parents=True)
    client = client_for(make_settings())
    typed = str(tmp_path / "books" / "MANGA") + "\\"
    assert client.post("/api/library-folders", json={"path": typed}).json()["folders"][0]["path"] == str(target)


def test_folder_changes_are_refused_while_a_job_runs(dirs, make_settings):
    a, b, _ = dirs
    app = create_app(make_settings(a), frontend_dist=None)
    client = TestClient(app)
    libs = groups(client)
    with app.state.scan_lock:
        result: dict = {}

        def call():
            result["add"] = client.post("/api/library-folders", json={"path": str(b), "group_id": libs["本"]["id"]})
            result["remove"] = client.delete(f"/api/library-folders/{libs['本']['folders'][0]['id']}")
        t = threading.Thread(target=call)
        t.start()
        t.join(30)
    assert result["add"].status_code == 409 and result["remove"].status_code == 409
    assert len(groups(client)["本"]["folders"]) == 1
    assert client.post("/api/library-folders", json={"path": str(b), "group_id": libs["本"]["id"]}).status_code == 201


def test_scan_skips_the_organize_dir_of_a_folder_added_on_screen(dirs, make_settings):
    """画面から足したフォルダ(設定に無い)でも、整理の作業場・退避フォルダは本として読まない。"""
    a, b, _ = dirs
    client = client_for(make_settings(a))
    client.post("/api/library-folders", json={"path": str(b), "group_id": groups(client)["本"]["id"]})
    trash = b / ".spinebox" / "trash" / "1"
    trash.mkdir(parents=True)
    cbz(trash, "退避した本.cbz")
    assert client.post("/api/scan").json()["volumes"] == 3


# --- サーバー機のフォルダの一覧 -------------------------------------------------------------------

def test_fs_folders_lists_only_directories_and_never_writes(dirs, tmp_path):
    a, _, _ = dirs
    (tmp_path / "file.txt").write_text("x")
    (tmp_path / "10").mkdir()
    (tmp_path / "9").mkdir()
    before = snapshot(tmp_path)
    client = client_for_empty(tmp_path)
    body = client.get("/api/fs/folders", params={"path": str(tmp_path)}).json()
    names = [f["name"] for f in body["folders"]]
    assert "file.txt" not in names and {"本", "同人", "追加", "9", "10"} <= set(names)
    assert names.index("9") < names.index("10")  # 自然順
    assert body["path"] == str(tmp_path) and body["parent"] == str(tmp_path.parent)
    assert next(f for f in body["folders"] if f["name"] == "本")["path"] == str(a)
    # data/ は client が作るので、それ以外に増減が無い
    after = snapshot(tmp_path)
    assert {k: v for k, v in after.items() if not k.startswith("data")} == \
           {k: v for k, v in before.items() if not k.startswith("data")}


def client_for_empty(tmp_path):
    from spinebox.config import Settings
    return client_for(Settings(data_dir=tmp_path / "data"))


def test_fs_folders_top_level_and_errors(tmp_path):
    client = client_for_empty(tmp_path)
    top = client.get("/api/fs/folders").json()
    assert top["path"] == "" and top["parent"] is None and top["folders"]
    assert all(os.path.isabs(f["path"]) for f in top["folders"])
    first = top["folders"][0]["path"]
    assert client.get("/api/fs/folders", params={"path": first}).json()["parent"] == ""  # ドライブの上は一覧
    assert client.get("/api/fs/folders", params={"path": "relative/path"}).status_code == 422
    assert client.get("/api/fs/folders", params={"path": str(tmp_path / "無い")}).status_code == 404
    f = tmp_path / "f.txt"
    f.write_text("x")
    assert client.get("/api/fs/folders", params={"path": str(f)}).status_code == 404


def test_list_folders_skips_unreadable_entries(tmp_path, monkeypatch):
    (tmp_path / "ok").mkdir()
    (tmp_path / "bad").mkdir()
    real = os.scandir

    class Entry:
        def __init__(self, e):
            self.name = e.name
            self._e = e

        def is_dir(self):
            if self.name == "bad":
                raise OSError("denied")
            return self._e.is_dir()

    class Scan:
        def __init__(self, path):
            self._it = real(path)

        def __enter__(self):
            return (Entry(e) for e in self._it)

        def __exit__(self, *exc):
            self._it.close()

    monkeypatch.setattr(libraries.os, "scandir", Scan)
    assert [f["name"] for f in libraries.list_folders(str(tmp_path))["folders"]] == ["ok"]
