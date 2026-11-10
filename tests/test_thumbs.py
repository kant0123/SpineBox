"""表紙のサムネイル(#75)。蔵書は一時ディレクトリに動的生成する(本物の JPEG は tests/images.py)。"""
import io
import sys

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from conftest import page_bytes, snapshot, write_zip, zip_bytes
from images import jpeg, make_page
from novels import epub_bytes
from spinebox import works
from spinebox.app import create_app
from spinebox.cache import ExtractionCache, NotCached
from spinebox.thumbs import THUMB_MAX_WIDTH, THUMB_SIZE, THUMB_TARGET_HEIGHT, calc_thumbnail_size, make_thumbnail


def solid(color, size=(600, 900)) -> bytes:
    """一色のページ(表紙の取り違えを色で確かめる)。"""
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "JPEG", quality=95)
    return buf.getvalue()


RED, BLUE, GREEN = (220, 20, 20), (20, 20, 220), (20, 200, 20)


def color_of(data: bytes) -> tuple[int, int, int]:
    im = Image.open(io.BytesIO(data)).convert("RGB")
    return im.getpixel((im.width // 2, im.height // 2))


def near(a, b, tol=30) -> bool:
    return all(abs(x - y) <= tol for x, y in zip(a, b))


def book(cover_color, pages=3) -> list:
    # cover.jpg は自然順だと末尾だが、ページの並びの規則で先頭になる(表紙はそれを使う)
    return [(f"{i:03d}.jpg", jpeg(make_page(i))) for i in range(pages)] + [("cover.jpg", solid(cover_color))]


@pytest.fixture
def lib(tmp_path):
    lib = tmp_path / "lib"
    write_zip(lib / "Hoshi no umi v02.cbz", book(BLUE))
    write_zip(lib / "Hoshi no umi v01.cbz", book(RED))
    # 内側アーカイブの巻(取り出さないと 1 ページ目が見えない)
    write_zip(lib / "Kaze no tani v01-02.zip", [("Kaze no tani v01.zip", zip_bytes(book(GREEN))),
                                                 ("Kaze no tani v02.zip", zip_bytes(book(BLUE)))])
    (lib / "Tsuki no shosetsu v01.epub").write_bytes(epub_bytes(["<p>本文</p>"]))
    return lib


@pytest.fixture
def env(make_settings, lib):
    settings = make_settings(lib)
    client = TestClient(create_app(settings, frontend_dist=None))
    assert client.post("/api/scan").status_code == 200
    return client, settings


def work_by_title(client):
    return {w["title"]: w for w in client.get("/api/works").json()["works"]}


def volume_id(client, title):
    return next(v["id"] for v in client.get("/api/volumes").json() if v["title"] == title)


def files(d):
    return sorted(p.name for p in d.iterdir()) if d.exists() else []


def test_work_cover_is_first_page_of_volume_one(env):
    client, settings = env
    w = work_by_title(client)["Hoshi no umi"]
    assert w["cover_volume_id"] == volume_id(client, "Hoshi no umi v01")
    r = client.get(f"/api/volumes/{w['cover_volume_id']}/thumbnail")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "image/jpeg"
    im = Image.open(io.BytesIO(r.content))
    assert im.width <= THUMB_MAX_WIDTH and im.height <= THUMB_TARGET_HEIGHT
    assert near(color_of(r.content), RED)  # 1 巻の cover.jpg(2 巻の青ではない)
    # 置き場はハッシュ名。展開キャッシュは使わない(zip の 1 エントリを読むだけ)
    (thumb,) = files(settings.thumbs_dir)
    assert len(thumb) == 64 + len(".bin")
    assert files(settings.cache_dir) == []


def test_thumbnail_revalidates_with_etag(env):
    client, _ = env
    vid = volume_id(client, "Hoshi no umi v01")
    r = client.get(f"/api/volumes/{vid}/thumbnail")
    etag = r.headers["etag"]
    again = client.get(f"/api/volumes/{vid}/thumbnail", headers={"If-None-Match": etag})
    assert again.status_code == 304 and again.content == b""


def test_inner_archive_cover_is_not_extracted_from_the_list(env):
    client, settings = env
    w = work_by_title(client)["Kaze no tani"]
    r = client.get(f"/api/volumes/{w['cover_volume_id']}/thumbnail")
    assert r.status_code == 404 and r.json()["pending"] is True
    assert r.headers["cache-control"] == "no-store"
    assert files(settings.cache_dir) == []  # 一覧からは内側アーカイブを取り出さない
    assert files(settings.thumbs_dir) == []


def test_inner_archive_cover_appears_after_the_volume_is_opened(env):
    client, settings = env
    vid = work_by_title(client)["Kaze no tani"]["cover_volume_id"]
    assert client.get(f"/api/volumes/{vid}").status_code == 200  # 読むために開いた(ここで取り出す)
    cached = files(settings.cache_dir)
    # 開いたので中の巻に分解され、表紙の巻は子の v01 になる
    vid = work_by_title(client)["Kaze no tani"]["cover_volume_id"]
    r = client.get(f"/api/volumes/{vid}/thumbnail")
    assert r.status_code == 200 and near(color_of(r.content), GREEN)
    assert files(settings.cache_dir) == cached  # 取り出し済みのものを使った(新たに取り出していない)


def test_explicit_build_extracts_and_makes_missing_covers(env, lib):
    client, settings = env
    before = snapshot(lib)
    r = client.post("/api/thumbnails/build?wait=true")
    assert r.status_code == 200
    state = r.json()
    assert state["running"] is False and state["error"] is None
    # 作品 3 つ。画像の 2 作品は作り、表紙の指定が無い EPUB は作れない(#250)
    assert state["summary"] == {"total": 3, "made": 2, "existing": 0, "skipped": 1}
    vid = work_by_title(client)["Kaze no tani"]["cover_volume_id"]
    r = client.get(f"/api/volumes/{vid}/thumbnail")
    assert r.status_code == 200 and near(color_of(r.content), GREEN)
    # 2 回目は作成済み
    assert client.post("/api/thumbnails/build?wait=true").json()["summary"]["existing"] == 2
    assert client.get("/api/thumbnails").json()["summary"]["existing"] == 2
    assert snapshot(lib) == before  # 蔵書は 1 バイトも変わらない


def test_build_does_not_read_existing_covers(env, monkeypatch):
    """「表紙のみ補完」は作成済みの表紙を読んで確かめない(#298。読むと LRU の順が変わる)。"""
    client, _ = env
    assert client.post("/api/thumbnails/build?wait=true").status_code == 200
    store = client.app.state.thumbs.store
    built = set(store.keys())
    assert len(built) == 2
    reads = []
    original = store.read
    monkeypatch.setattr(store, "read", lambda key: reads.append(key) or original(key))
    summary = client.post("/api/thumbnails/build?wait=true").json()["summary"]
    assert summary["existing"] == 2
    assert not set(reads) & built  # 作成済みの表紙は読んでいない


def test_build_is_refused_during_scan(env):
    client, _ = env
    client.app.state.scan_lock.acquire()
    try:
        assert client.post("/api/thumbnails/build?wait=true").status_code == 409
    finally:
        client.app.state.scan_lock.release()


def test_changed_source_makes_a_new_cover(env, lib):
    client, _ = env
    vid = volume_id(client, "Hoshi no umi v01")
    assert near(color_of(client.get(f"/api/volumes/{vid}/thumbnail").content), RED)
    write_zip(lib / "Hoshi no umi v01.cbz", book(GREEN))
    assert near(color_of(client.get(f"/api/volumes/{vid}/thumbnail").content), GREEN)


def test_novel_and_unreadable_cover_are_404_not_pending(env, lib, make_settings):
    client, _ = env
    novel = volume_id(client, "Tsuki no shosetsu v01")
    r = client.get(f"/api/volumes/{novel}/thumbnail")
    assert r.status_code == 404 and r.json()["pending"] is False
    write_zip(lib / "Fake v01.cbz", [("001.jpg", page_bytes("not an image"))])
    client.post("/api/scan")
    r = client.get(f"/api/volumes/{volume_id(client, 'Fake v01')}/thumbnail")
    assert r.status_code == 404 and r.json()["pending"] is False


FAKE_7Z = """#!{python}
import sys
with open({log!r}, "a") as f:
    f.write(sys.argv[1] + "\\n")
if sys.argv[1] == "l":
    sys.stdout.write("Path = 001.jpg\\nSize = 10\\nFolder = -\\n\\n")
"""


@pytest.mark.skipif(sys.platform == "win32", reason="偽の 7-Zip を shebang のスクリプトで作る")
def test_7z_outer_is_pending_without_starting_7zip(make_settings, tmp_path):
    # 7z / rar は目次を読むだけで 7-Zip が起動するので、一覧からは作らない
    log = tmp_path / "7z.log"
    fake = tmp_path / "fake7z"
    fake.write_text(FAKE_7Z.format(python=sys.executable, log=str(log)))
    fake.chmod(0o755)
    lib = tmp_path / "lib"
    lib.mkdir()
    (lib / "Solid v01.7z").write_bytes(b"7z\xbc\xaf\x27\x1c junk")
    client = TestClient(create_app(make_settings(lib, sevenzip_path=str(fake)), frontend_dist=None))
    client.post("/api/scan")
    vid = volume_id(client, "Solid v01")
    log.write_text("")
    r = client.get(f"/api/volumes/{vid}/thumbnail")
    assert r.status_code == 404 and r.json()["pending"] is True
    assert log.read_text() == ""  # 7-Zip を起動していない


def test_offline_root_is_409_and_keeps_the_cover(env, lib):
    client, settings = env
    vid = volume_id(client, "Hoshi no umi v01")
    assert client.get(f"/api/volumes/{vid}/thumbnail").status_code == 200
    moved = lib.with_name("unplugged")
    lib.rename(moved)
    assert client.get(f"/api/volumes/{vid}/thumbnail").status_code == 409
    moved.rename(lib)
    assert client.get(f"/api/volumes/{vid}/thumbnail").status_code == 200
    assert len(files(settings.thumbs_dir)) == 1  # 同じものを使った


def test_cover_volume_choice():
    def v(id, start=None, *, fmt="images", offline=False, pages=10, title=None):
        number = None if start is None else {"kind": "volume", "start": start, "end": start, "evidence": "name"}
        return {"id": id, "title": title or f"t{id}", "format": fmt, "offline": offline, "page_count": pages,
                "volume_number": number}

    assert works.cover_volume_id([v(1, 2), v(2, 1), v(3)]) == 2
    assert works.cover_volume_id([v(1), v(2, 3)]) == 2  # 番号の無い巻は後ろ
    assert works.cover_volume_id([v(1, 1, offline=True), v(2, 2)]) == 2
    assert works.cover_volume_id([v(1, 1, fmt="text"), v(2, 1, pages=0)]) is None
    # 同じ番号・番号の無い巻どうしでは EPUB を先にする(保存ページの zip の本文より本の表紙。#250)
    assert works.cover_volume_id([v(1, fmt="images", title="a"), v(2, fmt="epub", title="b")]) == 2
    assert works.cover_volume_id([v(1, 1, fmt="images", title="a"), v(2, 1, fmt="epub", title="b")]) == 2
    assert works.cover_volume_id([v(1, 1, fmt="images"), v(2, fmt="epub")]) == 1  # 番号のある巻が先
    assert works.cover_volume_id([v(1, 1, title="b"), v(2, 1, title="a")]) == 2  # 重複は名前の自然順
    assert works.cover_volume_id([]) is None


def test_calc_thumbnail_size():
    # 縦長 (ar <= 2/3): 幅を 480 に合わせる
    assert calc_thumbnail_size(1200, 1800) == (480, 720)
    assert calc_thumbnail_size(600, 1200) == (480, 960)
    # 横長 (ar > 2/3): 高さを 720 に合わせる (cover 時に十分な解像度を維持 #222)
    assert calc_thumbnail_size(1800, 1200) == (1080, 720)
    assert calc_thumbnail_size(1440, 720) == (1440, 720)
    # パノラマ・超横長: max_w (1440) でクリップ
    assert calc_thumbnail_size(3600, 1200) == (1440, 480)
    # 小さい画像: 拡大しない
    assert calc_thumbnail_size(240, 360) == (240, 360)
    assert calc_thumbnail_size(300, 200) == (300, 200)
    # 不正なサイズ
    assert calc_thumbnail_size(0, 0) == (0, 0)


def test_make_thumbnail_rejects_non_images_and_shrinks():
    assert make_thumbnail(b"not an image") is None
    # 縦長画像
    out = make_thumbnail(solid(RED, (1200, 1800)))
    im = Image.open(io.BytesIO(out))
    assert im.format == "JPEG" and im.size == THUMB_SIZE

    # 横長画像(#222: 見開きでも高さ 720 を維持してクロップ時の解像度低下を防ぐ)
    out_h = make_thumbnail(solid(RED, (1800, 1200)))
    im_h = Image.open(io.BytesIO(out_h))
    assert im_h.format == "JPEG" and im_h.size == (1080, 720)


def test_cache_use_existing_does_not_create(tmp_path):
    cache = ExtractionCache(tmp_path / "c", 1024)
    with pytest.raises(NotCached):
        with cache.use_existing("k"):
            pass
    assert cache.read("k") is None and files(tmp_path / "c") == []
    with cache.use("k", lambda f: f.write(b"abc")):
        pass
    assert cache.read("k") == b"abc"
    with cache.use_existing("k") as path:
        assert path.read_bytes() == b"abc"


def test_build_skips_unreadable_covers_and_continues(env, lib):
    client, _ = env
    write_zip(lib / "Fake v01.cbz", [("001.jpg", page_bytes("not an image"))])
    client.post("/api/scan")
    summary = client.post("/api/thumbnails/build?wait=true").json()["summary"]
    assert summary == {"total": 4, "made": 2, "existing": 0, "skipped": 2}


def test_epub_cover_comes_from_opf_and_beats_a_saved_page_zip(make_settings, tmp_path):
    lib = tmp_path / "lib2"
    write_zip(lib / "Book.zip", [("saved_files/images/001.jpg", jpeg(make_page(1)))])
    (lib / "Book.epub").write_bytes(epub_bytes(
        ["<p>本文</p>"], images={"images/cover.png": solid(RED)}, metadata_extra='<meta name="cover" content="img0"/>'))
    client = TestClient(create_app(make_settings(lib), frontend_dist=None))
    client.post("/api/scan")
    w = next(iter(work_by_title(client).values()))
    epub = next(v for v in client.get("/api/volumes").json() if v["format"] == "epub")
    assert w["cover_volume_id"] == epub["id"]
    r = client.get(f"/api/volumes/{w['cover_volume_id']}/thumbnail")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "image/jpeg"
