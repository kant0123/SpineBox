"""蔵書ごとの公開 DB の取得元(#316)。ネットワークには出ない(記録した応答 + 偽の取得元)。蔵書は合成データ。"""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from metadata_fakes import BANGUMI_SEARCH, MEDIAARTS, NDL
from spinebox import db, libraries, linking
from spinebox.app import create_app
from spinebox.scanner import Scanner
from spinebox import works
from test_follows import Env as FollowEnv, kaku_files
from test_libraries import cbz, client_for, groups
from test_linking import KAKU, KAKU_FILES, Env, network

DEFAULT = ("ndl", "mediaarts", "bangumi")


@pytest.fixture
def dirs(tmp_path):
    """作品A 2 冊の a、作品B 1 冊の b。"""
    a, b = tmp_path / "本", tmp_path / "同人"
    for d in (a, b):
        d.mkdir(parents=True)
    cbz(a, "作品A 第01巻.cbz")
    cbz(a, "作品A 第02巻.cbz")
    cbz(b, "作品B 第01巻.cbz")
    return a, b


def put(client, gid, sources):
    return client.put(f"/api/library-groups/{gid}/metadata-sources", json={"sources": sources})


def scanned(settings):
    """スキャンと名寄せまで済ませた接続と、作品名 → id。"""
    conn = db.open_db(settings.db_path)
    libraries.sync(conn, settings)
    Scanner(conn, settings).scan()
    works.sync(conn)
    ids = {w["title"]: w["id"] for w in works.list_works(conn)["works"]}
    return conn, ids


def group_id(conn, name):
    return next(g["id"] for g in libraries.list_groups(conn) if g["name"] == name)


# --- 指定の保存 ---------------------------------------------------------------------------------

def test_group_sources_are_saved_reset_and_validated(dirs, make_settings):
    a, b = dirs
    settings = make_settings(a, b)
    client = client_for(settings)
    gid = groups(client)["同人"]["id"]
    assert groups(client)["同人"]["metadata_sources"] is None  # 指定なし = 設定の既定に従う

    # 重複は畳み、検索の順で保存する(引く順は選べない)
    r = put(client, gid, ["nhentai", "ehentai", "nhentai"])
    assert r.status_code == 200 and r.json()["metadata_sources"] == ["ehentai", "nhentai"]
    assert groups(client)["同人"]["metadata_sources"] == ["ehentai", "nhentai"]
    assert groups(client)["本"]["metadata_sources"] is None  # ほかの蔵書は変わらない
    # 作り直したアプリ(再起動)でも残る
    assert groups(client_for(settings))["同人"]["metadata_sources"] == ["ehentai", "nhentai"]

    # 空の配列(この蔵書では取得しない)と null(既定に戻す)は別物
    assert put(client, gid, []).json()["metadata_sources"] == []
    assert put(client, gid, None).json()["metadata_sources"] is None

    assert put(client, gid, ["ndl", "anilist"]).status_code == 422 and "anilist" in put(client, gid, ["anilist"]).json()["detail"]
    assert put(client, gid, "ndl").status_code == 422
    assert put(client, gid, [1]).status_code == 422
    assert client.put(f"/api/library-groups/{gid}/metadata-sources", json={}).status_code == 422
    assert put(client, 9999, ["ndl"]).status_code == 404
    assert groups(client)["同人"]["metadata_sources"] is None  # 弾いた要求では変わらない


def test_setting_the_same_sources_again_writes_nothing(dirs, make_settings):
    a, _ = dirs
    conn = db.open_db(make_settings(a).db_path)
    libraries.sync(conn, make_settings(a))
    gid = group_id(conn, "本")
    libraries.set_group_sources(conn, gid, ["ndl"])
    before = conn.total_changes
    libraries.set_group_sources(conn, gid, ["ndl"])
    assert conn.total_changes == before
    libraries.set_group_sources(conn, gid, None)
    before = conn.total_changes
    libraries.set_group_sources(conn, gid, None)
    assert conn.total_changes == before
    conn.close()


def test_migration_19_keeps_existing_groups_on_the_default(make_settings):
    settings = make_settings()
    conn = db.connect(settings.db_path)
    with db.transaction(conn) as c:
        for migration in db.MIGRATIONS[:18]:
            for statement in migration:
                c.execute(statement)
        c.execute("PRAGMA user_version = 18")
        c.execute("INSERT INTO library_groups (id, name, created_at) VALUES (1, '本', 't')")
    assert db.migrate(conn) == len(db.MIGRATIONS) >= 19
    assert [g["metadata_sources"] for g in libraries.list_groups(conn)] == [None]
    conn.close()


def test_unreadable_stored_value_falls_back_to_the_default(dirs, make_settings):
    a, _ = dirs
    conn, ids = scanned(make_settings(a))
    for broken in ("not json", '{"ndl": true}', "3"):
        conn.execute("UPDATE library_groups SET metadata_sources = ?", (broken,))
        assert linking.parse_group_sources(broken) is None
        assert libraries.list_groups(conn)[0]["metadata_sources"] is None
        assert linking.work_sources(conn, DEFAULT) == {ids["作品A"]: DEFAULT}
        assert linking.active_sources(conn, DEFAULT) == DEFAULT
    # 知らない名前が混ざっていても、知っている取得元だけを使う
    conn.execute("UPDATE library_groups SET metadata_sources = ?", ('["anilist", "ndl"]',))
    assert linking.work_sources(conn, DEFAULT) == {ids["作品A"]: ("ndl",)}
    conn.close()


# --- 作品 → 取得元 ------------------------------------------------------------------------------

def test_work_sources_follow_the_group_of_the_works_volumes(dirs, make_settings):
    a, b = dirs
    conn, ids = scanned(make_settings(a, b))
    wa, wb = ids["作品A"], ids["作品B"]
    assert linking.work_sources(conn, DEFAULT) == {wa: DEFAULT, wb: DEFAULT}  # どちらも指定なし
    assert linking.active_sources(conn, DEFAULT) == DEFAULT

    libraries.set_group_sources(conn, group_id(conn, "同人"), ["nhentai", "ehentai"])
    assert linking.work_sources(conn, DEFAULT) == {wa: DEFAULT, wb: ("ehentai", "nhentai")}
    assert linking.work_sources(conn, DEFAULT, [wb]) == {wb: ("ehentai", "nhentai")}
    assert linking.sources_of(conn, DEFAULT, wb) == ("ehentai", "nhentai")
    assert linking.sources_of(conn, (), wa) == ()  # 既定が空なら、指定の無い蔵書の作品は取得元なし
    assert linking.active_sources(conn, DEFAULT) == ("ndl", "mediaarts", "bangumi", "ehentai", "nhentai")
    assert linking.active_sources(conn, ()) == ("ehentai", "nhentai")

    libraries.set_group_sources(conn, group_id(conn, "本"), [])  # この蔵書では取得しない
    assert linking.work_sources(conn, DEFAULT) == {wa: (), wb: ("ehentai", "nhentai")}
    assert linking.active_sources(conn, DEFAULT) == ("ehentai", "nhentai")
    conn.close()


def test_work_spanning_two_groups_uses_the_union(dirs, make_settings):
    a, b = dirs
    cbz(b, "作品A 第03巻.cbz")  # 作品A の巻が「同人」の蔵書にもある
    conn, ids = scanned(make_settings(a, b))
    libraries.set_group_sources(conn, group_id(conn, "本"), ["bangumi", "ndl"])
    libraries.set_group_sources(conn, group_id(conn, "同人"), ["ehentai"])
    assert linking.work_sources(conn, DEFAULT) == {ids["作品A"]: ("ndl", "bangumi", "ehentai"), ids["作品B"]: ("ehentai",)}
    # 片方が指定なしなら、その分は既定
    libraries.set_group_sources(conn, group_id(conn, "本"), None)
    assert linking.sources_of(conn, ("mediaarts",), ids["作品A"]) == ("mediaarts", "ehentai")
    conn.close()


def test_work_without_listed_volumes_is_left_to_the_default(dirs, make_settings):
    a, b = dirs
    settings = make_settings(a, b)
    conn, ids = scanned(settings)
    libraries.set_group_sources(conn, group_id(conn, "同人"), ["ehentai"])
    folder = next(f["id"] for g in libraries.list_groups(conn) for f in g["folders"] if f["path"] == str(b))
    libraries.remove_folder(conn, folder)  # 外したフォルダの巻は一覧に見えない
    assert ids["作品B"] not in linking.work_sources(conn, DEFAULT)
    assert linking.sources_of(conn, DEFAULT, ids["作品B"]) == DEFAULT
    conn.close()


def test_work_sources_is_one_query_regardless_of_the_number_of_works(dirs, make_settings):
    a, b = dirs
    for n in range(30):
        cbz(a, f"別の作品{n:02d} 第01巻.cbz", pages=1)
    conn, ids = scanned(make_settings(a, b))
    assert len(ids) >= 32
    statements = []
    conn.set_trace_callback(statements.append)
    linking.work_sources(conn, DEFAULT)
    linking.work_sources(conn, DEFAULT, list(ids.values()))
    conn.set_trace_callback(None)
    assert len(statements) == 2
    conn.close()


def test_works_needing_fetch_uses_each_works_own_sources(dirs, make_settings):
    a, b = dirs
    settings = make_settings(a, b)
    conn, ids = scanned(settings)
    wa, wb = ids["作品A"], ids["作品B"]
    meta = settings.metadata
    now = datetime.now(timezone.utc)

    def due(per_work, default=DEFAULT, at=now):
        return linking.works_needing_fetch(conn, meta, default, at, per_work=per_work)

    assert due({wa: ("ndl",), wb: ()}) == [wa]                 # 取得元の無い作品は対象外
    assert due({wb: ("ehentai",)}, default=()) == [wb]         # 設定が空でも、蔵書で有効にした作品は対象
    assert due({}, default=()) == [] and due(None, default=()) == []
    assert due({wb: ()}) == [wa]                               # 載っていない作品は既定で決める

    # 蔵書の指定から外れた取得元の行は、古くても・失敗していても取り直さない
    old = (now - timedelta(days=meta.recheck_days + 1)).isoformat(timespec="seconds")
    fresh = now.isoformat(timespec="seconds")
    with db.transaction(conn) as c:
        c.execute("INSERT INTO work_links (work_id, source, status, failure, attempted_at) VALUES (?, 'ndl', 'none', ?, ?)",
                  (wa, '{"kind": "timeout"}', old))
        c.execute("INSERT INTO work_links (work_id, source, status, attempted_at) VALUES (?, 'ehentai', 'none', ?)",
                  (wa, fresh))
    assert due({wa: ("ehentai",), wb: ()}) == []
    assert due({wa: ("ndl", "ehentai"), wb: ()}) == [wa]
    conn.close()


# --- 取得(API・ジョブ) --------------------------------------------------------------------------

def only_group(env):
    [g] = env.client.get("/api/libraries").json()
    return g["id"]


def test_group_can_enable_a_source_that_the_config_leaves_off(make_settings, tmp_path):
    """設定の取得元が空でも、蔵書で有効にした取得元は引ける。引くのはその取得元だけ。"""
    env = Env(make_settings, tmp_path, network("kaku"), KAKU_FILES, sources=())
    wid = env.work_id()
    assert env.client.post(f"/api/works/{wid}/metadata/fetch").status_code == 409
    assert put(env.client, only_group(env), ["ndl"]).status_code == 200

    settings = env.client.get("/api/settings").json()
    assert settings["metadata_sources"] == ["ndl"] and settings["metadata_default_sources"] == []
    assert [s["name"] for s in settings["metadata_all_sources"]] == list(linking.SEARCH_ORDER)
    assert settings["metadata_all_sources"][0]["label"] == "国立国会図書館サーチ"
    assert env.client.get("/api/works/metadata").json()["sources"] == ["ndl"]
    assert [x["source"] for x in env.work(wid)["links"]] == ["ndl"]  # 連携の欄に出るのは、この作品の取得元だけ

    w = env.fetch(wid)
    assert [(x["source"], x["status"]) for x in w["links"]] == [("ndl", "linked")]
    assert env.net.count(NDL) >= 1 and env.net.count(MEDIAARTS) == 0 and env.net.count(BANGUMI_SEARCH) == 0
    assert w["title"] == KAKU and w["fields"]["publisher"]["value"] == "集英社"


def test_sources_removed_from_the_group_keep_their_links_but_are_not_fetched_again(make_settings, tmp_path):
    env = Env(make_settings, tmp_path, network("kaku"), KAKU_FILES)
    wid = env.work_id()
    env.fetch(wid)  # 既定の 3 つで連携
    gid = only_group(env)
    assert put(env.client, gid, ["mediaarts"]).status_code == 200
    ndl_before, bangumi_before, mediaarts_before = env.net.count(NDL), env.net.count(BANGUMI_SEARCH), env.net.count(MEDIAARTS)

    # 取得済みの連携と、そこから入った値は残る(連携の欄にも出る)
    w = env.work(wid)
    assert {x["source"]: x["status"] for x in w["links"]} == {"ndl": "linked", "mediaarts": "linked", "bangumi": "linked"}
    assert w["fields"]["publisher"]["value"] == "集英社" and w["fields"]["publisher"]["sources"] == ["ndl"]

    # 取り直しは指定の取得元だけ(作品ページの「情報を取得」も、まとめての取得も)
    env.fetch(wid)
    r = env.client.post("/api/works/metadata/fetch?wait=true&all=true").json()
    assert r["error"] is None and set(r["summary"]["sources"]) <= {"linked"}
    assert env.net.count(NDL) == ndl_before and env.net.count(BANGUMI_SEARCH) == bangumi_before
    assert {x["source"]: x["status"] for x in env.work(wid)["links"]} == {"ndl": "linked", "mediaarts": "linked",
                                                                         "bangumi": "linked"}
    assert env.work(wid)["fields"]["publisher"]["value"] == "集英社"
    assert env.net.count(MEDIAARTS) >= mediaarts_before

    # この蔵書では取得しない: 作品の取得は 409、どの蔵書にも取得元が無いので、まとめての取得も 409
    assert put(env.client, gid, []).status_code == 200
    r = env.client.post(f"/api/works/{wid}/metadata/fetch")
    assert r.status_code == 409 and "蔵書" in r.json()["detail"]
    assert env.client.post("/api/works/metadata/fetch").status_code == 409
    assert env.client.get("/api/works/metadata").json()["sources"] == []
    assert env.work(wid)["fields"]["publisher"]["value"] == "集英社"  # それでも値は残る


def test_batch_fetch_asks_only_the_sources_of_each_works_group(make_settings, tmp_path):
    """まとめての取得(軽い取得)も、作品ごとの蔵書の取得元だけを引く。取得元の無い蔵書の作品は対象にしない。"""
    env = Env(make_settings, tmp_path, network("kaku"), KAKU_FILES)
    wid = env.work_id()
    gid = only_group(env)
    put(env.client, gid, ["bangumi"])
    r = env.client.post("/api/works/metadata/fetch?wait=true").json()
    assert r["total"] == 1 and r["summary"]["sources"] == {"linked": 1}
    assert env.net.count(NDL) == 0 and env.net.count(MEDIAARTS) == 0 and env.net.count(BANGUMI_SEARCH) >= 1
    assert [x["source"] for x in env.work(wid)["links"]] == ["bangumi"]
    assert env.client.post("/api/works/metadata/fetch?wait=true").json()["total"] == 0  # 取得済みは対象外

    # 取得元を足しても、確かな連携(0.9 以上)がある作品はまとめての取得の対象に戻らない(#288 の規則のまま)。
    # 足した取得元を引くのは、作品ページの「情報を取得」
    put(env.client, gid, ["ndl", "bangumi"])
    assert env.client.post("/api/works/metadata/fetch?wait=true").json()["total"] == 0
    assert env.net.count(NDL) == 0
    env.fetch(wid)
    assert env.net.count(NDL) >= 1 and env.net.count(MEDIAARTS) == 0


def test_review_list_shows_each_works_own_sources(make_settings, tmp_path):
    from test_linking import OKIRAKU_FILES
    env = Env(make_settings, tmp_path, network("okiraku"), OKIRAKU_FILES)
    wid = env.work_id()
    put(env.client, only_group(env), ["ndl"])
    env.fetch(wid)  # 漫画と小説が並び、確認待ちになる
    [w] = env.client.get("/api/review/links").json()["works"]
    assert w["id"] == wid and [(x["source"], x["status"]) for x in w["links"]] == [("ndl", "pending")]


def test_background_fetch_uses_the_works_sources(make_settings, tmp_path):
    env = Env(make_settings, tmp_path, network("kaku"), KAKU_FILES, sources=())
    from spinebox.datajobs import DataJobs
    from spinebox.metadata import MetadataService
    import dataclasses
    meta = dataclasses.replace(env.settings.metadata, sources=linking.SEARCH_ORDER)
    jobs = DataJobs(env.settings, None, None, lambda c: MetadataService(meta, c, fetcher=env.fetcher), (),
                    lambda c, **kw: works.sync(c))

    class Ctx:
        def progress(self, *a, **kw):
            pass

    assert jobs.background_metadata(Ctx()) is None and env.net.requests == []  # 取得元が無ければ何もしない
    put(env.client, only_group(env), ["mediaarts"])
    assert jobs.background_metadata(Ctx()) == {"works": 1}
    assert env.net.count(MEDIAARTS) >= 1 and env.net.count(NDL) == 0 and env.net.count(BANGUMI_SEARCH) == 0
    assert jobs.active_sources() == ("mediaarts",)


def test_full_sync_skips_the_metadata_step_only_when_no_group_has_a_source(make_settings, tmp_path):
    from test_jobs import wait_job
    env = Env(make_settings, tmp_path, network("kaku"), KAKU_FILES, sources=())

    def full_sync():
        job = wait_job(env.client, env.client.post("/api/jobs", json={"kind": "full_sync"}).json()["id"])
        assert job["status"] == "succeeded", job
        return job["summary"]

    def catalog():
        return {k["kind"]: k for k in env.client.get("/api/jobs/catalog").json()["kinds"]}

    assert "metadata" not in full_sync() and env.net.requests == []
    assert catalog()["metadata"]["available"] is False and catalog()["follow_check"]["available"] is False
    put(env.client, only_group(env), ["bangumi"])  # 設定は空のまま、蔵書で有効にする
    assert full_sync()["metadata"]["sources"] == {"linked": 1}
    assert env.net.count(BANGUMI_SEARCH) >= 1 and env.net.count(NDL) == 0
    kinds = catalog()
    assert kinds["metadata"]["available"] is True and kinds["metadata"]["count"] == 0
    assert kinds["follow_check"]["available"] is False and "国立国会図書館サーチ" in kinds["follow_check"]["unavailable_reason"]


# --- フォロー -----------------------------------------------------------------------------------

def test_follow_check_does_not_ask_ndl_for_a_work_whose_group_leaves_it_out(make_settings, tmp_path):
    env = FollowEnv(make_settings, tmp_path, "kaku", kaku_files(1, 2, 3, 4))
    wid = env.work_id()
    env.follow(wid)
    [g] = env.client.get("/api/libraries").json()
    put(env.client, g["id"], ["mediaarts", "ndl"])
    assert env.follows()["available"] is True
    assert env.check()["summary"] == {"works": 1, "statuses": {"ok": 1}}
    asked = env.net.count(NDL)
    assert asked >= 1

    put(env.client, g["id"], ["mediaarts"])  # 国立国会図書館サーチを外す
    assert env.follows()["available"] is False
    r = env.client.post("/api/follows/check?wait=true")
    assert r.status_code == 409 and "国立国会図書館サーチ" in r.json()["detail"]
    assert env.net.count(NDL) == asked
    assert [w["work_id"] for w in env.releases()] == [wid]  # 見つけてあった続刊は残る


def test_follow_check_marks_only_the_work_without_ndl_as_disabled(make_settings, tmp_path):
    """蔵書が 2 つで片方だけ国立国会図書館サーチを外しているとき、その蔵書の作品だけ問い合わせない。"""
    env = FollowEnv(make_settings, tmp_path, "kaku", kaku_files(1, 2, 3, 4))
    wid = env.work_id()
    env.follow(wid)
    [g] = env.client.get("/api/libraries").json()
    other = env.client.post("/api/library-groups", json={"name": "ほか"}).json()["id"]  # 指定なし = 既定(ndl あり)
    put(env.client, g["id"], ["bangumi"])
    assert env.follows()["available"] is True  # 「ほか」の蔵書が既定で ndl を使う
    assert env.check()["summary"] == {"works": 1, "statuses": {"disabled": 1}}
    assert env.net.count(NDL) == 0 and env.entry(wid)["status"] == "disabled"
    assert other
