"""画面構成(#85)のための API: 蔵書の切り替え・著者 / タグ / 出版社の一覧・管理の統計タブ。蔵書は合成データ。"""
import shutil

import pytest
from fastapi.testclient import TestClient

from conftest import page_bytes, write_zip
from PIL import Image

from images import jpeg
from novels import epub_bytes
from spinebox import db
from spinebox.app import _root_name, create_app

KAKU = "かくかくしかじか"
OKIRAKU = "お気楽領主の楽しい領地防衛"


def cbz(lib, name, pages=3, real=False):
    data = (lambda i: jpeg(Image.new("RGB", (60, 90), (200, 30 * i, 40)))) if real else (lambda i: page_bytes(f"{name}/{i}"))
    write_zip(lib / name, [(f"{i:03d}.jpg", data(i)) for i in range(pages)])


@pytest.fixture
def env(make_settings, tmp_path):
    a = tmp_path / "漫画"
    b = tmp_path / "外付け"
    a.mkdir()
    b.mkdir()
    cbz(a, "[東村アキコ] かくかくしかじか 第01巻.cbz", pages=4, real=True)
    cbz(a, "[東村アキコ] かくかくしかじか 第03巻.cbz", pages=5)
    cbz(b, "[東村アキコ] かくかくしかじか 第02巻.cbz", pages=6)
    cbz(b, "[青色まろ×赤池宗] お気楽領主の楽しい領地防衛 第01巻.cbz", pages=7)
    (b / "[夏目漱石] 吾輩は猫である 第1巻.epub").write_bytes(epub_bytes(["<p>吾輩は猫である。</p>"]))
    settings = make_settings(a, b)
    client = TestClient(create_app(settings, frontend_dist=None))
    assert client.post("/api/scan").status_code == 200
    return settings, client, a, b


def libraries(client):
    return {lib["name"]: lib for lib in client.get("/api/libraries").json()}


def work(client, title, **params):
    return next(w for w in client.get("/api/works", params=params).json()["works"] if w["title"] == title)


def set_fields(settings, title_to_fields, client):
    conn = db.connect(settings.db_path)
    try:
        with db.transaction(conn) as c:
            for title, fields in title_to_fields.items():
                wid = work(client, title)["id"]
                for field, value in fields.items():
                    c.execute("""INSERT INTO work_fields (work_id, field, value, source, manual, updated_at)
                                 VALUES (?, ?, ?, 'public_db', 0, ?)""", (wid, field, value, db.now()))
    finally:
        conn.close()


# --- 蔵書の切り替え ---------------------------------------------------------------------------

def test_libraries_list_roots_with_volume_counts(env):
    _, client, a, b = env
    libs = libraries(client)
    assert set(libs) == {"漫画", "外付け"}
    assert libs["漫画"]["volume_count"] == 2 and libs["外付け"]["volume_count"] == 3
    assert libs["漫画"]["online"] is True and libs["漫画"]["path"] == str(a)


def test_root_filter_narrows_works_and_volumes(env):
    _, client, _, _ = env
    libs = libraries(client)
    manga, ext = libs["漫画"]["id"], libs["外付け"]["id"]
    every = client.get("/api/works").json()
    assert {w["title"] for w in every["works"]} == {KAKU, OKIRAKU, "吾輩は猫である"} and every["total"] == 3
    only_a = client.get("/api/works", params={"root": manga}).json()
    assert [w["title"] for w in only_a["works"]] == [KAKU] and only_a["total"] == 1
    # 作品の巻数・欠けはそのルートの巻で数える(1・3 巻だけ → 2 巻が欠け)
    assert only_a["works"][0]["volume_count"] == 2 and only_a["works"][0]["missing_volumes"] == [2]
    assert work(client, KAKU)["volume_count"] == 3 and work(client, KAKU)["missing_volumes"] == []
    only_b = client.get("/api/works", params={"root": ext}).json()
    assert {w["title"] for w in only_b["works"]} == {KAKU, OKIRAKU, "吾輩は猫である"}
    # 検索・絞り込みと組み合わせられる
    assert [w["title"] for w in client.get("/api/works", params={"root": manga, "q": "お気楽"}).json()["works"]] == []
    assert [w["title"] for w in client.get("/api/works", params={"root": ext, "q": "お気楽"}).json()["works"]] == [OKIRAKU]
    # 巻の一覧も絞れる
    assert len(client.get("/api/volumes").json()) == 5
    assert sorted(v["page_count"] for v in client.get("/api/volumes", params={"root": manga}).json()) == [4, 5]
    # 知らない id は空(設定から外したルートを端末が覚えていても壊れない)
    assert client.get("/api/works", params={"root": 9999}).json()["works"] == []
    assert client.get("/api/volumes", params={"root": 9999}).json() == []


def test_offline_root_stays_listed_and_filterable(env):
    """外付けドライブを外しても、ルートは切り替えの候補に残り、巻はオフラインとして絞り込める(DB から消さない)。"""
    _, client, _, b = env
    moved = b.with_name("外付け-外した")
    shutil.move(str(b), str(moved))
    assert client.post("/api/scan").status_code == 200
    libs = libraries(client)
    assert libs["外付け"]["online"] is False and libs["外付け"]["volume_count"] == 3
    vols = client.get("/api/volumes", params={"root": libs["外付け"]["id"]}).json()
    assert len(vols) == 3 and all(v["offline"] for v in vols)


def test_root_name_handles_windows_paths():
    assert _root_name("D:\\Books\\漫画") == "漫画"
    assert _root_name("D:\\Books\\漫画\\") == "漫画"
    assert _root_name("/srv/lib") == "lib"
    assert _root_name("D:\\") == "D:"


# --- 著者・タグ・出版社の一覧 ----------------------------------------------------------------

def test_facets_count_works_per_value_and_follow_root(env):
    settings, client, _, _ = env
    set_fields(settings, {KAKU: {"tags": '["美大", "宮崎"]', "publisher": "集英社"},
                          OKIRAKU: {"tags": '["領地経営", "美大"]', "publisher": "ホビージャパン"}}, client)
    authors = client.get("/api/works/facets/author").json()
    assert authors["name"] == "author"
    assert {v["value"]: v["count"] for v in authors["values"]} == {"東村アキコ": 1, "赤池宗": 1, "青色まろ": 1, "夏目漱石": 1}
    assert [v["value"] for v in authors["values"]] == sorted(v["value"] for v in authors["values"])
    tags = {v["value"]: v["count"] for v in client.get("/api/works/facets/tag").json()["values"]}
    assert tags == {"美大": 2, "宮崎": 1, "領地経営": 1}
    pubs = {v["value"]: v["count"] for v in client.get("/api/works/facets/publisher").json()["values"]}
    assert pubs == {"集英社": 1, "ホビージャパン": 1}
    manga = libraries(client)["漫画"]["id"]
    tags_a = {v["value"]: v["count"] for v in client.get("/api/works/facets/tag", params={"root": manga}).json()["values"]}
    assert tags_a == {"美大": 1, "宮崎": 1}
    assert client.get("/api/works/facets/synopsis").status_code == 404


def test_facet_stars_toggle_and_normalize_and_handle_symbol_only_values(env, tmp_path):
    """タグ・著者・出版社の値ごとにスターを付け外しでき(#117)、表記揺れは同じキーにまとまり、記号のみの値も衝突しない。"""
    import pytest
    from spinebox import search

    settings, client, _, _ = env
    set_fields(settings, {KAKU: {"tags": '["コメディ", "美大", "!?"]', "publisher": "集英社"},
                          OKIRAKU: {"tags": '["領地経営", "!!"]', "publisher": "ホビージャパン"}}, client)
    before = {v["value"]: v["starred"] for v in client.get("/api/works/facets/tag").json()["values"]}
    assert before == {"!?": False, "!!": False, "コメディ": False, "宮崎": False, "美大": False, "領地経営": False} or \
           before == {"!?": False, "!!": False, "コメディ": False, "美大": False, "領地経営": False}

    # スターを付ける(半角カタカナの表記揺れでも同じキーに付き、2 回目は created_at が変わらない)
    r1 = client.put("/api/works/facets/tag/star", json={"value": "ｺﾒﾃﾞｨ", "starred": True})
    assert r1.status_code == 200 and r1.json()["starred"] is True and r1.json()["created_at"]
    r2 = client.put("/api/works/facets/tag/star", json={"value": "コメディ", "starred": True})
    assert r2.status_code == 200 and r2.json()["created_at"] == r1.json()["created_at"]

    # 記号だけのタグ("!?" と "!!")も別々にスターを付けられる
    assert client.put("/api/works/facets/tag/star", json={"value": "!?", "starred": True}).status_code == 200
    tags = {v["value"]: v["starred"] for v in client.get("/api/works/facets/tag").json()["values"]}
    assert tags == {"!?": True, "!!": False, "コメディ": True, "美大": False, "領地経営": False}

    # 著者・出版社とは別項目として独立している
    assert client.put("/api/works/facets/author/star", json={"value": "東村アキコ", "starred": True}).status_code == 200
    authors = {v["value"]: v["starred"] for v in client.get("/api/works/facets/author").json()["values"]}
    assert authors["東村アキコ"] is True and authors["赤池宗"] is False
    assert client.put("/api/works/facets/publisher/star", json={"value": "集英社", "starred": True}).status_code == 200
    pubs = {v["value"]: v["starred"] for v in client.get("/api/works/facets/publisher").json()["values"]}
    assert pubs == {"集英社": True, "ホビージャパン": False}

    # スターを外す
    off = client.put("/api/works/facets/tag/star", json={"value": "コメディ", "starred": False})
    assert off.status_code == 200 and off.json() == {"name": "tag", "value": "コメディ", "starred": False, "created_at": None}
    assert {v["value"]: v["starred"] for v in client.get("/api/works/facets/tag").json()["values"]}["コメディ"] is False

    # 異常系: 未対応の項目は 404、空文字は 422、型違いは 422
    assert client.put("/api/works/facets/synopsis/star", json={"value": "x", "starred": True}).status_code == 404
    assert client.put("/api/works/facets/tag/star", json={"value": "   ", "starred": True}).status_code == 422
    assert client.put("/api/works/facets/tag/star", json={"value": "美大", "starred": "yes"}).status_code == 422
    conn = db.open_db(settings.db_path)
    try:
        with pytest.raises(search.InvalidFilter):
            search.starred_facet_keys(conn, "synopsis")
        with pytest.raises(search.InvalidFilter):
            search.set_facet_star(conn, "synopsis", "x", True)
    finally:
        conn.close()

    # マイグレーション 15: 版 14 の DB から開いても facet_stars が作られる
    v14_path = tmp_path / "v14.db"
    c14 = db.connect(v14_path)
    with db.transaction(c14) as c:
        for m in db.MIGRATIONS[:14]:
            for stmt in m:
                c.execute(stmt)
        c.execute("PRAGMA user_version = 14")
    c14.close()
    upgraded = db.open_db(v14_path)
    try:
        assert upgraded.execute("PRAGMA user_version").fetchone()[0] == len(db.MIGRATIONS) >= 15
        assert search.set_facet_star(upgraded, "tag", "SF", True)["starred"] is True
        assert search.starred_facet_keys(upgraded, "tag") == {"sf"}
    finally:
        upgraded.close()


def test_publisher_filter_selects_works(env):
    settings, client, _, _ = env
    set_fields(settings, {KAKU: {"publisher": "集英社"}}, client)
    titles = [w["title"] for w in client.get("/api/works", params={"publisher": "集英社"}).json()["works"]]
    assert titles == [KAKU]
    assert client.get("/api/works").json()["facets"]["publisher"] == ["集英社"]


# --- 統計 ------------------------------------------------------------------------------------

def db_counts(settings, root_id=None):
    """統計と突き合わせる数を、API を通さずに DB から直接数える。"""
    conn = db.connect(settings.db_path)
    where = "" if root_id is None else f" AND s.root_id = {int(root_id)}"
    try:
        base = """FROM volumes v JOIN sources s ON s.id = v.source_id JOIN library_roots r ON r.id = s.root_id
                  WHERE r.enabled = 1 AND r.inbox = 0 AND v.missing = 0 AND s.missing = 0
                    AND NOT EXISTS (SELECT 1 FROM volumes c WHERE c.parent_id = v.id AND c.missing = 0)""" + where
        return {
            "volumes": conn.execute("SELECT COUNT(*) " + base).fetchone()[0],
            "novels": conn.execute("SELECT COUNT(*) " + base + " AND v.format IS NOT NULL").fetchone()[0],
            "pages": conn.execute("SELECT COALESCE(SUM(v.page_count), 0) " + base + " AND v.format IS NULL").fetchone()[0],
            "finished": conn.execute("SELECT COUNT(*) " + base + " AND v.id IN (SELECT volume_id FROM reading_progress"
                                     " WHERE finished = 1)").fetchone()[0],
            "reading": conn.execute("SELECT COUNT(*) " + base + " AND v.id IN (SELECT volume_id FROM reading_progress"
                                    " WHERE finished = 0)").fetchone()[0],
            "works": conn.execute("SELECT COUNT(DISTINCT wv.work_id) " + base.replace(
                "WHERE", "JOIN work_volumes wv ON wv.volume_id = v.id WHERE", 1)).fetchone()[0],
        }
    finally:
        conn.close()


def test_stats_match_the_db(env):
    settings, client, _, _ = env
    vols = {v["title"]: v["id"] for v in client.get("/api/volumes").json()}
    k1 = next(i for t, i in vols.items() if "第01巻" in t and "かくかく" in t)
    k2 = next(i for t, i in vols.items() if "第02巻" in t)
    client.put(f"/api/volumes/{k1}/progress", json={"page": 3, "finished": True})
    client.put(f"/api/volumes/{k2}/progress", json={"page": 1, "finished": False})
    set_fields(settings, {KAKU: {"kind": "manga"}, "吾輩は猫である": {"kind": "novel"}}, client)

    st = client.get("/api/stats").json()
    expect = db_counts(settings)
    assert st["volumes"]["total"] == expect["volumes"] == 5
    assert st["volumes"]["novels"] == expect["novels"] == 1 and st["volumes"]["images"] == 4
    assert st["pages"]["total"] == expect["pages"] == 4 + 5 + 6 + 7
    assert st["volumes"]["finished"] == expect["finished"] == 1
    assert st["volumes"]["reading"] == expect["reading"] == 1
    assert st["volumes"]["unread"] == 3
    assert st["works"]["total"] == expect["works"] == 3
    assert (st["works"]["manga"], st["works"]["novel"], st["works"]["kind_unknown"]) == (1, 1, 1)
    assert (st["works"]["finished"], st["works"]["reading"], st["works"]["unread"]) == (0, 1, 2)
    assert st["unassigned_volumes"] == 0 and st["duplicate_candidates"] == 0 and st["scan_issues"] == 0
    assert st["volumes"]["offline"] == 0
    assert set(st["cache"]) == {"disk", "memory", "thumbnails"}
    assert st["cache"]["disk"]["limit_bytes"] == settings.cache_limit_bytes

    manga = libraries(client)["漫画"]["id"]
    st_a = client.get("/api/stats", params={"root": manga}).json()
    expect_a = db_counts(settings, manga)
    assert st_a["root_id"] == manga
    assert st_a["volumes"]["total"] == expect_a["volumes"] == 2
    assert st_a["pages"]["total"] == expect_a["pages"] == 9
    assert st_a["works"]["total"] == expect_a["works"] == 1
    assert st_a["works"]["with_missing"] == 1  # この蔵書には 2 巻が無い


def test_stats_no_cover_drops_after_the_cover_is_made(env):
    _, client, _, _ = env
    before = client.get("/api/stats").json()["works"]["no_cover"]
    assert before == 2  # かくかくしかじか・お気楽領主(小説は表紙を作れないので数えない)
    cover = work(client, KAKU)["cover_volume_id"]
    assert client.get(f"/api/volumes/{cover}/thumbnail").status_code == 200
    assert client.get("/api/stats").json()["works"]["no_cover"] == 1


def test_stats_count_open_duplicates_and_scan_issues(env):
    settings, client, a, _ = env
    (a / "壊れた.cbz").write_bytes(b"PK\x03\x04broken")
    assert client.post("/api/scan").status_code == 200
    vols = [v["id"] for v in client.get("/api/volumes").json()]
    conn = db.connect(settings.db_path)
    try:
        with db.transaction(conn) as c:
            c.execute("""INSERT INTO duplicate_candidates (volume_a, volume_b, rule, matched_pages, compared_pages,
                         evidence, computed_at) VALUES (?, ?, 'pages', 3, 3, '{}', ?)""", (min(vols[:2]), max(vols[:2]), db.now()))
        issues = conn.execute("SELECT COUNT(*) FROM scan_issues").fetchone()[0]
    finally:
        conn.close()
    st = client.get("/api/stats").json()
    assert st["duplicate_candidates"] == 1
    assert st["scan_issues"] == issues >= 1


def test_settings_endpoint_reports_server_values(env):
    settings, client, _, _ = env
    body = client.get("/api/settings").json()
    assert body["untranslated_terms"] == settings.metadata.untranslated_terms
    assert body["cache_limit_bytes"] == settings.cache_limit_bytes


def test_stats_counts_a_vanished_cover_file_as_no_cover(env):
    """スキャンの後に表紙の巻のファイルが消えた(次のスキャン前)ときも、統計は落ちずに「表紙なし」に数える。"""
    _, client, a, _ = env
    cover = work(client, KAKU)["cover_volume_id"]
    assert client.get(f"/api/volumes/{cover}/thumbnail").status_code == 200
    assert client.get("/api/stats").json()["works"]["no_cover"] == 1
    (a / "[東村アキコ] かくかくしかじか 第01巻.cbz").unlink()
    r = client.get("/api/stats")
    assert r.status_code == 200 and r.json()["works"]["no_cover"] == 2


def test_stats_does_not_query_a_volume_row_per_work(env):
    """統計は全作品の表紙の有無を見るので、巻の行を作品の数だけ SELECT しない(agy レビュー。N+1 の禁止)。
    1000 作品分の表紙の巻を渡しても、巻の行は 500 件ずつの IN でまとめて引く。"""
    settings, client, _, _ = env
    conn = db.connect(settings.db_path)
    try:
        ids = [r["id"] for r in conn.execute("SELECT id FROM volumes")]
        from spinebox.app import ExtractionCache, Library, Thumbnails  # noqa: PLC0415
        lib = Library(settings, ExtractionCache(settings.cache_dir, 10**9))
        thumbs = Thumbnails(lib, ExtractionCache(settings.thumbs_dir, 10**9))
        statements: list[str] = []
        conn.set_trace_callback(statements.append)
        built = thumbs.are_built(conn, ids * 250)  # 重複を含む 1000 件
        conn.set_trace_callback(None)
        volume_selects = [s for s in statements if "FROM volumes v JOIN sources s" in s]
        assert set(built) == set(ids) and not any(built.values())
        assert len(volume_selects) == 1
        # 見えない巻(見つからない id)は False で、例外にならない
        assert thumbs.are_built(conn, [999999]) == {999999: False}
    finally:
        conn.close()
