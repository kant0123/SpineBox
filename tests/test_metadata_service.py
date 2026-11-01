"""3 つの取得元を通した検索: 候補・確からしさ、失敗の表現、保存済み応答の再利用、レート制限。"""
import sqlite3
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from metadata_fakes import ANILIST, MU_DETAIL, MU_SEARCH, NDL, FakeClock, FakeNetwork, fixture, json_body, \
    recorded_network
from spinebox.config import MetadataSettings
from spinebox.db import open_db
from spinebox.metadata import MetadataService, SearchQuery
from spinebox.metadata.http import HttpFetcher
from spinebox.metadata.model import (
    FAIL_CONNECT, FAIL_DISABLED, FAIL_HTTP, FAIL_INVALID, FAIL_RATE_LIMITED, FAIL_TIMEOUT, KIND_MANGA,
)
from spinebox.metadata.store import ResponseStore

OKIRAKU = "お気楽領主の楽しい領地防衛"


class WallClock:
    """保存済み応答の取得日時(壁時計)の差し替え。"""

    def __init__(self):
        self.now = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.now


class Env:
    def __init__(self, tmp_path, net: FakeNetwork, **settings):
        self.net = net
        self.clock = FakeClock()
        self.wall = WallClock()
        self.settings = MetadataSettings(**settings)
        self.conn = open_db(tmp_path / "metadata.db")
        self.store = ResponseStore(self.conn, clock=self.wall)
        fetcher = HttpFetcher(self.settings, transport=net.transport, clock=self.clock, sleep=self.clock.sleep)
        self.service = MetadataService(self.settings, self.conn, fetcher=fetcher, store=self.store)

    def search(self, title=OKIRAKU, sources=None, **query):
        return {o.source: o for o in self.service.search(SearchQuery(title, **query), sources)}

    def stored_rows(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM metadata_responses").fetchone()[0]


@pytest.fixture
def env(tmp_path):
    return Env(tmp_path, recorded_network("okiraku"), max_retries=1)


def test_search_returns_normalized_candidates_from_every_source(env):
    out = env.search(volume=1, kind=KIND_MANGA)
    assert set(out) == {"ndl", "anilist", "mangaupdates"}
    assert all(o.ok and o.candidates for o in out.values())

    best = out["ndl"].candidates[0]
    assert best.item.title == OKIRAKU and best.item.volume == 1 and best.item.kind == KIND_MANGA
    assert best.match.score == 1.0 and best.match.reasons == ("title:exact", "volume:match", "kind:match")
    scores = [c.match.score for c in out["ndl"].candidates]
    assert scores == sorted(scores, reverse=True)       # 確からしさの高い順
    # 巻違いは順位が下がる(同じ作品名でも巻の根拠が違う)
    assert out["ndl"].candidates[-1].match.score < best.match.score

    anilist = out["anilist"].candidates[0].item
    assert anilist.romaji.startswith("Okiraku Ryoushu") and anilist.scope == "work"

    mu = out["mangaupdates"].candidates[0]
    assert mu.item.title == OKIRAKU and mu.match.score == 1.0 and mu.item.total_volumes == 8
    assert "volume:not_applicable" in mu.match.reasons


def test_request_shapes(env):
    env.search(volume=1)
    ndl, anilist, mu_search = env.net.requests[0], None, None
    assert str(ndl.url).startswith(NDL) and ndl.url.params["title"] == OKIRAKU
    assert ndl.url.params["mediatype"] == "books" and ndl.url.params["cnt"] == "20"
    assert "creator" not in ndl.url.params
    anilist = next(r for r in env.net.requests if str(r.url).startswith(ANILIST))
    body = json_body(anilist)
    assert body["variables"] == {"search": OKIRAKU, "perPage": 20} and "MANGA" in body["query"]
    mu_search = next(r for r in env.net.requests if str(r.url) == MU_SEARCH)
    assert json_body(mu_search) == {"search": OKIRAKU, "perpage": 20}


def test_mangaupdates_detail_requests_are_limited(env):
    env.search(sources=["mangaupdates"])
    details = [r for r in env.net.requests if str(r.url).startswith(MU_DETAIL) and r.method == "GET"]
    assert len(details) == env.settings.detail_limit == 3
    # 詳細を引いた候補は別名・原版元を持ち、引かなかった候補は検索結果の項目だけ
    out = env.search(sources=["mangaupdates"])["mangaupdates"]
    assert out.candidates[0].item.publisher == "Overlap"
    assert any(c.item.publisher is None and c.item.authors == () for c in out.candidates)


def test_candidate_response_key_leads_back_to_the_raw_response(env):
    out = env.search(sources=["ndl", "mangaupdates"])
    c = out["ndl"].candidates[0]
    stored = env.store.get_by_key("ndl", c.response_key)
    assert stored is not None and stored.body == fixture("ndl/okiraku-search.xml")
    mu = out["mangaupdates"].candidates[0]
    detail = env.store.get_by_key("mangaupdates", mu.response_key)
    assert detail.body == fixture("mangaupdates/okiraku-detail-41097037681.json")
    assert {r.label for r in out["mangaupdates"].responses} == {"search", "detail"}


def test_author_filter_uses_creator_and_falls_back_when_empty(tmp_path):
    net = recorded_network("kaku")
    e = Env(tmp_path, net)
    out = e.search("かくかくしかじか", sources=["ndl"], author="東村アキコ", volume=3)["ndl"]
    assert len(net.requests) == 1 and net.requests[0].url.params["creator"] == "東村アキコ"
    best = out.candidates[0]
    assert (best.item.volume, best.item.authors, best.match.score) == (3, ("東村アキコ",), 1.0)
    novelization = next(c for c in out.candidates if c.item.volume is None)   # 巻が分からない項目は巻一致より下
    assert novelization.match.score < best.match.score

    # 著者で絞って 0 件なら、著者なしで取り直す
    empty = b'<rss version="2.0"><channel><title>x</title></channel></rss>'
    net2 = FakeNetwork()
    net2.route("GET", NDL, lambda r: empty if "creator" in r.url.params else fixture("ndl/kaku-search.xml"))
    (tmp_path / "second").mkdir()
    e2 = Env(tmp_path / "second", net2)
    out2 = e2.search("かくかくしかじか", sources=["ndl"], author="間違った著者")["ndl"]
    assert len(net2.requests) == 2 and "creator" not in net2.requests[1].url.params
    assert out2.ok and out2.candidates and any("著者なし" in n for n in out2.notes)


def test_isbn_search_uses_isbn_only(tmp_path):
    net = FakeNetwork()
    net.route("GET", NDL, fixture("ndl/kakuauthor-search.xml"))
    e = Env(tmp_path, net)
    out = e.search("かくかくしかじか", sources=["ndl"], isbn="978-4-08-782457-5")["ndl"]
    params = net.requests[0].url.params
    assert params["isbn"] == "9784087824575" and "title" not in params
    assert out.candidates[0].item.isbn == "9784087824575" or any(
        c.item.isbn == "9784087824575" and "isbn:match" in c.match.reasons for c in out.candidates)


def test_romaji_query_finds_ndl_and_mangaupdates_but_anilist_has_no_hit(tmp_path):
    e = Env(tmp_path, recorded_network("romaji"))
    out = e.search("Okiraku ryoshu no tanoshi ryochi")
    assert out["anilist"].ok and out["anilist"].candidates == ()    # 見つからない = 成功した 0 件
    assert out["ndl"].candidates[0].match.score > 0.8               # 読み(カタカナ)から比べる
    assert out["mangaupdates"].candidates[0].item.title == OKIRAKU


# --- 保存済みの応答 ---

def test_same_request_uses_saved_response_without_network(env):
    first = env.search()
    n = len(env.net.requests)
    assert n == 1 + 1 + 4 and env.stored_rows() == 6 and not any(o.from_cache for o in first.values())

    second = env.search()
    assert len(env.net.requests) == n                                # ネットワークに出ない
    assert all(o.from_cache for o in second.values())
    assert all(r.from_cache and not r.stale for o in second.values() for r in o.responses)
    for name in first:
        assert [c.item for c in second[name].candidates] == [c.item for c in first[name].candidates]
    assert env.settings.sources == ("ndl", "anilist", "mangaupdates")


def test_different_query_is_a_different_request(env):
    env.search()
    n = len(env.net.requests)
    env.search("別の作品", sources=["ndl"])
    assert len(env.net.requests) == n + 1


def test_expired_response_is_fetched_again_and_replaced(env):
    env.search(sources=["ndl"])
    env.wall.now += timedelta(days=env.settings.cache_ttl_days, seconds=1)
    out = env.search(sources=["ndl"])["ndl"]
    assert len(env.net.requests) == 2 and not out.from_cache
    assert env.stored_rows() == 1                                   # 同じ鍵は上書きで、行は増えない
    row = env.conn.execute("SELECT fetched_at FROM metadata_responses").fetchone()
    assert row["fetched_at"].startswith("2026-12-01")


def test_response_just_inside_ttl_is_reused(env):
    env.search(sources=["ndl"])
    env.wall.now += timedelta(days=env.settings.cache_ttl_days) - timedelta(seconds=1)
    assert env.search(sources=["ndl"])["ndl"].from_cache and len(env.net.requests) == 1


def test_expired_response_is_used_when_the_source_is_down(env):
    env.search(sources=["ndl"])
    env.wall.now += timedelta(days=100)
    env.net.route("GET", NDL, httpx.Response(503))
    out = env.search(sources=["ndl"])["ndl"]
    assert out.ok and out.candidates                                # 失敗にせず、古い応答で返す
    assert out.responses[0].stale and out.responses[0].from_cache
    assert any("有効期限切れ" in n for n in out.notes)


def test_expired_and_unparsable_saved_response_does_not_hide_the_failure(env):
    env.search(sources=["ndl"])
    env.conn.execute("UPDATE metadata_responses SET body = ?", (b"garbage",))
    env.wall.now += timedelta(days=100)
    env.net.route("GET", NDL, httpx.Response(503))
    out = env.search(sources=["ndl"])["ndl"]
    assert not out.ok and out.failure.kind == FAIL_HTTP and out.candidates == ()


def test_unparsable_saved_response_is_dropped_and_refetched(env):
    env.search(sources=["ndl"])
    env.conn.execute("UPDATE metadata_responses SET body = ?", (b"<html>broken</html>",))
    out = env.search(sources=["ndl"])["ndl"]
    assert out.ok and out.candidates and len(env.net.requests) == 2
    assert env.conn.execute("SELECT body FROM metadata_responses").fetchone()["body"] == fixture("ndl/okiraku-search.xml")


def test_empty_result_is_saved_too(tmp_path):
    net = FakeNetwork()
    net.route("POST", ANILIST, fixture("anilist/romaji-search.json"))
    e = Env(tmp_path, net)
    e.search(sources=["anilist"])
    out = e.search(sources=["anilist"])["anilist"]
    assert out.ok and out.from_cache and len(net.requests) == 1


# --- 失敗は例外にならず「取得失敗」の結果になる ---

@pytest.mark.parametrize("response, kind", [
    (httpx.ReadTimeout("t"), FAIL_TIMEOUT),
    (httpx.ConnectError("c"), FAIL_CONNECT),
    (httpx.Response(500), FAIL_HTTP),
    (httpx.Response(503), FAIL_HTTP),
    (httpx.Response(404), FAIL_HTTP),
    (httpx.Response(429, headers={"Retry-After": "60"}), FAIL_RATE_LIMITED),
])
def test_transport_failures_become_failure_outcomes(tmp_path, response, kind):
    net = FakeNetwork()
    for method, url in (("GET", NDL), ("POST", ANILIST), ("POST", MU_SEARCH)):
        net.route(method, url, response)
    e = Env(tmp_path, net, max_retries=1)
    out = e.search()
    assert set(out) == {"ndl", "anilist", "mangaupdates"}
    for o in out.values():
        assert not o.ok and o.candidates == () and o.failure.kind == kind
    assert e.stored_rows() == 0                                     # 失敗は保存しない
    if kind == FAIL_RATE_LIMITED:
        assert out["ndl"].failure.retry_after == 60 and out["ndl"].failure.status == 429


@pytest.mark.parametrize("source, method, url, body", [
    ("ndl", "GET", NDL, b"<html>maintenance</html>"),
    ("ndl", "GET", NDL, b"\xff\xfe broken"),
    ("anilist", "POST", ANILIST, b"<html>bad gateway</html>"),
    ("anilist", "POST", ANILIST, b'{"errors":[{"message":"boom"}],"data":null}'),
    ("mangaupdates", "POST", MU_SEARCH, b"not json"),
    ("mangaupdates", "POST", MU_SEARCH, b'{"status":"exception","reason":"x"}'),
])
def test_invalid_response_becomes_failure_and_is_not_saved(tmp_path, source, method, url, body):
    net = FakeNetwork()
    net.route(method, url, body)
    e = Env(tmp_path, net)
    out = e.search(sources=[source])[source]
    assert not out.ok and out.failure.kind == FAIL_INVALID and out.candidates == ()
    assert e.stored_rows() == 0


def test_everything_down_does_not_raise_and_does_not_poison_later_searches(tmp_path):
    net = FakeNetwork()  # 全部 404(経路なし)
    e = Env(tmp_path, net, max_retries=0)
    assert all(not o.ok for o in e.service.search(SearchQuery(OKIRAKU)))
    # 取得元が復旧したら次の検索で取れる
    healthy = recorded_network("okiraku")
    net.routes = healthy.routes
    assert all(o.ok and o.candidates for o in e.service.search(SearchQuery(OKIRAKU)))


def test_mangaupdates_detail_failure_keeps_search_results_with_a_note(tmp_path):
    net = recorded_network("okiraku")
    net.route("GET", MU_DETAIL + "41097037681", httpx.Response(500))
    e = Env(tmp_path, net, max_retries=0)
    out = e.search(sources=["mangaupdates"])["mangaupdates"]
    assert out.ok and len(out.notes) == 1 and "41097037681" in out.notes[0]
    top = out.candidates[0]
    assert top.item.title == OKIRAKU                                # 検索が当たった別名(日本語名)で返る
    assert top.item.publisher is None and top.item.authors == ()    # 詳細にしか無い項目は空
    assert top.item.aliases == () or top.item.romaji is not None


def test_storage_failure_does_not_fail_the_search(env, monkeypatch):
    def broken(*a, **k):
        raise sqlite3.OperationalError("database is locked")
    monkeypatch.setattr(env.store, "put", broken)
    out = env.search(sources=["ndl"])["ndl"]
    assert out.ok and out.candidates and not out.responses[0].from_cache


def test_internal_error_is_contained(env, monkeypatch):
    source = env.service.sources["ndl"]
    monkeypatch.setattr(source, "_search", lambda q, run: (_ for _ in ()).throw(KeyError("x")))
    out = env.search(sources=["ndl"])["ndl"]
    assert not out.ok and out.failure.kind == "internal"


# --- 取得元の有効 / 無効 ---

def test_sources_can_be_enabled_and_disabled_by_settings(tmp_path):
    e = Env(tmp_path, recorded_network("okiraku"), sources=("ndl", "futuristic"))
    assert list(e.service.sources) == ["ndl"] and e.service.unknown == ["futuristic"]
    out = e.service.search(SearchQuery(OKIRAKU))
    assert [o.source for o in out] == ["ndl"]                       # 有効なものだけ検索する
    disabled = e.service.search(SearchQuery(OKIRAKU), ["anilist"])[0]
    assert not disabled.ok and disabled.failure.kind == FAIL_DISABLED
    assert len(e.net.requests) == 1


def test_no_sources_means_no_requests(tmp_path):
    e = Env(tmp_path, recorded_network("okiraku"), sources=())
    assert e.service.search(SearchQuery(OKIRAKU)) == [] and e.net.requests == []


def test_empty_title_is_rejected_by_the_query_type():
    with pytest.raises(ValueError):
        SearchQuery("  ")


# --- レート制限(時計を差し替える) ---

def test_requests_to_the_same_source_are_spaced_by_the_configured_interval(tmp_path):
    e = Env(tmp_path, recorded_network("okiraku"),
            min_interval_seconds={"ndl": 1.0, "anilist": 2.0, "mangaupdates": 1.0})
    e.search(sources=["mangaupdates"])                              # 検索 1 + 詳細 3 = 4 要求
    assert e.clock.sleeps == [1.0, 1.0, 1.0]                        # 3 回の間隔だけ待つ
    e.clock.sleeps.clear()
    e.search("別の作品", sources=["ndl"])
    e.search("さらに別の作品", sources=["ndl"])
    assert e.clock.sleeps == [1.0]
    e.search("A", sources=["anilist"])
    e.search("B", sources=["anilist"])
    assert e.clock.sleeps[-1] == 2.0                                # AniList の既定は 30 回/分


def test_defaults_respect_documented_limits():
    s = MetadataSettings()
    assert s.interval("anilist") >= 2.0       # 障害時の上限 30 回/分(通常 90 回/分)
    assert s.interval("ndl") >= 1.0 and s.interval("mangaupdates") >= 1.0
    assert s.interval("unknown") == 1.0


def test_anilist_429_stops_further_anilist_requests_until_retry_after(tmp_path):
    net = recorded_network("okiraku")
    net.route("POST", ANILIST, httpx.Response(429, headers={"Retry-After": "60"}))
    e = Env(tmp_path, net, max_retries=2)
    first = e.search(sources=["anilist"])["anilist"]
    assert first.failure.kind == FAIL_RATE_LIMITED and net.count(ANILIST) == 1
    e.clock.advance(5)
    again = e.search("別の作品", sources=["anilist"])["anilist"]
    assert again.failure.kind == FAIL_RATE_LIMITED and net.count(ANILIST) == 1   # 要求を出さない
    # ほかの取得元は使える
    assert e.search(sources=["ndl"])["ndl"].ok
    # 待てば再開する
    net.route("POST", ANILIST, fixture("anilist/okiraku-search.json"))
    e.clock.advance(60)
    assert e.search("別の作品", sources=["anilist"])["anilist"].ok
