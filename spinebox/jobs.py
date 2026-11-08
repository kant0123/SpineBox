"""データ管理のジョブ(#83)。スキャン・補完・表紙などを 1 本ずつ動かし、履歴を DB(`jobs`)に残す。

- 実行は 1 本ずつ。ジョブは `lock`(整理処理の実行・元に戻す・受け入れの承認と共有)を持って動くので、
  蔵書・DB を同時に触らない。依頼は待ち行列に入る(`submit`)。
- その場で結果を返す API(一覧の「スキャン」など)は `run_now` で依頼した要求のスレッドで動かす。
  他のジョブが実行中・待ち中なら待たずに `Busy`(HTTP の要求を何分も待たせないため)。
  裏の定期処理が動いているだけなら、それが終わるのを少し待つ(1 件ずつなので短い)。
- 裏の定期処理(`run_background`)は、何もしていないときだけ 1 件動かす。続けて動いた分は履歴の 1 行にまとめる。
- 中止は区切り(`JobContext.progress` / `check`)で `JobCancelled` を投げて止める。ジョブは蔵書を読むだけで、
  DB の書き込みは 1 件ごとのトランザクションなので、途中で止めても壊れない。
"""
from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import db
from .scanner import describe_exception, safe_text

log = logging.getLogger(__name__)

STATUSES = ("queued", "running", "succeeded", "failed", "cancelled")
ACTIVE = ("queued", "running")
ORIGINS = ("jobs", "button", "background")
# run_now が、裏の定期処理(1 件ずつ)の終わりを待つ上限の秒数
BACKGROUND_WAIT_SECONDS = 30.0
INTERRUPTED = "サーバーが止まったため中断しました"
CANCELLED = "中止しました"


class JobCancelled(Exception):
    """中止の依頼を区切りで受け取った。"""


class Busy(Exception):
    """他のジョブが実行中・待ち中なので、その場では動かせない。"""


class JobNotFound(Exception):
    pass


class NotCancellable(Exception):
    """終わったジョブ・裏の定期処理は中止できない。"""


JobFn = Callable[["JobContext"], "dict | None"]


@dataclass
class Job:
    id: int | None
    kind: str
    label: str
    origin: str
    params: dict
    status: str
    queued_at: str
    fn: JobFn | None = None
    started_at: str | None = None
    finished_at: str | None = None
    done: int | None = None
    total: int | None = None
    current: str | None = None
    step: str | None = None
    # 複数の段を順に行うジョブの、今の段の番号(1 始まり)と段の数(#207)。段の数を決めていないジョブは None
    step_index: int | None = None
    step_count: int | None = None
    runs: int = 1
    summary: dict | None = None
    error: str | None = None
    cancel_requested: bool = False
    finished: threading.Event = field(default_factory=threading.Event)

    def to_json(self) -> dict:
        return {
            "id": self.id, "kind": self.kind, "label": self.label, "origin": self.origin, "params": self.params,
            "status": self.status, "queued_at": self.queued_at, "started_at": self.started_at,
            "finished_at": self.finished_at, "done": self.done, "total": self.total, "current": self.current,
            "step": self.step, "step_index": self.step_index, "step_count": self.step_count,
            "runs": self.runs, "summary": self.summary, "error": self.error,
            "cancel_requested": self.cancel_requested,
            "cancellable": self.id is not None and self.origin != "background" and self.status in ACTIVE,
        }


def _row_to_job(r) -> Job:
    return Job(id=r["id"], kind=r["kind"], label=r["label"], origin=r["origin"], params=json.loads(r["params"]),
               status=r["status"], queued_at=r["queued_at"], started_at=r["started_at"],
               finished_at=r["finished_at"], done=r["done"], total=r["total"], runs=r["runs"],
               summary=json.loads(r["summary"]) if r["summary"] else None, error=r["error"])


class JobContext:
    """ジョブの関数に渡す。進み具合を書き、区切りで中止を受け取る。"""

    def __init__(self, job: Job):
        self.job = job

    def cancelled(self) -> bool:
        return self.job.cancel_requested

    def check(self) -> None:
        if self.job.cancel_requested:
            raise JobCancelled()

    def plan(self, steps: int) -> None:
        """これから行う段の数。画面は「段 2/5」と出す(段ごとに進み具合が 0 に戻っても、全体のどこかが分かる)。"""
        self.job.step_count = steps
        self.job.step_index = 0

    def step(self, name: str) -> None:
        """複数の処理を順に行うジョブの、今の段。進み具合は段ごとに数え直す。"""
        self.check()
        if self.job.step_count is not None:
            self.job.step_index = min((self.job.step_index or 0) + 1, self.job.step_count)
        self.job.step = name
        self.job.done = self.job.total = None
        self.job.current = None

    def progress(self, done: int | None = None, total: int | None = None, current: str | None = None) -> None:
        """進み具合を書く(渡したものだけ)。中止の依頼があれば JobCancelled。"""
        if done is not None:
            self.job.done = done
        if total is not None:
            self.job.total = total
        if current is not None:
            self.job.current = safe_text(current)
        self.check()


def _merge_summary(old: dict | None, new: dict | None) -> dict | None:
    """裏の定期処理の要約をまとめる(数は足す)。"""
    if not old:
        return new
    out = dict(old)
    for k, v in (new or {}).items():
        if isinstance(v, int) and isinstance(out.get(k), int):
            out[k] = out[k] + v
        else:
            out[k] = v
    return out


class JobRunner:
    def __init__(self, db_path: Path | str, lock: threading.Lock):
        self.db_path = db_path
        self.lock = lock
        self._cv = threading.Condition()
        self._queue: list[Job] = []
        self._current: Job | None = None
        self._worker: threading.Thread | None = None
        self._stopped = False
        self._mark_interrupted()

    # --- DB -------------------------------------------------------------------------

    def _conn(self):
        return db.connect(self.db_path)

    def _mark_interrupted(self) -> None:
        """前のプロセスで待ち・実行中のまま終わった行を失敗にする(このプロセスでは動いていない)。"""
        c = self._conn()
        try:
            with db.transaction(c):
                c.execute("UPDATE jobs SET status = 'failed', error = ?, finished_at = ? WHERE status IN ('queued', 'running')",
                          (INTERRUPTED, db.now()))
        finally:
            c.close()

    def _insert(self, job: Job) -> None:
        c = self._conn()
        try:
            with db.transaction(c):
                cur = c.execute(
                    """INSERT INTO jobs (kind, label, origin, params, status, queued_at, started_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (job.kind, job.label, job.origin, json.dumps(job.params, ensure_ascii=False), job.status,
                     job.queued_at, job.started_at))
                job.id = cur.lastrowid
        finally:
            c.close()

    def _save(self, job: Job) -> None:
        c = self._conn()
        try:
            with db.transaction(c):
                c.execute(
                    """UPDATE jobs SET label = ?, status = ?, started_at = ?, finished_at = ?, done = ?, total = ?,
                              runs = ?, summary = ?, error = ? WHERE id = ?""",
                    (job.label, job.status, job.started_at, job.finished_at, job.done, job.total, job.runs,
                     json.dumps(job.summary, ensure_ascii=False) if job.summary is not None else None,
                     job.error, job.id))
        finally:
            c.close()

    # --- 依頼 -----------------------------------------------------------------------

    def _ensure_worker(self) -> None:
        if self._worker is None or not self._worker.is_alive():
            self._worker = threading.Thread(target=self._work, name="spinebox-jobs", daemon=True)
            self._worker.start()

    def submit(self, kind: str, label: str, fn: JobFn, *, origin: str = "jobs", params: dict | None = None) -> Job:
        """待ち行列に入れる。同じ種類・同じ指定のジョブが待ち・実行中なら、新しく足さずにそれを返す。"""
        params = params or {}
        with self._cv:
            for j in ([self._current] if self._current else []) + self._queue:
                if j.kind == kind and j.params == params and j.status in ACTIVE and not j.cancel_requested:
                    return j
            job = Job(None, kind, label, origin, params, "queued", db.now(), fn)
            self._insert(job)
            self._queue.append(job)
            self._ensure_worker()
            self._cv.notify_all()
        return job

    def busy(self) -> bool:
        with self._cv:
            return bool(self._queue) or self._current is not None or self.lock.locked()

    def run_now(self, kind: str, label: str, fn: JobFn, *, origin: str = "button", params: dict | None = None) -> Job:
        """呼んだスレッドでその場で動かし、終わったジョブを返す(失敗・中止も返す)。動かせなければ Busy。"""
        with self._cv:
            current = self._current
            if self._queue or (current is not None and current.origin != "background"):
                raise Busy()
        wait = BACKGROUND_WAIT_SECONDS if current is not None else 0
        if not (self.lock.acquire(timeout=wait) if wait else self.lock.acquire(blocking=False)):
            raise Busy()
        try:
            with self._cv:
                job = Job(None, kind, label, origin, params or {}, "running", db.now(), fn, started_at=db.now())
                self._insert(job)
                self._current = job
            self._execute(job)
        finally:
            self.lock.release()
            with self._cv:
                self._cv.notify_all()
        return job

    def run_background(self, kind: str, label: str, fn: JobFn) -> Job | None:
        """裏の定期処理を 1 回動かす。何も動いていない・待ちが無いときだけ。

        fn が None を返したら(することが無かった)履歴に残さない。直前の履歴の行が同じ種類の裏の処理なら、その行にまとめる。
        """
        with self._cv:
            if self._queue or self._current is not None:
                return None
            if not self.lock.acquire(blocking=False):
                return None
            job = Job(None, kind, label, "background", {}, "running", db.now(), fn, started_at=db.now())
            self._current = job
        try:
            try:
                summary = fn(JobContext(job))
                job.status, job.summary = "succeeded", summary
            except Exception as e:  # noqa: BLE001 (裏の処理で本体を止めない。失敗は履歴に残す)
                log.exception("%s に失敗しました", label)
                job.status, job.error, summary = "failed", describe_exception(e), {}
            job.finished_at = db.now()
            if summary is not None:
                self._record_background(job)
        finally:
            with self._cv:
                self._current = None
                self._cv.notify_all()
            self.lock.release()
            job.finished.set()
        return job

    def _record_background(self, job: Job) -> None:
        c = self._conn()
        try:
            last = c.execute("SELECT * FROM jobs ORDER BY id DESC LIMIT 1").fetchone()
        finally:
            c.close()
        if (job.status == "succeeded" and last is not None and last["kind"] == job.kind
                and last["origin"] == "background" and last["status"] == "succeeded"):
            prev = _row_to_job(last)
            prev.runs += 1
            prev.done = prev.runs
            prev.finished_at = job.finished_at
            prev.summary = _merge_summary(prev.summary, job.summary)
            job.id, job.runs, job.started_at, job.summary = prev.id, prev.runs, prev.started_at, prev.summary
            self._save(prev)
            return
        job.done = 1
        self._insert(job)
        self._save(job)

    def cancel(self, job_id: int) -> Job:
        """待ちなら取り下げ、実行中なら次の区切りで止める。"""
        with self._cv:
            for j in self._queue:
                if j.id == job_id:
                    self._queue.remove(j)
                    j.cancel_requested = True
                    j.status, j.finished_at, j.error = "cancelled", db.now(), CANCELLED
                    self._save(j)
                    j.finished.set()
                    return j
            if self._current is not None and self._current.id == job_id:
                if self._current.origin == "background":
                    raise NotCancellable(job_id)
                self._current.cancel_requested = True
                return self._current
        if self.get(job_id) is None:
            raise JobNotFound(job_id)
        raise NotCancellable(job_id)

    # --- 実行 -----------------------------------------------------------------------

    def _work(self) -> None:
        while True:
            with self._cv:
                while not self._queue and not self._stopped:
                    self._cv.wait()
                if self._stopped:
                    return
            self.lock.acquire()
            try:
                with self._cv:
                    if not self._queue:  # 待っている間に取り下げられた
                        continue
                    job = self._queue.pop(0)
                    self._current = job
                job.status, job.started_at = "running", db.now()
                self._save(job)
                self._execute(job)
            finally:
                self.lock.release()

    def _execute(self, job: Job) -> None:
        ctx = JobContext(job)
        try:
            ctx.check()
            job.summary = job.fn(ctx) if job.fn else None
            job.status = "succeeded"
        except JobCancelled:
            job.status, job.error = "cancelled", CANCELLED
        except Exception as e:  # noqa: BLE001 (ジョブの失敗は履歴に残して本体を止めない。トレースバックはログへ)
            log.exception("ジョブ %s に失敗しました", job.label)
            job.status, job.error = "failed", describe_exception(e)
        finally:
            job.finished_at = db.now()
            job.current = None
            try:
                self._save(job)
            except Exception:  # noqa: BLE001 (履歴を書けなくても待ち行列は進める)
                log.exception("ジョブの履歴を書けませんでした")
            with self._cv:
                if self._current is job:
                    self._current = None
                self._cv.notify_all()
            job.finished.set()

    def stop(self) -> None:
        with self._cv:
            self._stopped = True
            self._cv.notify_all()

    # --- 参照 -----------------------------------------------------------------------

    def get(self, job_id: int) -> Job | None:
        with self._cv:
            for j in ([self._current] if self._current else []) + self._queue:
                if j.id == job_id:
                    return j
        c = self._conn()
        try:
            r = c.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        finally:
            c.close()
        return _row_to_job(r) if r else None

    def latest(self, kind: str) -> Job | None:
        """その種類の最新のジョブ(待ち・実行中を優先)。"""
        with self._cv:
            for j in ([self._current] if self._current else []) + self._queue:
                if j.kind == kind:
                    return j
        c = self._conn()
        try:
            r = c.execute("SELECT * FROM jobs WHERE kind = ? ORDER BY id DESC LIMIT 1", (kind,)).fetchone()
        finally:
            c.close()
        return _row_to_job(r) if r else None

    def snapshot(self, limit: int = 50) -> dict:
        with self._cv:
            current = self._current.to_json() if self._current else None
            queue = [j.to_json() for j in self._queue]
            active = {j["id"] for j in queue} | ({current["id"]} if current else set())
        c = self._conn()
        try:
            rows = c.execute("SELECT * FROM jobs ORDER BY id DESC LIMIT ?", (limit + len(active),)).fetchall()
        finally:
            c.close()
        history = [_row_to_job(r).to_json() for r in rows if r["id"] not in active][:limit]
        return {"current": current, "queue": queue, "history": history}


def legacy_state(job: Job | None) -> dict:
    """個別の API(重複・表紙・公開 DB)が返していた状態の形。待ちも running: true にする(画面が見に行き続けるため)。"""
    if job is None:
        return {"running": False, "started_at": None, "finished_at": None, "done": 0, "total": 0, "current": None,
                "summary": None, "error": None, "job_id": None, "status": None}
    return {"running": job.status in ACTIVE, "started_at": job.started_at or job.queued_at,
            "finished_at": job.finished_at, "done": job.done or 0, "total": job.total or 0, "current": job.current,
            "summary": job.summary, "error": job.error, "job_id": job.id, "status": job.status}
