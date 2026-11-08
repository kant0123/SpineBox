"""公開 DB の情報の結線(#50)。ネットワークには出ない(#44 の記録した応答 + 偽の取得元)。

蔵書は一時ディレクトリに動的生成する。取得は `HttpFetcher` の時計と sleep を差し替えて待たない。
"""
import dataclasses
import json
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi.testclient import TestClient

from conftest import page_bytes, write_zip
from metadata_fakes import ANILIST, MU_SEARCH, NDL, FakeClock, FakeNetwork, fixture, recorded_network
from spinebox import db, linking, works
from spinebox.app import create_app
from spinebox.config import MetadataSettings, load_settings
from spinebox.metadata.http import HttpFetcher

KAKU = "かくかくしかじか"
OKIRAKU = "お気楽領主の楽しい領地防衛"


def cbz(lib, name, pages=3):
    write_zip(lib / name, [(f"{i:03d}.jpg", page_bytes(f"{name}/{i}")) for i in range(pages)])


def network(label: str) -> FakeNetwork:
    """記録した応答を返す偽の取得元。国立国会図書館サーチは、かくかくしかじかだけ著者つきの記録を返す。"""
    net = recorded_network(label)

    def ndl(request: httpx.Request):
        query = parse_qs(urlparse(str(request.url)).query)
        if label == "kaku" and "creator" in query:
            return fixture("ndl/kakuauthor-search.xml")
        return fixture(f"ndl/{label}-search.xml")

    net.route("GET", NDL, ndl)
    return net


class Env:
    def __init__(self, make_settings, tmp_path, net: FakeNetwork, files, **metadata):
        self.lib = tmp_path / "lib"
        self.lib.mkdir(exist_ok=True)
        for f in files:
            cbz(self.lib, f)
        meta = MetadataSettings(max_retries=0, **metadata)
        self.settings = dataclasses.replace(make_settings(self.lib), metadata=meta)
        self.net = net
        self.clock = FakeClock()
        self.fetcher = HttpFetcher(meta, transport=net.transport, clock=self.clock, sleep=self.clock.sleep)
        self.client = TestClient(create_app(self.settings, frontend_dist=None, metadata_fetcher=self.fetcher))
        self.client.post("/api/scan")

    def work_id(self, title=None):
        ws = self.client.get("/api/works").json()["works"]
        if title is None:
            assert len(ws) == 1, [w["title"] for w in ws]
            return ws[0]["id"]
        return next(w["id"] for w in ws if w["title"] == title)

    def fetch(self, work_id):
        r = self.client.post(f"/api/works/{work_id}/metadata/fetch")
        assert r.status_code == 200, r.text
        return r.json()

    def work(self, work_id):
        r = self.client.get(f"/api/works/{work_id}")
        assert r.status_code == 200, r.text
        return r.json()

    def link(self, work, source):
        return next(x for x in work["links"] if x["source"] == source)

    def volumes(self, work):
        return [v for s in work["slots"] for v in s["volumes"]]

    def rows(self, sql, *args):
        conn = db.connect(self.settings.db_path)
        try:
            return conn.execute(sql, args).fetchall()
        finally:
            conn.close()


@pytest.fixture
def env_factory(make_settings, tmp_path):
    def make(label, files, **metadata):
        return Env(make_settings, tmp_path, network(label), files, **metadata)
    return make


KAKU_FILES = [f"[東村アキコ] {KAKU} 第0{n}巻.cbz" for n in (1, 2, 3, 4)]
OKIRAKU_FILES = [f"{OKIRAKU} 第0{n}巻.cbz" for n in (1, 2, 3)]


# --- 埋まる -------------------------------------------------------------------------------


def test_kakukaku_is_linked_automatically_and_fills_work_and_volume_fields(env_factory):
    env = env_factory("kaku", KAKU_FILES)
    wid = env.work_id()
    w = env.fetch(wid)

    assert [env.link(w, s)["status"] for s in ("ndl", "anilist", "mangaupdates")] == ["linked"] * 3
    f = w["fields"]
    assert f["title"]["value"] == KAKU
    assert (f["author"]["value"], f["author"]["manual"]) == ("東村アキコ", False)
    assert {k: f["kind"][k] for k in ("value", "source", "manual")} == {"value": "manga", "source": "public_db", "manual": False}
    assert f["kind"]["sources"]
    assert f["publisher"]["value"] == "集英社" and f["publisher"]["source"] == "public_db"
    # AniList + MangaUpdates の和。英語の語彙は日本語にして入る(#74)
    assert "日常" in f["genres"]["value"] and "女性向け" in f["genres"]["value"]
    assert "成長" in f["tags"]["value"] and f["tags"]["source"] == "public_db"
    assert f["total_volumes"]["value"] == 5
    assert "synopsis" not in f  # あらすじは廃止(#82)。取得元にあっても作品の項目に入れない
    assert w["kind"] == "manga" and w["genres"] and w["tags"]
    # ローマ字名・別名が別名に入る(出所つき)
    aliases = {a["alias"]: a for a in w["aliases"]}
    assert aliases["Kakukaku Shikajika"]["source"] == "public_db"

    # 巻単位: 名前から読んだ巻番号ごとに、国立国会図書館サーチの書誌が入る
    vols = {v["volume_number"]["start"]: v for v in env.volumes(w)}
    assert set(vols) == {1, 2, 3, 4}
    m1 = vols[1]["metadata"]
    assert m1["publisher"]["value"] == "集英社" and m1["publisher"]["source"] == "public_db"
    assert m1["isbn"]["value"] == "9784087824575" and m1["released"]["value"] == "2012-07"
    assert vols[2]["metadata"]["isbn"]["value"] == "9784087826531"
    assert m1["isbn"]["confidence"] and not m1["isbn"]["manual"]


def test_okiraku_manga_and_novel_are_not_adopted_automatically(env_factory):
    env = env_factory("okiraku", OKIRAKU_FILES)
    wid = env.work_id()
    w = env.fetch(wid)

    ndl = env.link(w, "ndl")
    assert ndl["status"] == "pending" and ndl["linked"] is None
    kinds = sorted(c["kind"] for c in ndl["candidates"])
    assert kinds == ["manga", "novel"]                      # 漫画と小説が同名で並ぶ
    assert all(c["score"] >= linking.SHOW_MIN for c in ndl["candidates"])
    # 確認待ちの間は、公開 DB の値を何も入れない。漫画か小説かが決まるまで、ほかの取得元も自動で連携しない
    assert all(v["source"] != "public_db" for v in w["fields"].values())
    assert env.link(w, "mangaupdates")["status"] == "pending" and env.link(w, "mangaupdates")["hold"] == "kind_unresolved"
    assert w["link_pending"] == 3
    assert all("isbn" not in v["metadata"] for v in env.volumes(w))

    # 選ぶと手動の決定として固定され、項目と巻の書誌が埋まる
    manga = next(c for c in ndl["candidates"] if c["kind"] == "manga")
    r = env.client.post(f"/api/works/{wid}/links/ndl/choose", json={"source_id": manga["source_id"]})
    assert r.status_code == 200
    w = r.json()
    ndl = env.link(w, "ndl")
    assert (ndl["status"], ndl["manual"], ndl["decision"]) == ("linked", True, "manual")
    assert w["fields"]["kind"]["value"] == "manga"
    assert w["fields"]["author"]["value"] == "青色まろ、赤池宗"
    assert w["fields"]["publisher"]["value"] == "オーバーラップ"
    vols = {v["volume_number"]["start"]: v for v in env.volumes(w)}
    assert vols[1]["metadata"]["isbn"]["value"] == "9784824001177"
    assert vols[1]["metadata"]["label"]["value"] == "ガルドコミックス"
    assert vols[2]["metadata"]["released"]["value"] == "2022-08"

    # 再取得しても、選んだ連携は変わらない。種別が分かったので、AniList / MangaUpdates の漫画だけが残り自動で連携する
    w = env.fetch(wid)
    assert env.link(w, "ndl")["manual"] and env.link(w, "ndl")["source_id"] == manga["source_id"]
    assert env.link(w, "anilist")["status"] == "linked" and env.link(w, "mangaupdates")["status"] == "linked"
    assert any(t for t in w["fields"]["tags"]["value"])
    assert w["fields"]["genres"]["value"]


# --- 手動の項目は何があっても変わらない -----------------------------------------------------------


WORK_EDITS = {
    "title": "手で直した作品名", "author": "手で直した著者", "kind": "novel", "publisher": "手で直した出版社",
    "genres": ["手動ジャンル"], "tags": ["手動タグ1", "手動タグ2"],
}
VOLUME_EDITS = {"publisher": "手で直した巻の出版社", "label": "手動レーベル", "released": "1999-09", "isbn": "9784000000000"}


def churn(env, wid):
    """再取得・再スキャン・再計算・全体の補完。手動の項目はどれでも変わってはいけない。"""
    env.fetch(wid)
    assert env.client.post("/api/scan").status_code == 200
    assert env.client.post("/api/works/refresh").status_code == 200
    assert env.client.post("/api/works/metadata/fetch?wait=true&all=true").status_code == 200
    env.fetch(wid)


@pytest.mark.parametrize("field", list(WORK_EDITS))
@pytest.mark.parametrize("edit_first", [True, False], ids=["manual-before-fetch", "manual-after-fetch"])
def test_manually_edited_work_field_never_changes(env_factory, field, edit_first):
    env = env_factory("kaku", KAKU_FILES)
    wid = env.work_id()
    if not edit_first:
        env.fetch(wid)
    r = env.client.patch(f"/api/works/{wid}", json={field: WORK_EDITS[field]})
    assert r.status_code == 200, r.text
    wid = r.json()["id"]
    churn(env, wid)
    f = env.work(wid)["fields"][field]
    assert (f["value"], f["manual"], f["source"]) == (WORK_EDITS[field], True, "manual")


@pytest.mark.parametrize("field", ["author", "kind", "publisher", "genres", "tags"])
def test_field_emptied_by_hand_stays_empty(env_factory, field):
    env = env_factory("kaku", KAKU_FILES)
    wid = env.work_id()
    env.fetch(wid)
    assert env.work(wid)["fields"][field]["value"]  # 取得で埋まっていた
    empty = [] if field in ("genres", "tags") else None
    assert env.client.patch(f"/api/works/{wid}", json={field: empty}).status_code == 200
    churn(env, wid)
    f = env.work(wid)["fields"][field]
    assert f["manual"] and not f["value"]


@pytest.mark.parametrize("field", list(VOLUME_EDITS))
@pytest.mark.parametrize("edit_first", [True, False], ids=["manual-before-fetch", "manual-after-fetch"])
def test_manually_edited_volume_field_never_changes(env_factory, field, edit_first):
    env = env_factory("kaku", KAKU_FILES)
    wid = env.work_id()
    if not edit_first:
        env.fetch(wid)
    vols = env.volumes(env.work(wid))
    vol, other = vols[0], vols[1]
    r = env.client.patch(f"/api/volumes/{vol['id']}/metadata", json={field: VOLUME_EDITS[field]})
    assert r.status_code == 200, r.text
    churn(env, wid)
    w = env.work(wid)
    v = next(x for x in env.volumes(w) if x["id"] == vol["id"])
    assert (v["metadata"][field]["value"], v["metadata"][field]["manual"]) == (VOLUME_EDITS[field], True)
    # 手で直していない巻や項目は、公開 DB の値で埋まる
    o = next(x for x in env.volumes(w) if x["id"] == other["id"])
    assert o["metadata"]["isbn"]["source"] == "public_db"
    assert all(v["metadata"][f]["source"] == "public_db" for f in ("isbn", "released") if f != field)


def test_manual_title_survives_while_other_fields_are_still_filled(env_factory):
    env = env_factory("kaku", KAKU_FILES)
    wid = env.work_id()
    env.client.patch(f"/api/works/{wid}", json={"title": "手で直した作品名"})
    w = env.fetch(wid)
    assert w["title"] == "手で直した作品名" and w["title_manual"] is True
    assert w["fields"]["title"]["source"] == "manual"


def test_manual_link_choice_is_not_changed_by_refetch(env_factory):
    env = env_factory("okiraku", OKIRAKU_FILES)
    wid = env.work_id()
    ndl = env.link(env.fetch(wid), "ndl")
    novel = next(c for c in ndl["candidates"] if c["kind"] == "novel")
    env.client.post(f"/api/works/{wid}/links/ndl/choose", json={"source_id": novel["source_id"]})
    churn(env, wid)
    w = env.work(wid)
    link = env.link(w, "ndl")
    assert (link["status"], link["manual"], link["source_id"]) == ("linked", True, novel["source_id"])
    assert w["fields"]["kind"]["value"] == "novel" and w["fields"]["author"]["value"] == "赤池宗"


def test_choosing_none_is_remembered_and_not_requested_again(env_factory):
    env = env_factory("okiraku", OKIRAKU_FILES)
    wid = env.work_id()
    env.fetch(wid)
    w = env.client.post(f"/api/works/{wid}/links/ndl/choose", json={"source_id": None}).json()
    assert (env.link(w, "ndl")["status"], env.link(w, "ndl")["manual"]) == ("none", True)
    before = env.net.count(NDL)
    churn(env, wid)
    assert env.net.count(NDL) == before                      # 該当なしにした取得元には要求を出さない
    assert env.link(env.work(wid), "ndl")["status"] == "none"
    # 手動を解除すると、保存済みの候補から自動の規則で決め直す。ほかの取得元で種別(漫画)が決まっているので、
    # 種別の違う候補は除かれ、再取得したときと同じ結果になる
    w = env.client.post(f"/api/works/{wid}/links/ndl/reset").json()
    reset = env.link(w, "ndl")
    assert reset["manual"] is False and reset["status"] == "linked" and reset["linked"]["kind"] == "manga"
    again = env.link(env.fetch(wid), "ndl")
    assert (again["status"], again["source_id"]) == (reset["status"], reset["source_id"])


def test_reset_to_automatic_restores_previous_value_or_public_db(env_factory):
    env = env_factory("kaku", KAKU_FILES)
    wid = env.work_id()
    w = env.fetch(wid)
    original = {k: w["fields"][k]["value"] for k in ("author", "publisher", "genres", "title")}
    env.client.patch(f"/api/works/{wid}", json={"author": "誰か", "publisher": "どこか", "genres": ["x"], "title": "別名にする"})
    assert env.work(wid)["fields"]["publisher"]["manual"]
    for field in ("author", "publisher", "genres", "title"):
        r = env.client.post(f"/api/works/{wid}/fields/{field}/reset")
        assert r.status_code == 200, r.text
    w = env.work(wid)
    for field, value in original.items():
        assert w["fields"][field]["value"] == value and not w["fields"][field]["manual"], field
    assert w["fields"]["publisher"]["source"] == "public_db"
    # 手動でないものは戻せない
    assert env.client.post(f"/api/works/{wid}/fields/publisher/reset").status_code == 409
    assert env.client.post(f"/api/works/{wid}/fields/nonsense/reset").status_code == 422


def test_reset_volume_field(env_factory):
    env = env_factory("kaku", KAKU_FILES)
    wid = env.work_id()
    w = env.fetch(wid)
    vol = env.volumes(w)[0]
    auto = vol["metadata"]["isbn"]["value"]
    edited = env.client.patch(f"/api/volumes/{vol['id']}/metadata", json={"isbn": "978-4-00-000000-0"}).json()
    assert edited["metadata"]["isbn"]["value"] == "9784000000000"        # ハイフンは除いて保存
    r = env.client.post(f"/api/volumes/{vol['id']}/metadata/isbn/reset").json()
    assert r["metadata"]["isbn"]["value"] == auto and not r["metadata"]["isbn"]["manual"]
    assert env.client.post(f"/api/volumes/{vol['id']}/metadata/isbn/reset").status_code == 409


# --- 取得元が落ちていても止まらない --------------------------------------------------------------


FAILURES = {
    "timeout": (httpx.ReadTimeout("slow"), "timeout"),
    "http500": (httpx.Response(500, content=b"boom"), "http_status"),
    "connect": (httpx.ConnectError("down"), "connect"),
    "rate_limited": (httpx.Response(429, headers={"Retry-After": "120"}, content=b"slow down"), "rate_limited"),
}


def fail_all(env, response):
    for prefix, method in ((NDL, "GET"), (ANILIST, "POST"), (MU_SEARCH, "POST")):
        env.net.route(method, prefix, response)


@pytest.mark.parametrize("name", list(FAILURES))
def test_scan_and_browsing_succeed_when_every_source_is_down(env_factory, name):
    response, kind = FAILURES[name]
    env = env_factory("kaku", KAKU_FILES)
    fail_all(env, response)
    wid = env.work_id()
    w = env.fetch(wid)                                       # 取得の失敗は 200 で返り、作品に記録される
    for source in ("ndl", "anilist", "mangaupdates"):
        link = env.link(w, source)
        assert link["status"] is None and link["failure"]["kind"] == kind, (source, link)
        assert link["failure"]["message"] and link["attempted_at"] and not link["checked_at"]
    assert all(v["source"] != "public_db" for v in w["fields"].values())
    # スキャンと閲覧の API は止まらない
    assert env.client.post("/api/scan").status_code == 200
    assert env.client.get("/api/volumes").status_code == 200
    assert env.client.get("/api/works").status_code == 200
    assert env.client.get(f"/api/works/{wid}").status_code == 200
    assert env.client.post("/api/works/refresh").status_code == 200
    # 全体の補完も、失敗を作品に記録して最後まで進む
    r = env.client.post("/api/works/metadata/fetch?wait=true&all=true")
    assert r.status_code == 200 and r.json()["error"] is None and r.json()["summary"]["sources"]["failed"] == 3
    assert env.link(env.work(wid), "ndl")["failure"]["kind"] == kind


def test_one_source_down_does_not_stop_the_others(env_factory):
    env = env_factory("kaku", KAKU_FILES)
    env.net.route("POST", ANILIST, httpx.Response(503, content=b"unavailable"))
    w = env.fetch(env.work_id())
    assert env.link(w, "ndl")["status"] == "linked" and env.link(w, "mangaupdates")["status"] == "linked"
    assert env.link(w, "anilist")["failure"]["kind"] == "http_status" and env.link(w, "anilist")["failure"]["status"] == 503
    assert w["fields"]["publisher"]["value"] == "集英社" and w["fields"]["genres"]["value"]


def test_failure_after_success_keeps_the_link_and_values(env_factory):
    env = env_factory("kaku", KAKU_FILES)
    wid = env.work_id()
    before = env.fetch(wid)
    saved = list(env.net.routes)
    conn = db.connect(env.settings.db_path)
    conn.execute("DELETE FROM metadata_responses")           # 保存済みの応答も無い状態で、取得元が落ちる
    conn.close()
    fail_all(env, httpx.Response(500, content=b"boom"))
    after = env.fetch(wid)
    for source in ("ndl", "anilist", "mangaupdates"):
        link = env.link(after, source)
        assert link["status"] == "linked" and link["failure"]["kind"] == "http_status"   # 連携は残り、失敗が記録される
        assert link["checked_at"] == env.link(before, source)["checked_at"]
    assert {k: v["value"] for k, v in after["fields"].items()} == {k: v["value"] for k, v in before["fields"].items()}
    env.net.routes[:] = saved                                  # 回復したら失敗の記録は消える
    assert env.link(env.fetch(wid), "ndl")["failure"] is None


def test_expired_stored_response_is_used_when_the_source_is_down(env_factory):
    env = env_factory("kaku", KAKU_FILES, cache_ttl_days=0.0)   # 保存済みの応答をすぐ期限切れにする
    wid = env.work_id()
    env.fetch(wid)
    fail_all(env, httpx.Response(500, content=b"boom"))
    w = env.fetch(wid)
    link = env.link(w, "ndl")
    assert link["status"] == "linked" and link["failure"] is None
    assert any(r["stale"] for r in link["responses"])         # どの応答を、期限切れのまま使ったかが残る


def test_scan_and_browsing_never_request_the_sources(env_factory):
    env = env_factory("kaku", KAKU_FILES)
    env.client.post("/api/scan")
    env.client.post("/api/works/refresh")
    env.client.get("/api/volumes")
    env.client.get("/api/works")
    env.client.get(f"/api/works/{env.work_id()}")
    assert env.net.requests == []                            # 明示操作以外では、取得元に要求を出さない


def test_unexpected_error_while_fetching_is_recorded_not_raised(env_factory, monkeypatch):
    env = env_factory("kaku", KAKU_FILES)
    wid = env.work_id()

    def boom(*a, **k):
        raise RuntimeError("bug")

    monkeypatch.setattr(linking, "candidates_for", boom)
    w = env.fetch(wid)
    assert env.link(w, "ndl")["failure"]["kind"] == "internal"
    assert env.client.get("/api/volumes").status_code == 200


def test_ndl_is_asked_for_many_records_at_once(env_factory):
    env = env_factory("kaku", KAKU_FILES)
    env.fetch(env.work_id())
    ndl = next(r for r in env.net.requests if str(r.url).startswith(NDL))
    assert ndl.url.params["cnt"] == str(linking.NDL_LIMIT)


# --- 空欄だけを埋める・名前由来の値は確信度で置き換える ---------------------------------------------


def put_link(env, wid, source, item, *, confidence, manual=False, status="linked"):
    conn = db.connect(env.settings.db_path)
    try:
        with db.transaction(conn) as c:
            c.execute("DELETE FROM work_links WHERE work_id = ? AND source = ?", (wid, source))
            c.execute("""INSERT INTO work_links (work_id, source, status, source_id, manual, confidence, item)
                         VALUES (?, ?, ?, 'x', ?, ?, ?)""", (wid, source, status, int(manual), confidence, json.dumps(item)))
        linking.apply_work(conn, wid)
    finally:
        conn.close()


ANILIST_ITEM = {"title": "公開DBの作品名", "authors": ["公開DBの著者"], "kind": "manga", "genres": ["Drama"],
                "tags": ["Tag"], "synopsis": "あらすじ", "romaji": "Koukai DB no Sakuhin", "aliases": []}
ANILIST_ITEM_ROMAJI = {"title": "Koukai DB no Sakuhin", "authors": ["Koukai DB Author"], "kind": "manga",
                       "genres": ["Drama"], "tags": ["Tag"], "synopsis": "あらすじ", "aliases": []}


@pytest.mark.parametrize("confidence, manual, replaced", [(0.95, False, True), (0.8, False, False), (0.8, True, True)])
def test_name_derived_values_are_replaced_only_when_confident_or_chosen(env_factory, confidence, manual, replaced):
    env = env_factory("kaku", ["[ファイル名の著者] 作品名X 第01巻.cbz", "[ファイル名の著者] 作品名X 第02巻.cbz"])
    wid = env.work_id()
    assert env.work(wid)["fields"]["author"]["source"] == "filename"
    put_link(env, wid, "anilist", ANILIST_ITEM_ROMAJI, confidence=confidence, manual=manual)
    f = env.work(wid)["fields"]
    # 空欄は、確信度が低くても埋める
    assert f["kind"]["value"] == "manga" and f["genres"]["value"] == ["ドラマ"]
    assert "synopsis" not in f  # 連携した候補の中身にあらすじがあっても入れない(#82)
    if replaced:
        assert (f["author"]["value"], f["author"]["source"]) == ("Koukai DB Author", "public_db")
        assert f["title"]["value"] == "Koukai DB no Sakuhin"
        # 置き換えた作品名は別名に残る(その名前の巻が、また新しい作品にならない)
        assert "作品名X" in [a["alias"] for a in env.work(wid)["aliases"]]
    else:
        assert (f["author"]["value"], f["author"]["source"]) == ("ファイル名の著者", "filename")
        assert f["title"]["value"] == "作品名X"
    # 連携が外れたら、置き換える前の値に戻り、公開 DB から入れた値は消える
    conn = db.connect(env.settings.db_path)
    conn.execute("UPDATE work_links SET status = 'none', item = NULL WHERE work_id = ?", (wid,))
    linking.apply_work(conn, wid)
    conn.close()
    f = env.work(wid)["fields"]
    assert (f["author"]["value"], f["author"]["source"]) == ("ファイル名の著者", "filename")
    assert f["title"]["value"] == "作品名X"
    assert "kind" not in f and "genres" not in f and "synopsis" not in f


def test_japanese_title_and_author_are_preferred_over_filename_at_low_confidence(env_factory):
    """日本語の作品名・著者は確信度 0.9 未満でもファイル名由来の値を上書きする(#166)。"""
    env = env_factory("kaku", ["[File Author] Atumare Fushigi Kenkyubu 第01巻.cbz",
                                "[File Author] Atumare Fushigi Kenkyubu 第02巻.cbz"])
    wid = env.work_id()
    assert env.work(wid)["title"] == "Atumare Fushigi Kenkyubu"
    put_link(env, wid, "anilist", ANILIST_ITEM, confidence=0.8)
    w = env.work(wid)
    assert w["title"] == "公開DBの作品名" and w["title_source"] == "public_db"
    assert w["fields"]["author"]["value"] == "公開DBの著者"
    # 置き換えたローマ字の作品名は別名に残る
    assert "Atumare Fushigi Kenkyubu" in [a["alias"] for a in w["aliases"]]
    # 一覧 API も日本語の作品名を返す(ホーム・漫画・小説はこの一覧)
    titles = [x["title"] for x in env.client.get("/api/works").json()["works"]]
    assert titles == ["公開DBの作品名"]
    # 連携が外れたら元のファイル名の作品名に戻る
    conn = db.connect(env.settings.db_path)
    conn.execute("UPDATE work_links SET status = 'none', item = NULL WHERE work_id = ?", (wid,))
    linking.apply_work(conn, wid)
    conn.close()
    assert env.work(wid)["title"] == "Atumare Fushigi Kenkyubu"
    assert env.work(wid)["fields"]["author"]["value"] == "File Author"


def test_japanese_filename_title_and_author_are_not_overwritten_at_low_confidence(env_factory):
    """日本語同士の置き換えは確信度 0.9 の規則(低確信度の別作品で正しい日本語を上書きしない)。"""
    env = env_factory("kaku", ["[ファイル名の著者] かくかくしかじか 第01巻.cbz"])
    wid = env.work_id()
    assert env.work(wid)["title"] == "かくかくしかじか"
    put_link(env, wid, "anilist", ANILIST_ITEM, confidence=0.8)
    w = env.work(wid)
    assert w["title"] == "かくかくしかじか" and w["fields"]["author"]["value"] == "ファイル名の著者"
    # 確信度が高いときは置き換える
    put_link(env, wid, "anilist", ANILIST_ITEM, confidence=0.95)
    w = env.work(wid)
    assert w["title"] == "公開DBの作品名" and w["fields"]["author"]["value"] == "公開DBの著者"


def test_manual_title_and_author_are_not_overwritten_by_japanese_public_db(env_factory):
    env = env_factory("kaku", ["[ファイル名の著者] Atumare Fushigi Kenkyubu 第01巻.cbz"])
    wid = env.work_id()
    env.client.patch(f"/api/works/{wid}", json={"title": "手で直した作品名", "author": "手で直した著者"})
    put_link(env, wid, "anilist", ANILIST_ITEM, confidence=0.8)
    w = env.work(wid)
    assert w["title"] == "手で直した作品名" and w["title_manual"] is True
    assert w["fields"]["author"]["value"] == "手で直した著者"


def test_apply_is_idempotent_and_writes_nothing_when_unchanged(env_factory):
    env = env_factory("kaku", KAKU_FILES)
    wid = env.work_id()
    env.fetch(wid)
    snapshot = env.rows("SELECT * FROM work_fields ORDER BY work_id, field") + env.rows("SELECT * FROM volume_fields ORDER BY 1, 2")
    conn = db.connect(env.settings.db_path)
    linking.apply_work(conn, wid)
    assert linking.apply_all(conn) == 1
    conn.close()
    assert env.rows("SELECT * FROM work_fields ORDER BY work_id, field") + env.rows("SELECT * FROM volume_fields ORDER BY 1, 2") == snapshot


def test_volume_fields_skip_volumes_whose_number_disagrees_with_the_contents(env_factory):
    env = env_factory("kaku", ["[東村アキコ] かくかくしかじか 第01巻.cbz", "[東村アキコ] かくかくしかじか 第02巻.cbz",
                               "[東村アキコ] かくかくしかじか 第01-02巻.cbz", "[東村アキコ] かくかくしかじか 第2話.cbz"])
    wid = env.work_id()
    w = env.fetch(wid)
    by_label = {s["label"]: s["volumes"] for s in w["slots"]}
    assert by_label["1巻"][0]["metadata"]["isbn"]["value"] == "9784087824575"
    assert "isbn" not in by_label["1〜2巻"][0]["metadata"]      # 範囲の巻には入れない
    assert "isbn" not in by_label["2話"][0]["metadata"]         # 話にも入れない


def test_duplicate_scans_of_the_same_number_both_get_the_record(env_factory):
    env = env_factory("kaku", [*KAKU_FILES, "[東村アキコ] かくかくしかじか v01.cbz"])
    w = env.fetch(env.work_id())
    slot = next(s for s in w["slots"] if s["label"] == "1巻")
    assert len(slot["volumes"]) == 2 and all(v["metadata"]["isbn"]["value"] == "9784087824575" for v in slot["volumes"])


# --- 別名・統合 ---------------------------------------------------------------------------------


def test_public_db_name_merges_a_romaji_work_into_the_japanese_one(env_factory):
    env = env_factory("kaku", ["Kakukaku Shikajika v01.cbz", "Kakukaku Shikajika v02.cbz",
                               f"{KAKU} 第03巻.cbz", f"{KAKU} 第04巻.cbz"])
    ws = {w["title"]: w["id"] for w in env.client.get("/api/works").json()["works"]}
    assert set(ws) == {"Kakukaku Shikajika", KAKU}
    w = env.fetch(ws["Kakukaku Shikajika"])
    # AniList のローマ字名で連携 → 作品名が日本語名に置き換わり(確信度が高い)、もう一方の作品名と一致して自動で統合される
    assert w["title"] == KAKU and w["volume_count"] == 4 and w["merged"]
    assert any(m["rule"] == "alias" and m["status"] == "auto" for m in w["merges"])
    assert "Kakukaku Shikajika" in [a["alias"] for a in w["aliases"]]       # 置き換えた名前は別名に残る
    assert env.link(w, "anilist")["status"] == "linked"
    assert len(env.client.get("/api/works").json()["works"]) == 1
    absorbed = ws[KAKU] if w["id"] == ws["Kakukaku Shikajika"] else ws["Kakukaku Shikajika"]
    assert env.client.get(f"/api/works/{absorbed}").status_code == 404


def test_public_db_aliases_alone_only_propose_when_the_link_is_weak(env_factory):
    env = env_factory("kaku", ["Kakukaku Shikajika v01.cbz", f"{KAKU} 第03巻.cbz"])
    ws = {w["title"]: w["id"] for w in env.client.get("/api/works").json()["works"]}
    # 確信度の低い連携の別名は、自動統合の基準(0.9)に届かず、提案止まり
    # 日本語の作品名なら #166 でタイトルが置き換わり作品が合くため、別名だけを試す(タイトルはローマ字のまま)
    put_link(env, ws["Kakukaku Shikajika"], "anilist",
             {"title": "Kakukaku Shikajika", "romaji": "Kakukaku Shikajika", "aliases": [KAKU], "authors": []}, confidence=0.8)
    env.client.post("/api/works/refresh")
    works_now = {w["title"]: w for w in env.client.get("/api/works").json()["works"]}
    assert set(works_now) == {"Kakukaku Shikajika", KAKU}
    proposals = env.client.get("/api/works/proposals").json()
    assert len(proposals) == 1 and proposals[0]["rule"] == "alias" and proposals[0]["confidence"] == 0.8


def test_merge_keeps_the_manual_values_and_links_of_the_absorbed_work(env_factory):
    env = env_factory("kaku", ["Kakukaku Shikajika v01.cbz", f"{KAKU} 第03巻.cbz"])
    ws = {w["title"]: w["id"] for w in env.client.get("/api/works").json()["works"]}
    romaji, jp = ws["Kakukaku Shikajika"], ws[KAKU]
    env.client.patch(f"/api/works/{romaji}", json={"publisher": "手動の出版社", "genres": ["手動"]})
    env.client.patch(f"/api/works/{jp}", json={"kind": "manga"})
    w = env.client.post(f"/api/works/{jp}/merge", json={"other_id": romaji}).json()
    assert w["fields"]["publisher"] == {"value": "手動の出版社", "source": "manual", "manual": True, "confidence": 1.0, "sources": []}
    assert w["fields"]["genres"]["value"] == ["手動"] and w["fields"]["kind"]["manual"]


# --- 取得の対象を選ぶ ----------------------------------------------------------------------------


def test_works_needing_fetch_orders_never_fetched_first_and_honours_the_waits(env_factory):
    from datetime import datetime, timedelta, timezone
    env = env_factory("kaku", KAKU_FILES)
    wid = env.work_id()
    meta = env.settings.metadata
    enabled = ("ndl", "anilist", "mangaupdates")
    conn = db.connect(env.settings.db_path)
    now = datetime.now(timezone.utc)
    assert linking.works_needing_fetch(conn, meta, enabled, now) == [wid]            # 一度も取得していない
    assert linking.works_needing_fetch(conn, meta, (), now) == []                    # 取得元が無ければ何もしない
    conn.close()
    env.fetch(wid)
    conn = db.connect(env.settings.db_path)
    assert linking.works_needing_fetch(conn, meta, enabled, now) == []
    later = now + timedelta(days=meta.recheck_days, minutes=1)
    assert linking.works_needing_fetch(conn, meta, enabled, later) == [wid]          # 取得済みも期限が来れば取り直す
    # 失敗は retry_hours 後に取り直す(取得済みの期限よりずっと早い)
    conn.execute("UPDATE work_links SET failure = '{\"kind\": \"timeout\"}' WHERE source = 'ndl'")
    soon = now + timedelta(hours=meta.retry_hours, minutes=1)
    assert linking.works_needing_fetch(conn, meta, enabled, soon) == [wid]
    assert linking.works_needing_fetch(conn, meta, enabled, now + timedelta(hours=1)) == []
    # 利用者が「該当なし」にした取得元は取り直さない
    conn.execute("UPDATE work_links SET failure = NULL, manual = 1, status = 'none' WHERE source = 'ndl'")
    assert linking.works_needing_fetch(conn, meta, ("ndl",), later) == []
    conn.close()


def test_global_fetch_state_and_disabled_sources(env_factory):
    env = env_factory("kaku", KAKU_FILES)
    status = env.client.get("/api/works/metadata").json()
    assert status["sources"] == ["ndl", "anilist", "mangaupdates"] and status["state"]["running"] is False
    r = env.client.post("/api/works/metadata/fetch?wait=true").json()
    assert r["running"] is False and r["error"] is None and r["total"] == 1 and r["done"] == r["total"]
    assert r["summary"]["sources"]["linked"] == 3
    again = env.client.post("/api/works/metadata/fetch?wait=true").json()               # 取得済みは対象外
    assert again["total"] == 0


def test_fetch_is_refused_when_no_source_is_enabled(make_settings, tmp_path):
    env = Env(make_settings, tmp_path, network("kaku"), KAKU_FILES, sources=())
    wid = env.work_id()
    assert env.client.post(f"/api/works/{wid}/metadata/fetch").status_code == 409
    assert env.client.post("/api/works/metadata/fetch").status_code == 409
    assert env.work(wid)["links"] == []
    assert env.net.requests == []


def test_fetch_for_a_missing_work_is_404(env_factory):
    env = env_factory("kaku", KAKU_FILES)
    assert env.client.post("/api/works/999/metadata/fetch").status_code == 404
    assert env.client.post("/api/works/999/links/ndl/choose", json={"source_id": None}).status_code == 404
    assert env.client.post("/api/works/999/fields/title/reset").status_code == 404


# --- 入力の検証 ---------------------------------------------------------------------------------


@pytest.mark.parametrize("body", [{"kind": "comic"}, {"genres": "SF"}, {"tags": [1]}, {"genres": None, "tags": 3}, {}])
def test_invalid_work_edits_are_rejected(env_factory, body):
    env = env_factory("kaku", KAKU_FILES)
    assert env.client.patch(f"/api/works/{env.work_id()}", json=body).status_code == 422


@pytest.mark.parametrize("body", [{"isbn": "12345"}, {"released": "令和"}, {"released": "2020-1"}, {"publisher": 5}, {"label": ["a"]}, {}])
def test_invalid_volume_edits_are_rejected(env_factory, body):
    env = env_factory("kaku", KAKU_FILES)
    vol = env.volumes(env.work(env.work_id()))[0]
    assert env.client.patch(f"/api/volumes/{vol['id']}/metadata", json=body).status_code == 422
    assert env.client.patch("/api/volumes/9999/metadata", json={"publisher": "x"}).status_code == 404


def test_choose_validates_the_candidate(env_factory):
    env = env_factory("okiraku", OKIRAKU_FILES)
    wid = env.work_id()
    assert env.client.post(f"/api/works/{wid}/links/ndl/choose", json={"source_id": "x"}).status_code == 409  # 未取得
    env.fetch(wid)
    assert env.client.post(f"/api/works/{wid}/links/ndl/choose", json={"source_id": "ndl:nonexistent"}).status_code == 422
    assert env.client.post(f"/api/works/{wid}/links/ndl/reset").status_code == 409                          # 手動でない


def test_list_and_detail_expose_kind_genres_tags_and_pending_counts(env_factory):
    env = env_factory("okiraku", OKIRAKU_FILES)
    wid = env.work_id()
    env.fetch(wid)
    (summary,) = env.client.get("/api/works").json()["works"]
    assert summary["link_pending"] == 3 and summary["kind"] is None and summary["genres"] == []
    ndl = env.link(env.work(wid), "ndl")
    manga = next(c for c in ndl["candidates"] if c["kind"] == "manga")
    env.client.post(f"/api/works/{wid}/links/ndl/choose", json={"source_id": manga["source_id"]})
    env.fetch(wid)
    (summary,) = env.client.get("/api/works").json()["works"]
    assert summary["kind"] == "manga" and summary["publisher"] == "オーバーラップ" and summary["link_pending"] == 0
    assert "少年向け" in summary["genres"] and summary["tags"]


def test_review_lists_works_with_pending_candidates_and_counts(env_factory):
    """確認の画面(#207): 確認待ちの作品を作品ページと同じ形で並べ、選ぶと一覧と件数から消える。"""
    env = env_factory("okiraku", OKIRAKU_FILES)
    wid = env.work_id()
    assert env.client.get("/api/review/links").json() == {"works": []}
    assert env.client.get("/api/review/counts").json() == {"links": 0, "proposals": 0, "duplicates": 0, "total": 0}
    env.fetch(wid)

    (item,) = env.client.get("/api/review/links").json()["works"]
    assert item["id"] == wid and item["link_pending"] == 3
    ndl = env.link(item, "ndl")
    assert ndl["status"] == "pending" and len(ndl["candidates"]) >= 2
    assert env.client.get("/api/review/counts").json() == {"links": 1, "proposals": 0, "duplicates": 0, "total": 1}

    manga = next(c for c in ndl["candidates"] if c["kind"] == "manga")
    env.client.post(f"/api/works/{wid}/links/ndl/choose", json={"source_id": manga["source_id"]})
    env.fetch(wid)  # 種別が決まり、保留だった取得元も決まる
    remaining = env.client.get("/api/review/links").json()["works"]
    counts = env.client.get("/api/review/counts").json()
    assert counts["links"] == len(remaining) and counts["total"] == counts["links"]
    assert all(w["link_pending"] > 0 for w in remaining)


# --- 整理(CBZ 化)で巻が置き換わっても、巻の書誌を失わない ------------------------------------------


def test_volume_fields_are_transferred_to_the_new_volume(env_factory):
    env = env_factory("kaku", KAKU_FILES)
    wid = env.work_id()
    w = env.fetch(wid)
    old, new = [v["id"] for v in env.volumes(w)][:2]
    env.client.patch(f"/api/volumes/{old}/metadata", json={"publisher": "手で直した出版社"})
    conn = db.connect(env.settings.db_path)
    with db.transaction(conn) as c:
        c.execute("DELETE FROM volume_fields WHERE volume_id = ?", (new,))
        works.transfer_volume_fields(c, old, new)
    rows = {r["field"]: r for r in conn.execute("SELECT * FROM volume_fields WHERE volume_id = ?", (new,))}
    conn.close()
    assert rows["publisher"]["manual"] == 1 and rows["publisher"]["value"] == "手で直した出版社"
    assert rows["isbn"]["source"] == "public_db"
    # 写し先で手動の項目は、写し元の自動の値で上書きしない
    conn = db.connect(env.settings.db_path)
    with db.transaction(conn) as c:
        c.execute("UPDATE volume_fields SET value = 'auto', manual = 0 WHERE volume_id = ? AND field = 'publisher'", (old,))
        works.transfer_volume_fields(c, old, new)
    kept = conn.execute("SELECT value FROM volume_fields WHERE volume_id = ? AND field = 'publisher'", (new,)).fetchone()[0]
    conn.close()
    assert kept == "手で直した出版社"


# --- agy レビュー(PR #61)で見つかった抜けの回帰 ---------------------------------------------------


def test_a_manually_chosen_link_keeps_the_work_out_of_automatic_merging(env_factory):
    """手動で選んだ連携は手動の決定。強い証拠(同じ作品名)があっても、自動では統合せず提案に留める。"""
    env = env_factory("kaku", ["Kakukaku Shikajika v01.cbz", f"{KAKU} 第03巻.cbz"])
    ws = {w["title"]: w["id"] for w in env.client.get("/api/works").json()["works"]}
    put_link(env, ws["Kakukaku Shikajika"], "anilist",
             {"title": KAKU, "romaji": "Kakukaku Shikajika", "aliases": [], "authors": []}, confidence=0.95, manual=True)
    env.client.post("/api/works/refresh")
    assert len(env.client.get("/api/works").json()["works"]) == 2
    proposals = env.client.get("/api/works/proposals").json()
    assert len(proposals) == 1 and proposals[0]["rule"] == "alias"


def test_public_db_aliases_go_away_with_the_link(env_factory):
    env = env_factory("kaku", KAKU_FILES)
    wid = env.work_id()
    w = env.fetch(wid)
    assert any(a["source"] == "public_db" for a in w["aliases"])
    for source in ("ndl", "anilist", "mangaupdates"):
        r = env.client.post(f"/api/works/{wid}/links/{source}/choose", json={"source_id": None})
        assert r.status_code == 200, r.text
    w = env.work(wid)
    assert [a["alias"] for a in w["aliases"] if a["source"] == "public_db"] == []
    # 手動の別名は残る
    env.client.post(f"/api/works/{wid}/aliases", json={"alias": "手で足した別名"})
    env.client.post(f"/api/works/{wid}/links/anilist/reset")
    assert "手で足した別名" in [a["alias"] for a in env.work(wid)["aliases"]]


def test_approving_a_proposal_fills_the_volumes_that_joined_the_work(env_factory):
    env = env_factory("kaku", ["Kakukaku Shikajika v01.cbz", f"{KAKU} 第03巻.cbz"])
    ws = {w["title"]: w["id"] for w in env.client.get("/api/works").json()["works"]}
    record = {"source_id": "r1", "volume": 1, "volume_label": "1", "title": KAKU, "subtitle": None, "publisher": "集英社",
              "label": None, "released": "2012-01", "isbn": "9784088700000", "url": None, "score": 1.0, "response_key": None}
    put_link(env, ws[KAKU], "ndl", {"title": KAKU, "authors": [], "kind": "manga", "publisher": "集英社", "label": None,
                                    "records": [record]}, confidence=0.95)
    put_link(env, ws["Kakukaku Shikajika"], "anilist",
             {"title": KAKU, "romaji": "Kakukaku Shikajika", "aliases": [], "authors": []}, confidence=0.8)
    env.client.post("/api/works/refresh")
    proposal = env.client.get("/api/works/proposals").json()[0]
    assert env.client.post(f"/api/works/proposals/{proposal['id']}/approve").status_code == 200
    survivor = env.client.get("/api/works").json()["works"]
    assert len(survivor) == 1
    vols = env.volumes(env.work(survivor[0]["id"]))
    first = next(v for v in vols if v["volume_number"] and v["volume_number"]["start"] == 1)
    assert first["metadata"]["publisher"]["value"] == "集英社"


@pytest.mark.parametrize("action", ["choose", "reset"])
def test_unknown_source_is_404(env_factory, action):
    env = env_factory("okiraku", OKIRAKU_FILES)
    wid = env.work_id()
    env.fetch(wid)
    r = env.client.post(f"/api/works/{wid}/links/nosuch/{action}", json={"source_id": None})
    assert r.status_code == 404


def test_background_fetch_runs_when_enabled_and_stops_with_the_app(env_factory):
    env = env_factory("kaku", KAKU_FILES, background=True, background_interval_seconds=1)
    wid = env.work_id()
    assert env.work(wid)["links"][0]["status"] is None
    with env.client:  # アプリの起動(lifespan)で裏の取得が動き出す
        import time
        deadline = time.monotonic() + 15
        while env.link(env.work(wid), "ndl")["status"] != "linked":
            assert time.monotonic() < deadline, "裏の取得で連携されなかった"
            time.sleep(0.2)
    assert env.work(wid)["fields"]["kind"]["value"] == "manga"


def test_background_fetch_is_off_by_default(env_factory):
    env = env_factory("kaku", KAKU_FILES)
    assert env.settings.metadata.background is False
    with env.client:
        import time
        time.sleep(1.5)
    assert env.work(env.work_id())["links"][0]["status"] is None


def test_resetting_a_field_that_unblocks_an_auto_merge_returns_the_surviving_work(env_factory):
    """手動の作品名を自動に戻すと、元の名前が別の作品と一致して自動統合が成立し、操作した作品が吸収されうる。
    その id で詳細を引いて 404 にしない。"""
    env = env_factory("kaku", ["Kakukaku Shikajika v01.cbz", f"{KAKU} 第03巻.cbz"])
    ws = {w["title"]: w["id"] for w in env.client.get("/api/works").json()["works"]}
    romaji, jp = ws["Kakukaku Shikajika"], ws[KAKU]
    assert romaji < jp                                                       # 吸収されるのは id の大きい日本語名の側
    assert env.client.patch(f"/api/works/{jp}", json={"title": "手で付けた名前"}).status_code == 200
    put_link(env, romaji, "anilist", {"title": KAKU, "romaji": "Kakukaku Shikajika", "aliases": [], "authors": []},
             confidence=0.95)
    env.client.post("/api/works/refresh")
    assert len(env.client.get("/api/works").json()["works"]) == 2           # 手動の作品名があるので提案止まり
    # 直す前の名前の別名を外すと、提案の根拠が無くなって提案も消える
    for a in env.work(jp)["aliases"]:
        if a["alias"] == KAKU:
            assert env.client.delete(f"/api/works/{jp}/aliases/{a['id']}").status_code == 200
    env.client.post("/api/works/refresh")
    assert env.client.get("/api/works/proposals").json() == []
    r = env.client.post(f"/api/works/{jp}/fields/title/reset")
    assert r.status_code == 200, r.text
    assert r.json()["id"] == romaji and len(env.client.get("/api/works").json()["works"]) == 1


def test_resetting_a_manual_link_keeps_the_hold_while_the_kind_is_unresolved(env_factory):
    env = env_factory("okiraku", OKIRAKU_FILES)
    wid = env.work_id()
    w = env.fetch(wid)
    anilist = env.link(w, "anilist")
    assert anilist["status"] == "pending" and anilist["hold"] == "kind_unresolved"
    manga = next(c for c in anilist["candidates"] if c["kind"] == "manga")
    r = env.client.post(f"/api/works/{wid}/links/anilist/choose", json={"source_id": manga["source_id"]})
    assert r.status_code == 200
    r = env.client.post(f"/api/works/{wid}/links/anilist/reset")
    assert r.status_code == 200, r.text
    after = env.link(r.json(), "anilist")
    assert after["status"] == "pending" and after["hold"] == "kind_unresolved" and after["manual"] is False
    assert r.json()["kind"] is None


def test_an_auto_link_whose_kind_contradicts_the_known_kind_is_dropped_on_refetch(env_factory):
    env = env_factory("kaku", KAKU_FILES)
    wid = env.work_id()
    w = env.fetch(wid)
    assert env.link(w, "anilist")["status"] == "linked"
    assert env.client.patch(f"/api/works/{wid}", json={"kind": "novel"}).status_code == 200
    w = env.fetch(wid)
    assert env.link(w, "anilist")["status"] != "linked"
    assert w["fields"]["kind"]["value"] == "novel" and w["fields"]["kind"]["manual"]
    assert "Comedy" not in (w["fields"].get("genres") or {}).get("value", [])      # 漫画側の候補の分類は残さない


# --- 日本語での表示(#74) ----------------------------------------------------------------------------


def _ascii_only(values) -> list[str]:
    return [v for v in values if v.isascii()]


def test_english_genres_and_tags_are_filled_in_japanese_and_the_raw_values_are_kept(env_factory):
    env = env_factory("kaku", KAKU_FILES)
    wid = env.work_id()
    f = env.fetch(wid)["fields"]
    assert f["genres"]["value"] and not _ascii_only(f["genres"]["value"])
    assert f["tags"]["value"] and not _ascii_only(f["tags"]["value"])
    # 訳して同じになる語は 1 つにまとまる(MangaUpdates の `Teacher/s` と AniList の `Teacher`)
    assert f["tags"]["value"].count("教師") == 1
    # 取得元の応答から作った連携の中身は英語のまま(訳は反映で作るだけ)
    (row,) = env.rows("SELECT item FROM work_links WHERE work_id = ? AND source = 'anilist'", wid)
    assert "Slice of Life" in json.loads(row["item"])["genres"]


@pytest.mark.parametrize("setting, expected", [
    (None, ["ドラマ", "教師"]),
    ("hide", ["ドラマ", "教師"]),
    ("keep", ["ドラマ", "Unheard-of Tag", "教師"]),
])
def test_untranslated_terms_are_hidden_unless_configured(env_factory, setting, expected):
    extra = {} if setting is None else {"untranslated_terms": setting}
    env = env_factory("kaku", ["作品名X 第01巻.cbz"], **extra)
    wid = env.work_id()
    put_link(env, wid, "anilist", {**ANILIST_ITEM, "genres": [], "tags": ["Drama", "Unheard-of Tag", "Teacher/s"]},
             confidence=0.8)
    assert env.work(wid)["fields"]["tags"]["value"] == expected


# --- あらすじの廃止(#82) ------------------------------------------------------------------------


@pytest.mark.parametrize("synopsis", ["Van, the fourth son of a noble, remembers his past life (前世).", "日本語のあらすじ。"])
def test_synopsis_from_public_db_is_not_put_into_the_work(env_factory, synopsis):
    env = env_factory("kaku", ["作品名X 第01巻.cbz"])
    wid = env.work_id()
    put_link(env, wid, "anilist", {**ANILIST_ITEM, "synopsis": synopsis}, confidence=0.95)
    put_link(env, wid, "mangaupdates", {**ANILIST_ITEM, "synopsis": synopsis}, confidence=0.95)
    assert "synopsis" not in env.work(wid)["fields"]
    assert env.work(wid)["fields"]["genres"]["value"] == ["ドラマ"]  # ほかの項目は入る
    conn = db.connect(env.settings.db_path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM work_fields WHERE field = 'synopsis'").fetchone()[0] == 0
    finally:
        conn.close()


def _put_synopsis(env, wid, value, *, manual):
    conn = db.connect(env.settings.db_path)
    try:
        with db.transaction(conn) as c:
            c.execute("""INSERT INTO work_fields (work_id, field, value, source, manual, confidence, updated_at)
                         VALUES (?, 'synopsis', ?, ?, ?, 1.0, ?)""",
                      (wid, value, "manual" if manual else "public_db", int(manual), db.now()))
    finally:
        conn.close()


def _synopsis_rows(env):
    conn = db.connect(env.settings.db_path)
    try:
        return [tuple(r) for r in conn.execute(
            "SELECT work_id, value, source, manual FROM work_fields WHERE field = 'synopsis' ORDER BY work_id")]
    finally:
        conn.close()


@pytest.mark.parametrize("manual", [True, False], ids=["manual", "public-db"])
def test_existing_synopsis_rows_are_kept_but_hidden(env_factory, manual):
    """廃止前に入ったあらすじの行(手で書いたもの・公開 DB から入ったもの)は消さない。API・検索・編集には出さない。"""
    env = env_factory("kaku", KAKU_FILES)
    wid = env.work_id()
    _put_synopsis(env, wid, "手で書いた宮崎のあらすじ", manual=manual)
    churn(env, wid)  # 再取得・再スキャン・再計算・全体の補完
    assert _synopsis_rows(env) == [(wid, "手で書いた宮崎のあらすじ", "manual" if manual else "public_db", int(manual))]
    assert "synopsis" not in env.work(wid)["fields"]
    assert env.client.get("/api/works", params={"q": "宮崎のあらすじ"}).json()["works"] == []
    # 編集・自動に戻す、の対象でもない
    assert env.client.patch(f"/api/works/{wid}", json={"synopsis": "x"}).status_code == 422
    assert env.client.post(f"/api/works/{wid}/fields/synopsis/reset").status_code in (404, 422)
    assert _synopsis_rows(env)[0][1] == "手で書いた宮崎のあらすじ"


def test_hidden_manual_synopsis_does_not_count_as_a_manual_decision(env_factory):
    """画面から見えず戻せない、廃止したあらすじの手動の行で、自動統合が止まり続けないようにする。"""
    env = env_factory("kaku", KAKU_FILES)
    wid = env.work_id()
    _put_synopsis(env, wid, "手で書いたあらすじ", manual=True)
    conn = db.connect(env.settings.db_path)
    try:
        assert works._manual_involved(conn, wid) is False
        conn.execute("UPDATE work_fields SET manual = 1 WHERE work_id = ? AND field = 'author'", (wid,))
        assert works._manual_involved(conn, wid) is True
    finally:
        conn.close()


def test_old_english_synopsis_setting_does_not_stop_the_app(tmp_path, caplog):
    """廃止した設定 `[metadata] english_synopsis` が古い設定ファイルに残っていても、無視してログで知らせ、起動する。"""
    lib = tmp_path / "lib"
    lib.mkdir()
    cbz(lib, KAKU_FILES[0])
    path = tmp_path / "config.local.toml"
    path.write_text('library_roots = ["lib"]\ndata_dir = "data"\nsevenzip_path = "spinebox-test-no-such-7z"\n\n'
                    '[metadata]\nsources = []\nenglish_synopsis = "hide"\n', encoding="utf-8")
    with caplog.at_level("WARNING", logger="spinebox.config"):
        settings = load_settings(path)
    assert [r.levelname for r in caplog.records if "english_synopsis" in r.getMessage()] == ["WARNING"]
    assert not hasattr(settings.metadata, "english_synopsis")
    with TestClient(create_app(settings, frontend_dist=None)) as client:
        assert client.post("/api/scan").status_code == 200
        assert [w["title"] for w in client.get("/api/works").json()["works"]] == [KAKU]


@pytest.mark.parametrize("publisher, expected", [("Overlap", "オーバーラップ"), ("Kadokawa Shoten", "角川書店"),
                                                 ("Unknown Shobo", "Unknown Shobo")])
def test_romanized_publisher_from_mangaupdates_is_shown_in_japanese(env_factory, publisher, expected):
    env = env_factory("kaku", ["作品名X 第01巻.cbz"])
    wid = env.work_id()
    put_link(env, wid, "mangaupdates", {**ANILIST_ITEM, "publisher": publisher}, confidence=0.8)
    assert env.work(wid)["fields"]["publisher"]["value"] == expected
    # 国立国会図書館サーチの出版者があれば、そちらが先
    put_link(env, wid, "ndl", {"title": "作品名X", "publisher": "集英社", "records": []}, confidence=0.8)
    assert env.work(wid)["fields"]["publisher"]["value"] == "集英社"


def test_candidates_are_shown_in_japanese_but_stored_as_fetched(env_factory):
    env = env_factory("okiraku", OKIRAKU_FILES)
    wid = env.work_id()
    w = env.fetch(wid)
    mu = env.link(w, "mangaupdates")
    assert mu["status"] == "pending"
    assert "オーバーラップ" in [c["publisher"] for c in mu["candidates"]]
    assert all(not _ascii_only(c["genres"]) for c in mu["candidates"] if c["genres"])
    (row,) = env.rows("SELECT candidates FROM work_links WHERE work_id = ? AND source = 'mangaupdates'", wid)
    assert "Overlap" in [c["summary"]["publisher"] for c in json.loads(row["candidates"])]


def test_english_values_already_in_the_db_turn_japanese_at_startup_but_manual_ones_do_not(env_factory):
    env = env_factory("kaku", KAKU_FILES)
    wid = env.work_id()
    env.fetch(wid)
    # 日本語化の前に入った値(英語)と、利用者が手で入れた英語の値
    conn = db.connect(env.settings.db_path)
    with db.transaction(conn) as c:
        c.execute("UPDATE work_fields SET value = ? WHERE work_id = ? AND field = 'genres'", ('["Drama"]', wid))
    conn.close()
    assert env.client.patch(f"/api/works/{wid}", json={"tags": ["Drama", "My Tag"]}).status_code == 200
    client = TestClient(create_app(env.settings, frontend_dist=None, metadata_fetcher=env.fetcher))
    f = client.get(f"/api/works/{wid}").json()["fields"]
    assert "ドラマ" in f["genres"]["value"] and "Drama" not in f["genres"]["value"]
    assert (f["tags"]["value"], f["tags"]["manual"]) == (["Drama", "My Tag"], True)


# --- CamelCase のローマ字作品名の分割 (#195) ----------------------------------------------


def test_query_splits_camel_case_romaji_title():
    from spinebox.linking import _Context, _query
    ctx = _Context("KakukakuShikajika", None, None, [])
    assert _query("anilist", ctx).title == "Kakukaku Shikajika"

    # 連続する大文字の後に小文字が続く場合（頭字語など）も正しく分割される
    ctx_seed = _Context("GundamSEEDDestiny", None, None, [])
    assert _query("anilist", ctx_seed).title == "Gundam SEED Destiny"

    # 日本語混じりや英字を含む日本語作品名は誤分割しない
    ctx_spy = _Context("SPYxFAMILY", None, None, [])
    assert _query("anilist", ctx_spy).title == "SPYxFAMILY"


def test_decide_threshold_is_not_loosened_for_ambiguous_matches():
    from spinebox.linking import decide
    # スコア 0.65 の曖昧一致候補は、単一候補であっても自動連携せず pending に留める
    cand = [{"summary": {"source_id": "1", "score": 0.65, "title": "日本語タイトル", "kind": "manga"}}]
    status, _, _, _ = decide(cand, "manga", hold=False)
    assert status == "pending"


def test_proposals_can_be_filtered_by_title_author_and_alias(env_factory):
    """統合の提案の絞り込み(#208): 作品名・別名の部分一致、空白区切りは AND、正規化は名寄せと同じ。"""
    env = env_factory("kaku", ["Kakukaku Shikajika v01.cbz", f"{KAKU} 第03巻.cbz"])
    ws = {w["title"]: w["id"] for w in env.client.get("/api/works").json()["works"]}
    put_link(env, ws["Kakukaku Shikajika"], "anilist",
             {"title": KAKU, "romaji": "Kakukaku Shikajika", "aliases": [], "authors": []}, confidence=0.95, manual=True)
    env.client.post("/api/works/refresh")

    def ids(**params):
        return [p["id"] for p in env.client.get("/api/works/proposals", params=params).json()]

    all_ids = ids()
    assert len(all_ids) == 1
    assert ids(q="kakukaku") == all_ids  # 作品名(大文字小文字を区別しない)
    assert ids(q="KAKUKAKU shikajika") == all_ids  # 空白区切りは AND
    assert ids(q=KAKU[:2]) == all_ids  # 反対側の作品名にだけ含まれる語でも当たる
    assert ids(q="kakukaku 存在しない語") == []  # AND: 1 語でも含まれなければ外れる
    assert ids(q="   ") == all_ids  # 空白だけは絞らない
    assert ids(q="zzz") == []
    assert ids(status="dismissed", q="kakukaku") == []  # status との併用
