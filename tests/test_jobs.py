"""データ管理のジョブ(#83)。1 本ずつ・待ち行列・中止・履歴と、既存のボタンからの起動が同じ履歴に残ること。"""
from __future__ import annotations

import dataclasses
import threading
import time

import pytest
from fastapi.testclient import TestClient

from conftest import page_bytes, snapshot, write_zip, zip_bytes
from metadata_fakes import FakeClock
from spinebox import db
from spinebox.app import create_app
from spinebox.config import InboxSetting, MetadataSettings
from spinebox.datajobs import KINDS
from spinebox.jobs import Busy, JobCancelled, JobRunner, NotCancellable
from spinebox.metadata.http import HttpFetcher
from spinebox.scanner import Scanner


def wait_until(pred, timeout=10.0, what="条件"):
    deadline = time.monotonic() + timeout
    while not pred():
        assert time.monotonic() < deadline, f"{what}が成り立たなかった"
        time.sleep(0.02)


@pytest.fixture
def runner(tmp_path):
    path = tmp_path / "jobs.db"
    db.open_db(path).close()
    r = JobRunner(path, threading.Lock())
    yield r
    r.stop()


class Gate:
    """ジョブの中で止めておき、テストから先へ進める。同時に動いた数も数える。"""

    def __init__(self):
        self.entered = threading.Event()
        self.go = threading.Event()
        self.running = 0
        self.max_running = 0
        self.order: list[str] = []
        self._lock = threading.Lock()

    def job(self, name, *, steps=1):
        def run(ctx):
            with self._lock:
                self.running += 1
                self.max_running = max(self.max_running, self.running)
                self.order.append(name)
            try:
                self.entered.set()
                for i in range(steps):
                    assert self.go.wait(5)
                    ctx.progress(i + 1, steps, f"{name}-{i}")
                return {"name": name}
            finally:
                with self._lock:
                    self.running -= 1
        return run


# --- 実行の仕組み(JobRunner) ------------------------------------------------------------


def test_jobs_run_one_at_a_time_in_order_and_are_recorded(runner):
    gate = Gate()
    a = runner.submit("a", "A", gate.job("a"))
    b = runner.submit("b", "B", gate.job("b"))
    assert gate.entered.wait(5)
    snap = runner.snapshot()
    assert snap["current"]["id"] == a.id and snap["current"]["status"] == "running"
    assert [j["id"] for j in snap["queue"]] == [b.id] and snap["queue"][0]["status"] == "queued"
    gate.go.set()
    assert b.finished.wait(5)
    assert gate.order == ["a", "b"] and gate.max_running == 1
    history = runner.snapshot()["history"]
    assert [(j["kind"], j["status"], j["summary"]) for j in history] == [
        ("b", "succeeded", {"name": "b"}), ("a", "succeeded", {"name": "a"})]
    assert all(j["started_at"] and j["finished_at"] for j in history)


def test_same_job_is_not_queued_twice(runner):
    gate = Gate()
    a = runner.submit("a", "A", gate.job("a"))
    assert runner.submit("a", "A", gate.job("a")).id == a.id
    b = runner.submit("a", "A", gate.job("a"), params={"all": True})  # 指定が違えば別のジョブ
    assert b.id != a.id
    gate.go.set()
    assert b.finished.wait(5)


def test_run_now_is_refused_while_another_job_runs_or_waits(runner):
    gate = Gate()
    runner.submit("a", "A", gate.job("a"))
    assert gate.entered.wait(5)
    with pytest.raises(Busy):
        runner.run_now("b", "B", lambda ctx: {})
    gate.go.set()
    wait_until(lambda: not runner.busy(), what="ジョブの終わり")
    job = runner.run_now("b", "B", lambda ctx: {"ok": 1})
    assert job.status == "succeeded" and job.summary == {"ok": 1} and job.origin == "button"


def test_queued_job_can_be_cancelled_and_never_runs(runner):
    gate = Gate()
    a = runner.submit("a", "A", gate.job("a"))
    b = runner.submit("b", "B", gate.job("b"))
    assert gate.entered.wait(5)
    assert runner.cancel(b.id).status == "cancelled"
    gate.go.set()
    assert a.finished.wait(5)
    time.sleep(0.1)
    assert gate.order == ["a"]
    assert runner.get(b.id).status == "cancelled" and runner.get(b.id).error == "中止しました"


def test_running_job_stops_at_the_next_checkpoint(runner):
    gate = Gate()
    a = runner.submit("a", "A", gate.job("a", steps=3))
    assert gate.entered.wait(5)
    assert runner.cancel(a.id).cancel_requested is True
    gate.go.set()
    assert a.finished.wait(5)
    saved = runner.get(a.id)
    assert saved.status == "cancelled" and saved.summary is None
    with pytest.raises(NotCancellable):
        runner.cancel(a.id)  # 終わったジョブ


def test_failure_is_recorded_and_the_queue_goes_on(runner):
    def boom(ctx):
        raise RuntimeError("壊れた")
    a = runner.submit("a", "A", boom)
    b = runner.submit("b", "B", lambda ctx: {"ok": 1})
    assert b.finished.wait(5)
    assert runner.get(a.id).status == "failed" and runner.get(a.id).error == "RuntimeError: 壊れた"
    assert runner.get(b.id).status == "succeeded"


def test_jobs_wait_for_the_shared_lock(runner):
    """整理処理(同じロックを持つ)の間はジョブを始めない。"""
    gate = Gate()
    assert runner.lock.acquire(blocking=False)
    try:
        a = runner.submit("a", "A", gate.job("a"))
        time.sleep(0.2)
        assert not gate.entered.is_set() and runner.get(a.id).status == "queued"
    finally:
        runner.lock.release()
    gate.go.set()
    assert a.finished.wait(5) and runner.get(a.id).status == "succeeded"


def test_background_runs_only_when_idle_and_consecutive_runs_share_one_row(runner):
    assert runner.run_background("bg", "裏", lambda ctx: None) is not None  # することが無ければ残さない
    assert runner.snapshot()["history"] == []
    for _ in range(3):
        runner.run_background("bg", "裏", lambda ctx: {"analyzed": 1})
    (row,) = runner.snapshot()["history"]
    assert (row["origin"], row["runs"], row["summary"], row["cancellable"]) == ("background", 3, {"analyzed": 1 * 3}, False)
    runner.run_now("x", "X", lambda ctx: {})
    runner.run_background("bg", "裏", lambda ctx: {"analyzed": 1})
    assert [(j["kind"], j["runs"]) for j in runner.snapshot()["history"]] == [("bg", 1), ("x", 1), ("bg", 3)]
    # 他のジョブが動いている間は動かない
    gate = Gate()
    runner.submit("a", "A", gate.job("a"))
    assert gate.entered.wait(5)
    assert runner.run_background("bg", "裏", lambda ctx: {"analyzed": 1}) is None
    gate.go.set()


def test_failed_background_run_gets_its_own_row(runner):
    def boom(ctx):
        raise ValueError("x")
    runner.run_background("bg", "裏", lambda ctx: {"analyzed": 1})
    runner.run_background("bg", "裏", boom)
    assert [(j["status"], j["runs"]) for j in runner.snapshot()["history"]] == [("failed", 1), ("succeeded", 1)]


def test_jobs_left_running_by_a_previous_process_are_marked_failed(tmp_path):
    path = tmp_path / "jobs.db"
    c = db.open_db(path)
    with db.transaction(c):
        c.execute("INSERT INTO jobs (kind, label, origin, status, queued_at) VALUES ('scan', 'S', 'jobs', 'running', 'x')")
        c.execute("INSERT INTO jobs (kind, label, origin, status, queued_at) VALUES ('scan', 'S', 'jobs', 'queued', 'x')")
    c.close()
    r = JobRunner(path, threading.Lock())
    assert {(j["status"], j["error"]) for j in r.snapshot()["history"]} == {("failed", "サーバーが止まったため中断しました")}


def test_context_progress_raises_when_cancel_is_requested(runner):
    seen = []

    def run(ctx):
        ctx.progress(0, 2, "first")
        ctx.job.cancel_requested = True
        try:
            ctx.progress(1, 2, "second")
        except JobCancelled:
            seen.append("cancelled")
            raise
    job = runner.run_now("a", "A", run)
    assert seen == ["cancelled"] and job.status == "cancelled" and (job.done, job.total) == (1, 2)


# --- スキャンの中止 ---------------------------------------------------------------------


def test_stopped_scan_does_not_mark_unseen_sources_missing(make_settings, tmp_path):
    lib = tmp_path / "lib"
    for n in ("a", "b", "c"):
        write_zip(lib / f"{n}.cbz", [("1.jpg", page_bytes(n))])
    settings = make_settings(lib)
    conn = db.open_db(settings.db_path)
    Scanner(conn, settings).scan()
    seen = []
    result = Scanner(conn, settings, should_stop=lambda: len(seen) >= 1, on_source=seen.append).scan()
    assert result.stopped is True and seen == ["a.cbz"]
    assert conn.execute("SELECT COUNT(*) FROM sources WHERE missing = 1").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM volumes WHERE missing = 0").fetchone()[0] == 3
    conn.close()


def test_recheck_rereads_unchanged_archives_but_keeps_the_analysis(make_settings, tmp_path, monkeypatch):
    lib = tmp_path / "lib"
    write_zip(lib / "set.zip", [("v01.zip", zip_bytes([("1.jpg", page_bytes("1")), ("2.jpg", page_bytes("2"))]))])
    settings = make_settings(lib)
    client = TestClient(create_app(settings, frontend_dist=None))
    client.post("/api/scan")
    (vol,) = client.get("/api/volumes").json()
    client.get(f"/api/volumes/{vol['id']}")  # 開いてページ数・解析を記録する
    conn = db.connect(settings.db_path)
    before = conn.execute("SELECT analyzed_version, page_count FROM volumes WHERE id = ?", (vol["id"],)).fetchone()
    assert before["analyzed_version"] is not None and before["page_count"] == 2
    opened = []
    import spinebox.scanner as scanner
    real = scanner.open_archive
    monkeypatch.setattr(scanner, "open_archive", lambda path, **kw: (opened.append(path.name), real(path, **kw))[1])
    Scanner(conn, settings).scan()
    assert opened == []  # 普段のスキャンは変わっていないファイルを開かない
    Scanner(conn, settings, recheck=True).scan()
    assert opened == ["set.zip"]
    after = conn.execute("SELECT analyzed_version, page_count FROM volumes WHERE id = ?", (vol["id"],)).fetchone()
    assert tuple(after) == tuple(before)
    conn.close()


# --- API -------------------------------------------------------------------------------


def no_network_settings(settings):
    return dataclasses.replace(settings, metadata=MetadataSettings(sources=()))


@pytest.fixture
def app_env(make_settings, library, tmp_path):
    inbox = tmp_path / "inbox"
    write_zip(inbox / "新刊 第01巻.cbz", [("1.jpg", page_bytes("new"))])
    settings = dataclasses.replace(no_network_settings(make_settings(library)),
                                   inboxes=(InboxSetting(path=inbox, library=library),))
    client = TestClient(create_app(settings, frontend_dist=None))
    return client, settings, library


def wait_job(client, job_id, timeout=20.0):
    deadline = time.monotonic() + timeout
    while True:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] not in ("queued", "running"):
            return job
        assert time.monotonic() < deadline, job
        time.sleep(0.05)


def test_catalog_lists_every_job_with_counts(app_env):
    client, _, _ = app_env
    client.post("/api/scan")
    kinds = {k["kind"]: k for k in client.get("/api/jobs/catalog").json()["kinds"]}
    assert list(kinds) == [k.kind for k in KINDS]
    assert kinds["analyze"]["count"] >= 1 and kinds["analyze"]["count_text"].startswith("未解析")
    assert kinds["inbox_sync"]["available"] is True
    # 取得元が無効なら公開 DB のジョブは動かせない(理由つき)
    for k in ("daily_metadata", "metadata", "metadata_thumbs"):
        assert kinds[k]["available"] is False and "取得元" in kinds[k]["unavailable_reason"]
    assert kinds["metadata"]["options"] == [{"name": "all", "label": "全作品を取り直す"}]


@pytest.mark.parametrize("kind", ["daily_scan", "maintenance", "full_sync", "scan", "thumbnails", "analyze",
                                  "duplicates", "works_refresh", "inbox_sync"])
def test_each_job_can_be_started_and_is_recorded(app_env, kind):
    client, _, library = app_env
    before = snapshot(library)
    client.post("/api/scan")
    r = client.post("/api/jobs", json={"kind": kind})
    assert r.status_code == 202 and r.json()["origin"] == "jobs"
    job = wait_job(client, r.json()["id"])
    assert job["status"] == "succeeded", job
    assert job["summary"] is not None
    assert client.get("/api/jobs").json()["history"][0]["id"] == job["id"]
    assert snapshot(library) == before  # 蔵書の元ファイルは 1 バイトも変わらない


def test_unavailable_and_unknown_jobs_are_refused(app_env):
    client, _, _ = app_env
    assert client.post("/api/jobs", json={"kind": "metadata"}).status_code == 409
    assert client.post("/api/jobs", json={"kind": "nosuch"}).status_code == 422
    assert client.post("/api/jobs", json={"kind": "scan", "params": {"all": True}}).status_code == 422
    assert client.post("/api/jobs/9999/cancel").status_code == 404
    assert client.get("/api/jobs/9999").status_code == 404


def test_existing_buttons_record_the_same_history(app_env):
    client, _, _ = app_env
    assert client.post("/api/scan").status_code == 200
    assert client.post("/api/duplicates/scan?wait=true").status_code == 200
    assert client.post("/api/thumbnails/build?wait=true").status_code == 200
    assert client.post("/api/works/refresh").status_code == 200
    assert client.post("/api/inbox/sync").status_code == 200
    r = client.post("/api/duplicates/scan")
    assert r.status_code == 202
    wait_job(client, r.json()["job_id"])
    history = client.get("/api/jobs").json()["history"]
    assert [(j["kind"], j["origin"], j["status"]) for j in history] == [
        ("duplicates", "button", "succeeded"), ("inbox_sync", "button", "succeeded"),
        ("works_refresh", "button", "succeeded"), ("thumbnails", "button", "succeeded"),
        ("duplicates", "button", "succeeded"), ("scan", "button", "succeeded")]
    assert history[-1]["summary"]["volumes"] == len(client.get("/api/volumes").json())
    # 個別の状態の API は、同じ種類の最新のジョブを見せる
    assert client.get("/api/duplicates").json()["scan"]["job_id"] == history[0]["id"]
    assert client.get("/api/thumbnails").json()["job_id"] == history[3]["id"]


class Blocker:
    """ジョブの途中で止めておく(テストから中止・別の依頼を差し込むため)。"""

    def __init__(self, monkeypatch, target, name):
        self.entered = threading.Event()
        self.go = threading.Event()
        real = getattr(target, name)

        def blocked(*a, **kw):
            self.entered.set()
            assert self.go.wait(10)
            return real(*a, **kw)
        monkeypatch.setattr(target, name, blocked)


def test_second_job_waits_and_sync_buttons_are_refused_while_one_runs(app_env, monkeypatch):
    client, _, _ = app_env
    client.post("/api/scan")
    block = Blocker(monkeypatch, Scanner, "_scan_archive")
    first = client.post("/api/jobs", json={"kind": "maintenance"}).json()
    assert block.entered.wait(5)
    second = client.post("/api/jobs", json={"kind": "works_refresh"}).json()
    assert second["status"] == "queued"
    snap = client.get("/api/jobs").json()
    assert snap["current"]["id"] == first["id"] and [j["id"] for j in snap["queue"]] == [second["id"]]
    assert snap["current"]["step"] == "ファイルのスキャン"
    # その場で結果を返す API は待たずに 409
    assert client.post("/api/scan").status_code == 409
    assert client.post("/api/works/refresh").status_code == 409
    assert client.post("/api/thumbnails/build?wait=true").status_code == 409
    assert client.post("/api/inbox/sync").status_code == 409
    # 蔵書を動かす処理(受け入れの承認・整理の実行)も、ジョブの実行中は 409
    assert client.post("/api/inbox/approve", json={"items": []}).status_code == 409
    block.go.set()
    assert wait_job(client, first["id"])["status"] == "succeeded"
    assert wait_job(client, second["id"])["status"] == "succeeded"


def test_cancelled_scan_keeps_the_db_and_the_library(app_env, monkeypatch):
    client, settings, library = app_env
    before = snapshot(library)
    client.post("/api/scan")
    volumes = client.get("/api/volumes").json()
    block = Blocker(monkeypatch, Scanner, "_scan_archive")
    job = client.post("/api/jobs", json={"kind": "maintenance"}).json()
    assert block.entered.wait(5)
    r = client.post(f"/api/jobs/{job['id']}/cancel")
    assert r.status_code == 200 and r.json()["cancel_requested"] is True
    block.go.set()
    done = wait_job(client, job["id"])
    assert done["status"] == "cancelled" and done["error"] == "中止しました"
    assert client.post(f"/api/jobs/{job['id']}/cancel").status_code == 409  # 終わったジョブ
    # 途中で止めても、見ていないソースを見つからない扱いにしない。一覧はそのまま
    assert client.get("/api/volumes").json() == volumes
    conn = db.connect(settings.db_path)
    assert conn.execute("SELECT COUNT(*) FROM sources WHERE missing = 1").fetchone()[0] == 0
    conn.close()
    assert snapshot(library) == before
    # 次のジョブは普通に動く
    again = client.post("/api/jobs", json={"kind": "maintenance"}).json()
    assert wait_job(client, again["id"])["status"] == "succeeded"


def test_cancelled_duplicate_scan_keeps_earlier_fingerprints(app_env, monkeypatch):
    client, settings, library = app_env
    client.post("/api/scan")
    import spinebox.duplicates as duplicates
    block = Blocker(monkeypatch, duplicates.DuplicateScanner, "_fingerprint")
    job = client.post("/api/jobs", json={"kind": "duplicates"}).json()
    assert block.entered.wait(5)
    client.post(f"/api/jobs/{job['id']}/cancel")
    block.go.set()
    assert wait_job(client, job["id"])["status"] == "cancelled"
    conn = db.connect(settings.db_path)
    assert conn.execute("SELECT COUNT(*) FROM volume_fingerprints").fetchone()[0] == 1  # 止めるまでに作った 1 巻
    conn.close()
    monkeypatch.undo()
    again = client.post("/api/jobs", json={"kind": "duplicates"}).json()
    assert wait_job(client, again["id"])["status"] == "succeeded"


def test_failed_job_is_recorded_and_scan_and_reading_keep_working(app_env, monkeypatch):
    client, _, _ = app_env
    client.post("/api/scan")
    import spinebox.duplicates as duplicates

    def boom(self, limit=None):
        raise RuntimeError("壊れた")
    monkeypatch.setattr(duplicates.DuplicateScanner, "run", boom)
    job = client.post("/api/jobs", json={"kind": "duplicates"}).json()
    assert wait_job(client, job["id"])["error"] == "RuntimeError: 壊れた"
    assert client.post("/api/scan").status_code == 200
    vid = client.get("/api/volumes").json()[0]["id"]
    assert client.get(f"/api/volumes/{vid}/pages/0").status_code == 200


def test_analyze_job_records_unreadable_archives_without_7zip(app_env):
    """7-Zip が無い環境で 7z / rar の中の巻を調べても、理由を記録して終わる(ジョブは成功)。"""
    client, settings, _ = app_env
    client.post("/api/scan")
    job = wait_job(client, client.post("/api/jobs", json={"kind": "analyze"}).json()["id"])
    assert job["status"] == "succeeded" and job["summary"]["analyzed"] >= 1
    assert client.get("/api/jobs/catalog").json()["kinds"][8]["count"] == 0  # 未解析は残らない(読めないものも記録済み)


def test_background_analysis_is_recorded_as_one_row(make_settings, tmp_path):
    lib = tmp_path / "lib"
    for n in ("a", "b"):
        write_zip(lib / f"{n}.zip", [(f"{n}1.zip", zip_bytes([("1.jpg", page_bytes(n))])),
                                     (f"{n}2.zip", zip_bytes([("1.jpg", page_bytes(n + "2"))]))])
    settings = dataclasses.replace(no_network_settings(make_settings(lib)), background_analysis=True,
                                   background_analysis_interval=1)
    with TestClient(create_app(settings, frontend_dist=None)) as c:
        c.post("/api/scan")

        def analyzed():
            rows = [j for j in c.get("/api/jobs").json()["history"] if j["kind"] == "background_analysis"]
            return rows and rows[0]["summary"]["analyzed"] >= 4
        wait_until(analyzed, timeout=20, what="裏の解析")
        rows = [j for j in c.get("/api/jobs").json()["history"] if j["kind"] == "background_analysis"]
        assert len(rows) == 1 and rows[0]["origin"] == "background" and rows[0]["runs"] >= 4


def test_metadata_jobs_with_a_fake_network(make_settings, tmp_path):
    from test_linking import KAKU_FILES, cbz, network
    lib = tmp_path / "lib"
    lib.mkdir()
    for f in KAKU_FILES:
        cbz(lib, f)
    meta = MetadataSettings(max_retries=0)
    settings = dataclasses.replace(make_settings(lib), metadata=meta)
    net = network("kaku")
    clock = FakeClock()
    fetcher = HttpFetcher(meta, transport=net.transport, clock=clock, sleep=clock.sleep)
    client = TestClient(create_app(settings, frontend_dist=None, metadata_fetcher=fetcher))
    client.post("/api/scan")
    kinds = {k["kind"]: k for k in client.get("/api/jobs/catalog").json()["kinds"]}
    assert kinds["daily_metadata"]["count"] == 1 and kinds["daily_metadata"]["count_text"] == "登録済みの 1 作品が補完待ち"
    job = wait_job(client, client.post("/api/jobs", json={"kind": "daily_metadata"}).json()["id"])
    assert job["status"] == "succeeded" and job["summary"]["fetched"] == 1
    assert client.get("/api/jobs/catalog").json()["kinds"][1]["count"] == 0
    job = wait_job(client, client.post("/api/jobs", json={"kind": "metadata", "params": {"all": True}}).json()["id"])
    assert job["params"] == {"all": True} and job["summary"]["works"] == 1
    job = wait_job(client, client.post("/api/jobs", json={"kind": "metadata_thumbs"}).json()["id"])
    assert job["status"] == "succeeded" and set(job["summary"]) == {"metadata", "thumbnails"}
    # 作品ページの「情報を取得」と一覧の「情報を補完」も同じ履歴に残る
    wid = client.get("/api/works").json()["works"][0]["id"]
    assert client.post(f"/api/works/{wid}/metadata/fetch").status_code == 200
    assert client.post("/api/works/metadata/fetch?wait=true").json()["status"] == "succeeded"
    history = client.get("/api/jobs").json()["history"]
    assert [(j["kind"], j["origin"]) for j in history[:2]] == [("metadata", "button"), ("work_metadata", "button")]
    assert history[1]["label"].startswith("作品の情報を取得: ")
    assert client.get("/api/works/metadata").json()["state"]["job_id"] == history[0]["id"]


# --- フォローの続刊の確認(#87)をジョブに載せたもの ---------------------------------------------


@pytest.fixture
def follow_env(make_settings, tmp_path):
    from test_follows import Env, kaku_files
    env = Env(make_settings, tmp_path, "kaku", [*kaku_files(1, 2, 3, 4), "別の作品 第01巻.cbz", "別の作品 第02巻.cbz"])
    env.kaku, env.other = env.work_id("かくかくしかじか"), env.work_id("別の作品")
    env.follow(env.kaku)
    env.follow(env.other)
    return env


def test_follow_check_job_and_button_api_share_one_history(follow_env):
    env = follow_env
    client = env.client
    kinds = {k["kind"]: k for k in client.get("/api/jobs/catalog").json()["kinds"]}
    assert kinds["follow_check"]["available"] is True and kinds["follow_check"]["count_text"] == "フォロー中 2 作品"
    # データ管理から: 作品を絞れる。フォローしていない作品の id は飛ばす
    r = client.post("/api/jobs", json={"kind": "follow_check", "params": {"work_ids": [env.kaku, 99999]}})
    assert r.status_code == 202
    job = wait_job(client, r.json()["id"])
    assert job["status"] == "succeeded" and job["origin"] == "jobs"
    assert job["summary"] == {"works": 1, "statuses": {"ok": 1}}
    assert env.entry(env.kaku)["checked_at"] and env.entry(env.other)["checked_at"] is None
    # 既存のボタンの API(wait)も同じ種類のジョブとして履歴に残り、GET /api/follows の state はその最新を見せる
    state = env.check()
    assert state["summary"] == {"works": 2, "statuses": {"ok": 1, "unlinked": 1}} and state["status"] == "succeeded"
    history = client.get("/api/jobs").json()["history"]
    assert [(j["kind"], j["origin"]) for j in history[:2]] == [("follow_check", "button"), ("follow_check", "jobs")]
    assert env.follows()["state"]["job_id"] == history[0]["id"]


def test_follow_check_runs_one_at_a_time_and_waiting_button_is_refused(follow_env, monkeypatch):
    from spinebox import follows
    env = follow_env
    client = env.client
    block = Blocker(monkeypatch, follows, "check_work")
    first = client.post("/api/jobs", json={"kind": "follow_check"}).json()
    assert block.entered.wait(5)
    assert client.post("/api/follows/check?wait=true").status_code == 409  # 動いている間は待たずに断る
    again = client.post("/api/follows/check")  # 待ち行列に入る側は、同じジョブ(同じ指定)を返す
    assert again.status_code == 202 and again.json()["job_id"] == first["id"]
    block.go.set()
    assert wait_job(client, first["id"])["status"] == "succeeded"


def test_follow_check_can_be_cancelled_between_works(follow_env, monkeypatch):
    from spinebox import follows
    env = follow_env
    client = env.client
    block = Blocker(monkeypatch, follows, "check_work")
    job = client.post("/api/jobs", json={"kind": "follow_check"}).json()
    assert block.entered.wait(5)
    assert client.post(f"/api/jobs/{job['id']}/cancel").status_code == 200
    block.go.set()
    done = wait_job(client, job["id"])
    assert done["status"] == "cancelled" and done["error"] == "中止しました"
    # 調べた 1 作品の結果は残り、残りは調べていない。フォローも見つけた巻も消えない
    checked = [env.entry(w)["checked_at"] for w in (env.kaku, env.other)]
    assert sum(c is not None for c in checked) == 1
    assert len(env.follows()["follows"]) == 2
    assert wait_job(client, client.post("/api/jobs", json={"kind": "follow_check"}).json()["id"])["status"] == "succeeded"


def test_follow_check_is_unavailable_when_ndl_is_disabled(make_settings, tmp_path):
    from test_follows import Env, kaku_files
    env = Env(make_settings, tmp_path, "kaku", kaku_files(1), sources=("anilist",))
    kinds = {k["kind"]: k for k in env.client.get("/api/jobs/catalog").json()["kinds"]}
    assert kinds["follow_check"]["available"] is False and "国立国会図書館サーチ" in kinds["follow_check"]["unavailable_reason"]
    r = env.client.post("/api/jobs", json={"kind": "follow_check"})
    assert r.status_code == 409 and "国立国会図書館サーチ" in r.json()["detail"]
    assert env.client.post("/api/follows/check").status_code == 409


def test_background_follow_check_is_recorded_as_one_row(make_settings, tmp_path):
    from test_follows import Env, kaku_files
    from spinebox.config import FollowSettings
    env = Env(make_settings, tmp_path, "kaku", kaku_files(1, 2, 3, 4),
              follow=FollowSettings(background=True, background_interval_seconds=1, interval_hours=0.0001,
                                    response_ttl_days=0.0))
    wid = env.work_id()
    env.follow(wid)
    with env.client:
        wait_until(lambda: env.entry(wid)["checked_at"] is not None, timeout=15, what="裏の続刊の確認")
        wait_until(lambda: any(j["kind"] == "background_follow" for j in env.client.get("/api/jobs").json()["history"]),
                   timeout=15, what="裏の確認の履歴")
    rows = [j for j in env.client.get("/api/jobs").json()["history"] if j["kind"] == "background_follow"]
    assert len(rows) == 1 and rows[0]["origin"] == "background" and rows[0]["label"] == "裏の続刊の確認"
    assert rows[0]["summary"]["works"] >= 1
