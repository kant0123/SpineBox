"""HTTP 層: タイムアウト・再試行の上限・5xx / 4xx / 429・取得元ごとのレート制限(時計を差し替える)・秘匿情報。"""
import logging
import time

import httpx
import pytest

from metadata_fakes import FakeClock, FakeNetwork
from spinebox.config import MetadataSettings
from spinebox.db import open_db
from spinebox.metadata import http as mhttp
from spinebox.metadata.http import FetchError, HttpFetcher, SourceRequest, parse_retry_after, user_agent
from spinebox.metadata.model import (
    FAIL_CONNECT, FAIL_HTTP, FAIL_RATE_LIMITED, FAIL_TIMEOUT, FAIL_TOO_LARGE,
)
from spinebox.metadata.store import ResponseStore

URL = "https://example.test/api"


def req(source="ndl", **kw) -> SourceRequest:
    return SourceRequest(source, "GET", URL, kw.pop("params", (("q", "x"),)), **kw)


def make(net: FakeNetwork, clock: FakeClock | None = None, **kw):
    clock = clock or FakeClock()
    settings = MetadataSettings(**kw)
    return HttpFetcher(settings, transport=net.transport, clock=clock, sleep=clock.sleep), clock


def failure_of(fetcher, request):
    with pytest.raises(FetchError) as e:
        fetcher.fetch(request)
    return e.value.failure


def test_ok_response_and_user_agent():
    net = FakeNetwork()
    net.route("GET", URL, b"hello")
    f, _ = make(net, contact="me@example.test")
    assert f.fetch(req()).body == b"hello"
    ua = net.requests[0].headers["User-Agent"]
    assert ua == user_agent("me@example.test") and ua.startswith("SpineBox/") and "me@example.test" in ua
    assert "@" not in user_agent()  # 連絡先は設定したときだけ


def test_min_interval_between_requests_per_source():
    net = FakeNetwork()
    net.route("GET", URL, b"x")
    f, clock = make(net, min_interval_seconds={"ndl": 1.0, "anilist": 2.0})
    f.fetch(req("ndl"))
    assert clock.sleeps == []                  # 最初の要求は待たない
    f.fetch(req("ndl"))
    assert clock.sleeps == [1.0]               # 直前の要求の終わりから 1 秒空ける
    clock.advance(0.4)
    f.fetch(req("ndl"))
    assert clock.sleeps[-1] == pytest.approx(0.6)
    clock.advance(5)
    n = len(clock.sleeps)
    f.fetch(req("ndl"))
    assert len(clock.sleeps) == n              # 間隔が空いていれば待たない
    f.fetch(req("anilist"))                    # 取得元ごとに別の関門
    assert len(clock.sleeps) == n
    f.fetch(req("anilist"))
    assert clock.sleeps[-1] == 2.0


def test_5xx_is_retried_up_to_the_limit_then_fails():
    net = FakeNetwork()
    net.route("GET", URL, httpx.Response(503))
    f, clock = make(net, max_retries=2)
    failure = failure_of(f, req())
    assert (failure.kind, failure.status) == (FAIL_HTTP, 503)
    assert len(net.requests) == 3                                    # 1 回 + 再試行 2 回
    assert mhttp.BACKOFF_BASE in clock.sleeps and 2 * mhttp.BACKOFF_BASE in clock.sleeps  # 1 秒・2 秒のバックオフ


def test_retry_recovers_when_a_later_attempt_succeeds():
    net = FakeNetwork()
    answers = iter([httpx.Response(500), httpx.Response(502), httpx.Response(200, content=b"ok")])
    net.route("GET", URL, lambda r: next(answers))
    f, _ = make(net, max_retries=2)
    assert f.fetch(req()).body == b"ok" and len(net.requests) == 3


def test_max_retries_zero_means_single_attempt():
    net = FakeNetwork()
    net.route("GET", URL, httpx.Response(500))
    f, _ = make(net, max_retries=0)
    failure_of(f, req())
    assert len(net.requests) == 1


@pytest.mark.parametrize("status", [400, 403, 404])
def test_4xx_is_not_retried(status):
    net = FakeNetwork()
    net.route("GET", URL, httpx.Response(status))
    f, _ = make(net, max_retries=3)
    failure = failure_of(f, req())
    assert (failure.kind, failure.status) == (FAIL_HTTP, status) and len(net.requests) == 1


def test_redirect_is_a_failure_not_followed():
    net = FakeNetwork()
    net.route("GET", URL, httpx.Response(302, headers={"Location": "https://elsewhere.test/"}))
    f, _ = make(net, max_retries=0)
    assert failure_of(f, req()).kind == FAIL_HTTP and len(net.requests) == 1


def test_timeout_and_connect_error_become_failures_without_the_url():
    net = FakeNetwork()
    net.route("GET", URL, httpx.ReadTimeout("timed out reading https://example.test/api?q=SECRET"))
    f, _ = make(net, max_retries=1)
    failure = failure_of(f, req())
    assert failure.kind == FAIL_TIMEOUT and len(net.requests) == 2
    assert "example.test" not in failure.message and "SECRET" not in failure.message

    net = FakeNetwork()
    net.route("GET", URL, httpx.ConnectError("connection refused to https://example.test/api?key=SECRET"))
    f, _ = make(net, max_retries=0)
    failure = failure_of(f, req())
    assert failure.kind == FAIL_CONNECT
    assert "example.test" not in failure.message and "SECRET" not in failure.message


def test_unexpected_exception_is_contained(caplog):
    net = FakeNetwork()
    net.route("GET", URL, RuntimeError("boom https://example.test/?key=SECRET"))
    f, _ = make(net, max_retries=0)
    with caplog.at_level(logging.DEBUG):
        failure = failure_of(f, req())
    assert failure.kind == "internal"
    assert "SECRET" not in caplog.text and "SECRET" not in failure.message


def test_429_blocks_the_source_and_following_requests_are_not_sent():
    net = FakeNetwork()
    net.route("GET", URL, httpx.Response(429, headers={"Retry-After": "30"}))
    f, clock = make(net, max_retries=2)
    failure = failure_of(f, req("anilist"))
    assert failure.kind == FAIL_RATE_LIMITED and failure.status == 429 and failure.retry_after == 30
    assert len(net.requests) == 1                      # 429 は再試行しない

    clock.advance(1)
    failure = failure_of(f, req("anilist"))            # 止められている間は要求を出さない
    assert failure.kind == FAIL_RATE_LIMITED and 28 <= failure.retry_after <= 30
    assert len(net.requests) == 1

    net.route("GET", URL, b"fine")
    clock.advance(25)                                  # 残りが短ければ待って送る
    assert f.fetch(req("anilist")).body == b"fine"
    assert len(net.requests) == 2 and clock.sleeps[-1] == pytest.approx(4, abs=1.5)

    # 別の取得元は影響されない
    assert f.fetch(req("ndl")).body == b"fine"


def test_429_without_retry_after_uses_default_block():
    net = FakeNetwork()
    net.route("GET", URL, httpx.Response(429))
    f, clock = make(net, max_retries=0)
    assert failure_of(f, req()).retry_after == mhttp.DEFAULT_RATE_LIMIT_BLOCK
    clock.advance(mhttp.DEFAULT_RATE_LIMIT_BLOCK - 5)
    net.route("GET", URL, b"ok")
    assert f.fetch(req()).body == b"ok"                # 5 秒待てば通る


def test_parse_retry_after():
    assert parse_retry_after("12") == 12
    assert parse_retry_after("-5") == 0
    assert parse_retry_after("999999") == mhttp.MAX_RATE_LIMIT_BLOCK
    assert parse_retry_after("Wed, 21 Oct 2026 07:28:30 GMT", now_epoch=lambda: 1792567700.0) == pytest.approx(
        1792567710 - 1792567700, abs=40)
    assert parse_retry_after("soon") is None and parse_retry_after(None) is None and parse_retry_after("") is None


def test_oversized_response_is_rejected_without_retry(monkeypatch):
    monkeypatch.setattr(mhttp, "MAX_RESPONSE_BYTES", 10)
    net = FakeNetwork()
    net.route("GET", URL, b"x" * 100)
    f, _ = make(net, max_retries=2)
    assert failure_of(f, req()).kind == FAIL_TOO_LARGE and len(net.requests) == 1


def test_gate_is_released_after_a_failure():
    net = FakeNetwork()
    net.route("GET", URL, httpx.Response(429, headers={"Retry-After": "600"}))
    f, clock = make(net, max_retries=0)
    failure_of(f, req())
    failure_of(f, req())                                # 待たずに失敗 → ロックを握ったままにならない
    assert f.gate("ndl")._lock.acquire(blocking=False)


def test_long_min_interval_is_waited_not_failed_and_not_really_slept():
    # 時計を差し替えたテストは実時間では待たない(待つなら遅くなるので検出する)
    net = FakeNetwork()
    net.route("GET", URL, b"x")
    f, clock = make(net, min_interval_seconds={"ndl": 60.0})
    start = time.monotonic()
    f.fetch(req())
    f.fetch(req())
    assert clock.sleeps == [60.0]           # 429 の長い停止と違い、設定した最小間隔は(時計の上で)待つ
    assert time.monotonic() - start < 5


# --- 秘匿情報 ---

SECRET = "sk-test-SECRET-123"


def test_secret_params_and_headers_are_sent_but_never_keyed_stored_or_logged(tmp_path, caplog):
    net = FakeNetwork()
    net.route("GET", URL, b"payload")
    f, _ = make(net, max_retries=0)
    plain = req(params=(("q", "x"),))
    secret = req(params=(("q", "x"),), secret_params=(("applicationId", SECRET),),
                 secret_headers=(("X-Api-Key", SECRET),))
    assert secret.key() == plain.key()                 # 鍵に混ざらない(キーを変えても保存済みが使える)
    assert SECRET not in repr(secret) and SECRET not in str(secret.describe())

    with caplog.at_level(logging.DEBUG):
        f.fetch(secret)
    sent = net.requests[0]
    assert sent.url.params["applicationId"] == SECRET and sent.headers["X-Api-Key"] == SECRET  # 送信には使う

    conn = open_db(tmp_path / "s.db")
    ResponseStore(conn).put(secret, 200, b"payload")
    dump = " ".join(str(tuple(row)) for row in conn.execute("SELECT * FROM metadata_responses"))
    assert SECRET not in dump
    assert SECRET not in caplog.text


def test_secret_not_in_failure_or_logs_on_errors(caplog):
    net = FakeNetwork()
    net.route("GET", URL, httpx.ConnectError(f"cannot connect {URL}?applicationId={SECRET}"))
    f, _ = make(net, max_retries=1)
    secret = req(secret_params=(("applicationId", SECRET),))
    with caplog.at_level(logging.DEBUG):
        failure = failure_of(f, secret)
    assert SECRET not in failure.message and SECRET not in caplog.text


def test_settings_repr_hides_keys():
    s = MetadataSettings(keys={"rakuten_application_id": SECRET})
    assert SECRET not in repr(s)
