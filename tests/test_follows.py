"""フォロー(#87)。フォローした作品の続刊・欠けを公開 DB で調べて知らせる。

ネットワークには出ない(#44 の記録した応答 + 偽の取得元)。蔵書は一時ディレクトリに動的生成する。
"""
import dataclasses
import re
import time
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi.testclient import TestClient

from metadata_fakes import ANILIST, MU_SEARCH, NDL, FakeClock, fixture
from spinebox import db, follows
from spinebox.app import create_app
from spinebox.config import FollowSettings, MetadataSettings, parse_settings
from spinebox.metadata.http import HttpFetcher
from test_linking import KAKU, OKIRAKU, cbz, network


def without_volumes(xml: bytes, *volumes: int) -> bytes:
    """国立国会図書館サーチの記録から、指定した巻次の書誌(item)を除く(まだ出ていなかった頃の応答を作る)。"""
    text = xml.decode("utf-8")
    for v in volumes:
        text = re.sub(r"<item>(?:(?!</item>).)*?<dcndl:volume>%d</dcndl:volume>.*?</item>\s*" % v, "", text, flags=re.S)
    return text.encode("utf-8")


class Env:
    def __init__(self, make_settings, tmp_path, label, files, follow: FollowSettings | None = None, **metadata):
        self.lib = tmp_path / "lib"
        self.lib.mkdir(exist_ok=True)
        for f in files:
            cbz(self.lib, f)
        meta = MetadataSettings(max_retries=0, **metadata)
        self.settings = dataclasses.replace(make_settings(self.lib), metadata=meta,
                                            follow=follow or FollowSettings(response_ttl_days=0.0))
        self.net = network(label)
        self.clock = FakeClock()
        self.fetcher = HttpFetcher(meta, transport=self.net.transport, clock=self.clock, sleep=self.clock.sleep)
        self.client = TestClient(create_app(self.settings, frontend_dist=None, metadata_fetcher=self.fetcher))
        self.client.post("/api/scan")

    def work_id(self, title=None):
        ws = self.client.get("/api/works").json()["works"]
        if title is None:
            assert len(ws) == 1, [w["title"] for w in ws]
            return ws[0]["id"]
        return next(w["id"] for w in ws if w["title"] == title)

    def follow(self, wid):
        r = self.client.put(f"/api/works/{wid}/follow")
        assert r.status_code == 200, r.text
        return r.json()

    def check(self, *ids):
        r = self.client.post("/api/follows/check?wait=true", json={"work_ids": list(ids)} if ids else None)
        assert r.status_code == 200, r.text
        assert r.json()["error"] is None
        return r.json()

    def releases(self, seen=False):
        r = self.client.get(f"/api/follows/releases{'?seen=true' if seen else ''}")
        assert r.status_code == 200, r.text
        return r.json()["works"]

    def follows(self):
        r = self.client.get("/api/follows")
        assert r.status_code == 200, r.text
        return r.json()

    def entry(self, wid):
        return next(f for f in self.follows()["follows"] if f["work_id"] == wid)

    def ndl_only(self, xml: bytes):
        self.net.route("GET", NDL, lambda request: xml)


@pytest.fixture
def env_factory(make_settings, tmp_path):
    def make(label, files, **kw):
        return Env(make_settings, tmp_path, label, files, **kw)
    return make


def kaku_files(*numbers):
    return [f"[東村アキコ] {KAKU} 第0{n}巻.cbz" for n in numbers]


def volumes_of(entry):
    return [(r["volume"], r["kind"]) for r in entry["releases"]]


# --- 続刊・欠けが出る -------------------------------------------------------------------------


def test_volume_missing_from_the_library_is_reported_as_a_new_release(env_factory):
    env = env_factory("kaku", kaku_files(1, 2, 3, 4))
    wid = env.work_id()
    assert env.follow(wid) == {"followed": True, "followed_at": env.entry(wid)["followed_at"], "checked_at": None,
                               "status": None, "failure": None, "releases": []}
    state = env.check()
    assert state["summary"] == {"works": 1, "statuses": {"ok": 1}}
    [w] = env.releases()
    assert w["work_id"] == wid and w["title"] == KAKU and w["owned_max"] == 4
    [rel] = w["releases"]
    assert (rel["volume"], rel["kind"], rel["released"], rel["seen"]) == (5, "new", "2015-03", False)
    assert rel["url"] and rel["source"] == "ndl"
    entry = env.entry(wid)
    assert entry["checked_at"] and entry["status"] == "ok" and (entry["unseen"], entry["found"]) == (1, 1)
    # 調べるのは巻ごとの書誌を持つ国立国会図書館サーチだけ
    assert env.net.count(NDL) == 1 and env.net.count(ANILIST) == 0 and env.net.count(MU_SEARCH) == 0
    # 作品ページにもフォローの状態と見つけた巻が出る
    detail = env.client.get(f"/api/works/{wid}").json()["follow"]
    assert detail["followed"] and [r["volume"] for r in detail["releases"]] == [5]


def test_gaps_and_new_volumes_are_told_apart_and_sorted_by_release(env_factory):
    env = env_factory("kaku", kaku_files(1, 2, 4))
    wid = env.work_id()
    env.follow(wid)
    env.check()
    [w] = env.releases()
    assert volumes_of(w) == [(5, "new"), (3, "gap")]     # 新しい発売日が先
    assert w["latest_released"] == "2015-03"


def test_only_volumes_of_the_works_kind_are_counted(env_factory):
    """漫画と小説が同名で並ぶ作品は、連携した(作品の種別に合う)組の巻だけを数える。"""
    files = [f"{OKIRAKU} 第0{n}巻.cbz" for n in (1, 2, 3)]
    env = env_factory("okiraku", files)
    wid = env.work_id()
    env.follow(wid)
    env.check()
    # 漫画か小説かが未決(国立国会図書館サーチは確認待ち)のうちは数えない
    assert env.entry(wid)["status"] == "unlinked" and env.releases() == []
    link = next(x for x in env.client.get(f"/api/works/{wid}").json()["links"] if x["source"] == "ndl")
    manga = next(c for c in link["candidates"] if c["kind"] == "manga")
    assert env.client.post(f"/api/works/{wid}/links/ndl/choose", json={"source_id": manga["source_id"]}).status_code == 200
    env.check()
    [w] = env.releases()
    assert volumes_of(w) == [(4, "new")]                 # 小説の 4〜6 巻は数えない
    assert w["releases"][0]["released"] == "2023-08"


def test_a_link_whose_kind_contradicts_the_work_is_not_counted(env_factory):
    env = env_factory("kaku", kaku_files(1, 2, 3))
    wid = env.work_id()
    env.follow(wid)
    env.check()
    assert [r["volume"] for r in env.releases()[0]["releases"]] == [5, 4]
    link = next(x for x in env.client.get(f"/api/works/{wid}").json()["links"] if x["source"] == "ndl")
    env.client.post(f"/api/works/{wid}/links/ndl/choose", json={"source_id": link["source_id"]})  # 手動の連携(漫画)
    assert env.client.patch(f"/api/works/{wid}", json={"kind": "novel"}).status_code == 200
    env.check()
    assert env.entry(wid)["status"] == "kind_mismatch"


def test_work_without_readable_volume_numbers_is_not_counted(env_factory):
    env = env_factory("kaku", [f"[東村アキコ] {KAKU}.cbz"])
    wid = env.work_id()
    env.follow(wid)
    env.check()
    assert env.entry(wid)["status"] == "no_numbers" and env.releases() == []
    assert env.net.requests == []


def test_volume_that_joins_the_library_disappears(env_factory):
    env = env_factory("kaku", kaku_files(1, 2, 3, 4))
    wid = env.work_id()
    env.follow(wid)
    env.check()
    assert env.releases()
    cbz(env.lib, kaku_files(5)[0])
    env.client.post("/api/scan")
    assert env.releases() == [] and env.entry(wid)["found"] == 0   # 調べ直さなくても、蔵書に入った巻は出ない


# --- 確認済み ------------------------------------------------------------------------------------


def test_seen_volumes_do_not_come_back_and_only_new_ones_show(env_factory):
    env = env_factory("kaku", kaku_files(1, 2, 3))
    wid = env.work_id()
    env.follow(wid)
    full = fixture("ndl/kakuauthor-search.xml")
    env.ndl_only(without_volumes(full, 5))           # 5 巻がまだ出ていない頃
    env.check()
    assert volumes_of(env.releases()[0]) == [(4, "new")]
    r = env.client.post("/api/follows/releases/seen", json={"work_id": wid})
    assert r.status_code == 200 and r.json() == {"marked": 1}
    assert env.releases() == []

    env.check()                                      # 再確認しても確認済みの 4 巻は出ない
    assert env.releases() == []
    env.ndl_only(full)                               # 5 巻が出た
    env.check()
    [w] = env.releases()
    assert volumes_of(w) == [(5, "new")]
    assert [(r["volume"], r["seen"]) for r in env.releases(seen=True)[0]["releases"]] == [(5, False), (4, True)]
    # 巻を指定して確認済みにもできる
    assert env.client.post("/api/follows/releases/seen", json={"work_id": wid, "volumes": [5]}).json() == {"marked": 1}
    assert env.releases() == []


def test_unfollow_and_follow_again_keeps_the_seen_marks(env_factory):
    env = env_factory("kaku", kaku_files(1, 2, 3, 4))
    wid = env.work_id()
    env.follow(wid)
    env.check()
    env.client.post("/api/follows/releases/seen", json={"work_id": wid})
    r = env.client.delete(f"/api/works/{wid}/follow")
    assert r.status_code == 200 and r.json() == {"followed": False}
    assert env.follows()["follows"] == [] and env.releases(seen=True) == []
    env.follow(wid)
    env.check()
    assert env.releases() == []


# --- 取得元が落ちていても止まらない -------------------------------------------------------------


def test_follow_lists_and_browsing_keep_working_when_the_source_is_down(env_factory):
    env = env_factory("kaku", kaku_files(1, 2, 3, 4))
    wid = env.work_id()
    env.follow(wid)
    env.net.route("GET", NDL, httpx.Response(503, content=b"unavailable"))
    state = env.check()
    assert state["summary"]["statuses"] == {"failed": 1}
    entry = env.entry(wid)
    assert entry["status"] == "failed" and entry["failure"]["kind"] == "http_status" and entry["checked_at"]
    assert env.releases() == []
    # 一覧・閲覧は止まらない
    vid = env.client.get("/api/volumes").json()[0]["id"]
    assert env.client.get(f"/api/volumes/{vid}/pages/0").status_code == 200
    assert env.client.get(f"/api/works/{wid}").status_code == 200
    assert env.client.get("/api/works").status_code == 200


def test_previous_result_stays_when_the_source_goes_down_later(env_factory):
    env = env_factory("kaku", kaku_files(1, 2, 3, 4))
    wid = env.work_id()
    env.follow(wid)
    env.check()
    conn = db.connect(env.settings.db_path)
    conn.execute("DELETE FROM metadata_responses")   # 保存済みの応答も無い状態で落ちる
    conn.close()
    env.net.route("GET", NDL, httpx.ConnectError("down"))
    env.check()
    entry = env.entry(wid)
    assert entry["status"] == "ok" and entry["failure"]["kind"] == "connect"   # 前回までの連携で数え、失敗を残す
    assert volumes_of(env.releases()[0]) == [(5, "new")]


def test_stored_response_is_reused_within_the_follow_ttl(env_factory):
    env = env_factory("kaku", kaku_files(1, 2, 3, 4), follow=FollowSettings(response_ttl_days=1.0))
    wid = env.work_id()
    env.follow(wid)
    env.check()
    env.check()
    assert env.net.count(NDL) == 1                   # 有効期限内は保存済みの応答を使う(取得元に出ない)


def test_listing_never_requests_the_sources(env_factory):
    env = env_factory("kaku", kaku_files(1, 2, 3, 4))
    wid = env.work_id()
    env.follow(wid)
    env.follows()
    env.releases()
    env.client.get(f"/api/works/{wid}")
    assert env.net.requests == []


# --- 操作の検証・統合・設定 ------------------------------------------------------------------------


def test_follow_validation(env_factory):
    env = env_factory("kaku", kaku_files(1))
    wid = env.work_id()
    assert env.client.put("/api/works/9999/follow").status_code == 404
    assert env.client.post("/api/follows/releases/seen", json={"work_id": 9999}).status_code == 404
    assert env.client.post("/api/follows/releases/seen", json={"work_id": "x"}).status_code == 422
    env.follow(wid)
    env.follow(wid)                                  # 2 回フォローしても 1 件
    assert len(env.follows()["follows"]) == 1
    # フォローしていない作品を指定しても調べない
    assert env.check(9999)["total"] == 0
    assert env.check(wid)["total"] == 1


def test_check_is_refused_when_ndl_is_disabled(env_factory):
    env = env_factory("kaku", kaku_files(1), sources=("anilist",))
    env.follow(env.work_id())
    assert env.follows()["available"] is False
    assert env.client.post("/api/follows/check?wait=true").status_code == 409


def test_follow_moves_to_the_surviving_work_on_merge(env_factory):
    env = env_factory("kaku", [*kaku_files(1, 2, 3, 4), "別の作品 第01巻.cbz"])
    kaku, other = env.work_id(KAKU), env.work_id("別の作品")
    env.follow(kaku)
    env.check()
    env.client.post(f"/api/follows/releases/seen", json={"work_id": kaku})
    env.follow(other)
    r = env.client.post(f"/api/works/{other}/merge", json={"other_id": kaku})
    assert r.status_code == 200, r.text
    root = r.json()["id"]
    assert [f["work_id"] for f in env.follows()["follows"]] == [root]
    assert [(x["volume"], x["seen"]) for x in env.releases(seen=True)[0]["releases"]] == [(5, True)]


def test_background_check_runs_when_enabled(env_factory):
    env = env_factory("kaku", kaku_files(1, 2, 3, 4),
                      follow=FollowSettings(background=True, background_interval_seconds=1, response_ttl_days=0.0))
    wid = env.work_id()
    env.follow(wid)
    with env.client:
        deadline = time.monotonic() + 15
        while env.entry(wid)["checked_at"] is None:
            assert time.monotonic() < deadline, "裏の確認で調べられなかった"
            time.sleep(0.2)
    assert env.entry(wid)["status"] == "ok"


def test_background_check_is_off_by_default(env_factory):
    env = env_factory("kaku", kaku_files(1, 2, 3, 4))
    assert env.settings.follow.background is False
    wid = env.work_id()
    env.follow(wid)
    with env.client:
        time.sleep(1.5)
    assert env.entry(wid)["checked_at"] is None and env.net.requests == []


def test_works_due_orders_unchecked_first_and_honours_the_interval(env_factory):
    env = env_factory("kaku", [*kaku_files(1), "別の作品 第01巻.cbz"])
    a, b = env.work_id(KAKU), env.work_id("別の作品")
    env.follow(a)
    env.follow(b)
    conn = db.connect(env.settings.db_path)
    try:
        conn.execute("UPDATE follows SET checked_at = ? WHERE work_id = ?", ("2026-11-01T00:00:00+00:00", a))
        from datetime import datetime, timezone
        now = datetime(2026, 11, 1, 12, tzinfo=timezone.utc)
        assert follows.works_due(conn, 24, now) == [b]
        assert follows.works_due(conn, 6, now) == [b, a]
    finally:
        conn.close()


def test_follow_settings_are_parsed(tmp_path):
    s = parse_settings({"follow": {"background": True, "interval_hours": 12, "response_ttl_days": 0.5}}, tmp_path)
    assert (s.follow.background, s.follow.interval_hours, s.follow.response_ttl_days) == (True, 12.0, 0.5)
    assert parse_settings({}, tmp_path).follow == FollowSettings()
    from spinebox.config import ConfigError
    with pytest.raises(ConfigError):
        parse_settings({"follow": "yes"}, tmp_path)


def test_ndl_query_is_the_normal_linking_query(env_factory):
    env = env_factory("kaku", kaku_files(1, 2, 3, 4))
    env.follow(env.work_id())
    env.check()
    q = parse_qs(urlparse(str(env.net.requests[0].url)).query)
    assert q["title"] == [KAKU] and q["cnt"] == ["100"]
