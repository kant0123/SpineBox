"""7z / rar を蔵書に置いたときのスキャンと閲覧(API 越し)。7z 依存のテストは 7z が無ければ skip する。"""
import pytest
from fastapi.testclient import TestClient

from archives7z import make_7z, rar5_bytes
from conftest import page_bytes, snapshot, write_zip, zip_bytes
from spinebox.app import create_app
from spinebox.archive import sevenzip as sz


@pytest.fixture
def make_client(make_settings):
    def make(*roots, **kw):
        settings = make_settings(*roots, **kw)
        return TestClient(create_app(settings, frontend_dist=None)), settings
    return make


def by_title(client):
    return {v["title"]: v for v in client.get("/api/volumes").json()}


def pages(client, vid):
    detail = client.get(f"/api/volumes/{vid}")
    assert detail.status_code == 200, detail.text
    return [client.get(f"/api/volumes/{vid}/pages/{p['index']}").content for p in detail.json()["pages"]]


def cache_files(settings):
    return list(settings.cache_dir.iterdir())


def sample_rar(label: str) -> bytes:
    return rar5_bytes([(f"{label}/", None)] + [(f"{label}/{n}.jpg", page_bytes(f"{label}/{n}")) for n in (10, 2, 1)])


# --- 7z が無くても走る: 入れ子の深さ -------------------------------------------


def nested_zips(depth: int) -> bytes:
    data = zip_bytes([("p2.jpg", page_bytes(f"d{depth}/2")), ("p1.jpg", page_bytes(f"d{depth}/1"))])
    for d in range(depth - 1, 0, -1):
        data = zip_bytes([(f"c{d}.zip", data), (f"a{d}.jpg", page_bytes(f"d{d}/a")), ("z.txt", b"t")])
    return data


def test_nested_archives_continue_after_the_images_of_each_level(make_client, tmp_path):
    lib = tmp_path / "lib"
    write_zip(lib / "outer.zip", [("matryoshka.zip", nested_zips(3))])
    c, settings = make_client(lib)
    c.post("/api/scan")
    (vol,) = c.get("/api/volumes").json()
    assert [p["name"] for p in c.get(f"/api/volumes/{vol['id']}").json()["pages"]] == [
        "a1.jpg", "a2.jpg", "p1.jpg", "p2.jpg"]
    assert pages(c, vol["id"]) == [page_bytes("d1/a"), page_bytes("d2/a"), page_bytes("d3/1"), page_bytes("d3/2")]
    assert len(cache_files(settings)) == 3  # 各段をハッシュ名で 1 つずつ。元の階層は作らない


def test_nesting_beyond_max_depth_is_skipped_and_recorded(make_client, tmp_path):
    lib = tmp_path / "lib"
    write_zip(lib / "outer.zip", [("matryoshka.zip", nested_zips(3))])
    c, settings = make_client(lib, max_depth=2)
    c.post("/api/scan")
    (vol,) = c.get("/api/volumes").json()
    assert pages(c, vol["id"]) == [page_bytes("d1/a"), page_bytes("d2/a")]
    import sqlite3
    db = sqlite3.connect(settings.db_path)
    reasons = db.execute("SELECT container_path, entry_name, reason FROM scan_issues").fetchall()
    assert any(r[0] == "matryoshka.zip" and "c2.zip" in (r[1] or "") and "入れ子" in r[2] for r in reasons)


def test_unsafe_names_in_nested_archives_are_recorded_with_their_path(make_client, tmp_path):
    inner = zip_bytes([("../evil.jpg", b"x"), ("ok.jpg", page_bytes("ok"))])
    lib = tmp_path / "lib"
    write_zip(lib / "outer.zip", [("mid.zip", zip_bytes([("deep.zip", inner)]))])
    c, settings = make_client(lib)
    c.post("/api/scan")
    (vol,) = c.get("/api/volumes").json()
    assert pages(c, vol["id"]) == [page_bytes("ok")]
    import sqlite3
    ((entry, reason),) = sqlite3.connect(settings.db_path).execute(
        "SELECT entry_name, reason FROM scan_issues WHERE container_path = 'mid.zip'").fetchall()
    assert entry == "deep.zip/../evil.jpg" and ".." in reason


# --- 7z が要る -------------------------------------------------------------------


def test_outer_7z_with_volume_folders_and_inner_archives(sevenzip, make_client, tmp_path):
    lib = tmp_path / "lib"
    make_7z(sevenzip, lib / "[作者] 作品 第01-04巻.7z", {
        "作品/第1巻/001.jpg": page_bytes("f1/1"), "作品/第1巻/002.jpg": page_bytes("f1/2"),
        "作品/第2巻/001.jpg": page_bytes("f2/1"),
        "作品/v3.rar": sample_rar("r3"),
        "作品/v4.zip": zip_bytes([("v4/2.jpg", page_bytes("z4/2")), ("v4/1.jpg", page_bytes("z4/1"))]),
    })
    c, settings = make_client(lib, sevenzip_path=sevenzip)
    assert c.post("/api/scan").json() == {"volumes": 4, "offline_roots": []}
    vols = by_title(c)
    assert set(vols) == {"第1巻", "第2巻", "v3", "v4"}
    assert vols["第1巻"]["page_count"] == 2 and vols["v3"]["page_count"] is None
    assert cache_files(settings) == []  # スキャンは内側アーカイブを取り出さない
    assert pages(c, vols["第1巻"]["id"]) == [page_bytes("f1/1"), page_bytes("f1/2")]
    assert cache_files(settings) == []  # 外側のフォルダ巻はキャッシュを使わない
    assert pages(c, vols["v3"]["id"]) == [page_bytes("r3/1"), page_bytes("r3/2"), page_bytes("r3/10")]
    assert pages(c, vols["v4"]["id"]) == [page_bytes("z4/1"), page_bytes("z4/2")]
    assert len(cache_files(settings)) == 2
    assert by_title(c)["v3"]["page_count"] == 3


def test_scan_lists_7z_and_rar_without_extracting_anything(sevenzip, make_client, tmp_path, monkeypatch):
    lib = tmp_path / "lib"
    make_7z(sevenzip, lib / "a.7z", {"in.rar": sample_rar("x"), "in.zip": zip_bytes([("1.jpg", b"j")])})
    (lib / "b.rar").write_bytes(rar5_bytes([("v/1.jpg", page_bytes("b"))]))
    write_zip(lib / "c.zip", [("in.7z", (lib / "a.7z").read_bytes())])
    monkeypatch.setattr(sz, "_stream", lambda *a: pytest.fail("スキャンが 7z で取り出した"))
    c, settings = make_client(lib, sevenzip_path=sevenzip)
    assert c.post("/api/scan").json()["volumes"] == 4  # a.7z の in.rar / in.zip、b.rar の v、c.zip の in.7z
    assert cache_files(settings) == []


def test_inner_archive_is_extracted_once_and_reused(sevenzip, make_client, tmp_path, monkeypatch):
    lib = tmp_path / "lib"
    make_7z(sevenzip, lib / "a.7z", {"in.rar": sample_rar("x"), "pad.bin": b"p" * 1000})
    c, settings = make_client(lib, sevenzip_path=sevenzip)
    c.post("/api/scan")
    vid = by_title(c)["in"]["id"]
    assert len(pages(c, vid)) == 3
    extracted = []
    real = sz._stream
    monkeypatch.setattr(sz, "_stream", lambda cmd, *a: extracted.append(cmd[-1]) or real(cmd, *a))
    for _ in range(2):
        assert len(pages(c, vid)) == 3
    # 2 回目以降は外側の 7z から内側を取り出さない(ページ画像の取り出しだけ)
    assert all(not name.endswith("in.rar") for name in extracted) and extracted
    assert len(cache_files(settings)) == 1


def test_7z_nested_in_rar_in_7z(sevenzip, make_client, tmp_path):
    """実データと同じ形: 7z の中の rar の中の rar の中の zip。開くと中の巻(v1・v3)に分解される。"""
    inner_zip = zip_bytes([("v1/2.jpg", page_bytes("z/2")), ("v1/1.jpg", page_bytes("z/1"))])
    mid = rar5_bytes([("v1-2.rar", rar5_bytes([("v1.zip", inner_zip)])), ("v3.zip", zip_bytes([("3.jpg", page_bytes("z3"))]))])
    lib = tmp_path / "lib"
    make_7z(sevenzip, lib / "all.7z", {"all/v1-3.rar": mid})
    c, settings = make_client(lib, sevenzip_path=sevenzip)
    assert c.post("/api/scan").json()["volumes"] == 1
    (vol,) = c.get("/api/volumes").json()
    assert pages(c, vol["id"]) == [page_bytes("z/1"), page_bytes("z/2"), page_bytes("z3")]
    children = c.get("/api/volumes").json()
    assert [(v["title"], v["parent_id"], v["volume_number"]["start"]) for v in children] == [
        ("v1", vol["id"], 1), ("v3", vol["id"], 3)]
    assert [pages(c, v["id"]) for v in children] == [[page_bytes("z/1"), page_bytes("z/2")], [page_bytes("z3")]]
    # 中の v1-2.rar には v1.zip しか無い。親の名前「v1-3」に対して 2 巻が欠けている
    assert [[(f["scope"], f["missing"], f["extra"]) for f in v["findings"]] for v in children] == [
        [("volume", [2], [])]] * 2
    # max_depth を絞ると入れ子の途中で止まる(理由が記録される)。中の巻が 1 つになるので分解を取り消す
    c2, _ = make_client(lib, sevenzip_path=sevenzip, max_depth=2)
    c2.post("/api/scan")
    assert pages(c2, vol["id"]) == [page_bytes("z3")]
    assert [v["id"] for v in c2.get("/api/volumes").json()] == [vol["id"]]


def test_broken_7z_after_scan_is_422_and_junk_is_recorded(sevenzip, make_client, tmp_path):
    lib = tmp_path / "lib"
    make_7z(sevenzip, lib / "ok.7z", {"v/1.jpg": page_bytes("ok")})
    (lib / "junk.7z").write_bytes(b"7z\xbc\xaf\x27\x1c junk")
    (lib / "junk2.rar").write_bytes(b"Rar!\x1a\x07\x01\x00 junk")
    c, settings = make_client(lib, sevenzip_path=sevenzip)
    assert c.post("/api/scan").json()["volumes"] == 1
    import sqlite3
    reasons = dict(sqlite3.connect(settings.db_path).execute(
        "SELECT s.rel_path, i.reason FROM scan_issues i JOIN sources s ON s.id = i.source_id").fetchall())
    assert "読めないアーカイブ" in reasons["junk.7z"] and "読めないアーカイブ" in reasons["junk2.rar"]


def test_without_7zip_nested_7z_and_rar_stay_unreadable_but_zip_works(make_client, tmp_path):
    lib = tmp_path / "lib"
    write_zip(lib / "x.zip", [("a.rar", rar5_bytes([("1.jpg", b"j")])), ("b.zip", zip_bytes([("1.jpg", page_bytes("b"))]))])
    (lib / "o.7z").write_bytes(b"7z\xbc\xaf\x27\x1c junk")
    c, settings = make_client(lib)  # 7-Zip なし
    assert c.post("/api/scan").json()["volumes"] == 2
    vols = by_title(c)
    assert pages(c, vols["b"]["id"]) == [page_bytes("b")]
    r = c.get(f"/api/volumes/{vols['a']['id']}")
    assert r.status_code == 422 and "未対応" in r.json()["detail"]
    assert len(cache_files(settings)) == 1  # 開けた b.zip の分だけ。a.rar は取り出す前に未対応と分かる


def test_7z_library_bytes_unchanged_by_scan_and_full_read(sevenzip, make_client, tmp_path):
    lib = tmp_path / "lib"
    make_7z(sevenzip, lib / "a.7z", {"v/1.jpg": page_bytes("1"), "v2.rar": sample_rar("y")})
    (lib / "b.rar").write_bytes(rar5_bytes([("v/1.jpg", page_bytes("b"))]))
    before = snapshot(lib)
    c, _ = make_client(lib, sevenzip_path=sevenzip)
    c.post("/api/scan")
    for v in c.get("/api/volumes").json():
        pages(c, v["id"])
    assert snapshot(lib) == before
