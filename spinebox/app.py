"""FastAPI アプリ。API の契約はフロント(frontend/)と共有している(wiki/components/backend.md)。

`frontend/dist` があれば `/` で静的配信し、API 以外の未知のパスは index.html を返す(SPA の fallback)。
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import logging
import sqlite3
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Iterator, Literal

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field, StrictBool, StrictInt, StrictStr

from . import bookmarks, db, follows, linking, search, works
from .analysis import compute_findings
from .datajobs import KIND_BY_NAME, DataJobs, Unavailable, kind_label, work_title
from .duplicates import CandidateNotFound, candidate_counts, dismiss
from .jobs import Busy, Job, JobNotFound, JobRunner, NotCancellable, legacy_state
from .buildinfo import current_commit
from .cache import CacheTooLarge, ExtractionCache, MemoryCache
from .config import Settings
from .library import (Library, NeedsExtraction, VolumeNotFound, VolumeOffline, VolumeUnreadable, get_volume_row,
                      is_analyzable, is_offline)
from .novel.books import Books
from .metadata import MetadataService
from .metadata.service import shared_fetcher
from .metadata.http import HttpFetcher
from .organize.intake import MAX_NUMBER, Decision, Intake
from .organize.organizer import OperationNotFound, OrganizeError, Organizer, RangeError, RangeNotFound
from .organize.template import PARAMETERS, PRESETS, NameTemplate, TemplateError, range_examples
from .pages import media_type, natural_key
from .scanner import LISTED_VOLUMES_JOIN, LISTED_VOLUMES_WHERE, safe_text
from .thumbs import ThumbnailUnavailable, Thumbnails

log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FRONTEND_DIST = REPO_ROOT / "frontend" / "dist"
PAGE_CACHE_CONTROL = "private, max-age=3600"
# 画面(index.html と静的ファイル)の CSP。EPUB の本文は無害化してから画面に差し込むが、万一すり抜けても
# インラインのスクリプト・イベントハンドラ・外部の読み込みが動かないようにする(wiki/components/novel-reader.md)
APP_CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
           "font-src 'self' data:; connect-src 'self'; media-src 'none'; object-src 'none'; frame-src 'none'; "
           "base-uri 'none'; form-action 'self'; frame-ancestors 'none'")
# EPUB の画像の資源。直接開かれても何も動かさない
RESOURCE_HEADERS = {"Content-Security-Policy": "default-src 'none'; sandbox", "X-Content-Type-Options": "nosniff",
                    "Cache-Control": PAGE_CACHE_CONTROL}


class OrganizePlanIn(BaseModel):
    """どちらか一方を指定する。volume_id ならその巻を含むソース(元ファイル/フォルダ)全体が対象。
    naming: 出力名を巻の表示名(title)にするか、命名テンプレート(template)にするか。"""
    volume_id: StrictInt | None = None
    source_id: StrictInt | None = None
    naming: Literal["title", "template"] = "title"


class RenamePlanIn(BaseModel):
    """作品の全巻をテンプレートの名前へ改名・移動する計画。exclude_volume_ids は衝突などで外す巻。"""
    work_id: StrictInt
    exclude_volume_ids: list[StrictInt] = Field(default_factory=list)


class RangePlanIn(BaseModel):
    """範囲指定の整理(#84)。path は蔵書ルートからの相対(空ならルート全体。サブフォルダも対象)。
    file_template / folder_template が両方空ならファイル名の整理だけ。exclude_volume_ids は外す巻。"""
    root_id: StrictInt
    path: StrictStr = ""
    file_template: StrictStr = ""
    folder_template: StrictStr = ""
    exclude_volume_ids: list[StrictInt] = Field(default_factory=list)


class RangeExampleIn(BaseModel):
    file_template: StrictStr = ""
    folder_template: StrictStr = ""
    path: StrictStr = ""


class InboxDecisionIn(BaseModel):
    """受け入れフォルダの 1 件(ソース)について、利用者が選んだこと。省略したものは自動(確信度が高いときだけ決まる)。
    numbers は巻 id(文字列)→ 巻の番号。expect_outputs は承認で、プレビューで見た出力先(違っていたら実行しない)。"""
    source_id: StrictInt
    work_id: StrictInt | None = None
    new_title: StrictStr | None = None
    numbers: dict[str, StrictInt] = Field(default_factory=dict)
    skip_volume_ids: list[StrictInt] = Field(default_factory=list)
    expect_outputs: list[StrictStr] | None = None

    def to_decision(self) -> Decision:
        try:
            numbers = {int(k): v for k, v in self.numbers.items()}
        except ValueError:
            raise HTTPException(422, "numbers のキーは巻の id(整数)で指定してください") from None
        if any(not 0 <= v <= MAX_NUMBER for v in numbers.values()):
            raise HTTPException(422, f"巻の番号は 0〜{MAX_NUMBER} で指定してください")
        return Decision(self.source_id, self.work_id, self.new_title, numbers, frozenset(self.skip_volume_ids),
                        self.expect_outputs)


class InboxItemsIn(BaseModel):
    items: list[InboxDecisionIn] = Field(max_length=1000)


class ProgressIn(BaseModel):
    """page は画像の巻ならページ番号、小説なら区切りの番号。position は小説だけ(区切りの中の割合 0〜1)。"""
    page: StrictInt = Field(ge=0)
    finished: StrictBool
    position: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)


class WorkEditIn(BaseModel):
    """作品の手動編集。送ったものだけ直す(title 以外は null / 空文字 / 空配列で空欄にできる)。"""
    title: StrictStr | None = None
    author: StrictStr | None = None
    kind: StrictStr | None = None  # manga / novel
    publisher: StrictStr | None = None
    genres: list[StrictStr] | None = None
    tags: list[StrictStr] | None = None


class VolumeMetaIn(BaseModel):
    """巻の書誌の手動編集。送ったものだけ直す(null / 空文字で空欄にできる)。"""
    publisher: StrictStr | None = None
    label: StrictStr | None = None
    released: StrictStr | None = None
    isbn: StrictStr | None = None


class ChooseIn(BaseModel):
    """確認待ちの候補を選ぶ。source_id が null なら「該当なし」。"""
    source_id: StrictStr | None = None


class SeenIn(BaseModel):
    """見つけた巻を「確認済み」にする。volumes を省くと、その作品のまだ確認していない巻すべて。"""
    work_id: StrictInt
    volumes: list[StrictInt] | None = None


class FollowCheckIn(BaseModel):
    """続刊を調べる作品。省くとフォロー中の全作品。"""
    work_ids: list[StrictInt] | None = None


class AliasIn(BaseModel):
    alias: StrictStr


class MergeIn(BaseModel):
    other_id: StrictInt


class JobIn(BaseModel):
    """データ管理のジョブの依頼。params は種類ごとの指定(作品情報のみ補完の all、続刊の確認の work_ids)。"""
    kind: StrictStr
    params: dict[str, StrictBool | list[StrictInt]] = Field(default_factory=dict)


class AssignIn(BaseModel):
    """巻の付け替え。work_id が null なら新しい作品に分ける(title は新しい作品の名前。省略すると巻の名前から)。"""
    volume_ids: list[StrictInt] = Field(min_length=1)
    work_id: StrictInt | None = None
    title: StrictStr | None = None


def _progress(row) -> dict | None:
    if row is None or row["page"] is None:
        return None
    out = {"page": row["page"], "finished": bool(row["finished"])}
    if "position" in row.keys() and row["position"] is not None:
        out["position"] = row["position"]
    return out


def _format(row) -> str:
    """巻の形式。images = ページ画像の巻(漫画など)、epub / text = 小説。"""
    return row["format"] or "images"


def _number(row) -> dict | None:
    """名前から読んだ巻・話の番号の候補(確定ではない)。"""
    if row["number_kind"] is None:
        return None
    return {"kind": row["number_kind"], "start": row["number_start"], "end": row["number_end"],
            "evidence": row["number_evidence"]}


def _chain_text(inner_path: str | None) -> str:
    try:
        return "/".join(json.loads(inner_path)) if inner_path else ""
    except ValueError:
        return inner_path or ""


def _list_sort_key(r) -> tuple:
    """一覧の並び。子の巻は親の位置に、入れ子の名前・フォルダの自然順で並べる。"""
    if r["parent_id"] is None:
        return (natural_key(r["rel_path"]), natural_key(r["entry_prefix"]), natural_key(r["container_path"] or ""),
                natural_key(""), natural_key(""))
    return (natural_key(r["rel_path"]), natural_key(r["parent_prefix"] or ""), natural_key(r["container_path"] or ""),
            natural_key(_chain_text(r["inner_path"])), natural_key(r["entry_prefix"]))


def _sync_works(conn: sqlite3.Connection, *, apply_links: bool = True) -> None:
    """巻を作品に割り当て直し、公開 DB の連携の中身を新しい巻・統合後の作品へ反映する。

    名前の解析と DB の更新だけ(ネットワークには出ない)で、失敗しても本体(スキャン・閲覧)を止めない。
    """
    try:
        works.sync(conn)
        if apply_links:
            linking.apply_all(conn)
    except Exception:  # noqa: BLE001 (派生データの更新で本体を止めない)
        log.exception("作品の割り当てに失敗しました")


def create_app(settings: Settings, frontend_dist: Path | None = DEFAULT_FRONTEND_DIST,
               metadata_fetcher: HttpFetcher | None = None) -> FastAPI:
    """metadata_fetcher はテスト用の差し替え(通常はプロセス共有の fetcher を使う)。"""
    conn = db.open_db(settings.db_path)  # 起動時にマイグレーションを当てる
    linking.configure(settings.metadata)  # 日本語での表示の方針。下の反映で、入っている英語の値も日本語になる
    _sync_works(conn)  # 作品への割り当てを取り直す(作品の機能が入る前の DB は、ここで初めて作品になる)
    try:
        search.prepare(conn)  # 検索の索引が無ければ作る(検索の機能が入る前の DB・SQLite を替えた後)
    except Exception:  # noqa: BLE001 (派生データの更新で起動を止めない。一覧の取得時にもう一度試す)
        log.exception("検索の索引を用意できませんでした")
    conn.close()
    cache = ExtractionCache(settings.cache_dir, settings.cache_limit_bytes)
    memory = MemoryCache(settings.memory_cache_limit_bytes)
    library = Library(settings, cache, memory)
    books = Books(settings)
    thumbs = Thumbnails(library, ExtractionCache(settings.thumbs_dir, settings.thumb_cache_limit_bytes))
    # ジョブ(スキャン・補完・表紙など)は 1 本ずつ、このロックを持って動く。整理処理の実行・元に戻す・受け入れの承認も
    # 同じロックで、ジョブの実行中は 409 にする(蔵書・DB を同時に触らない。wiki/concepts/data-jobs.md)
    scan_lock = threading.Lock()
    runner = JobRunner(settings.db_path, scan_lock)
    enabled_sources = tuple(n for n in linking.SEARCH_ORDER if n in settings.metadata.sources)

    def metadata_service(c: sqlite3.Connection) -> MetadataService:
        return MetadataService(settings.metadata, c, fetcher=metadata_fetcher)

    def follow_service(c: sqlite3.Connection) -> MetadataService:
        """続刊の確認用。保存済みの応答の有効期限だけ短くし、取得元の関門(最小間隔・429 の停止)は補完と共有する
        (設定を変えたものを渡すと共有の fetcher が作り直されるので、元の設定の共有 fetcher を明示的に渡す)。"""
        meta = dataclasses.replace(settings.metadata, cache_ttl_days=settings.follow.response_ttl_days)
        return MetadataService(meta, c, fetcher=metadata_fetcher or shared_fetcher(settings.metadata))

    data_jobs = DataJobs(settings, library, thumbs, metadata_service, enabled_sources, _sync_works, follow_service)

    def background_loop(stop: threading.Event, interval: float, kind: str, fn) -> None:
        """裏の定期処理。1 回に 1 件、ほかのジョブが動いていないときだけ動き、動いたら履歴に残す。失敗しても止まらない。"""
        while not stop.wait(interval):
            try:
                runner.run_background(kind, kind_label(kind), fn)
            except Exception:  # noqa: BLE001 (裏の処理で本体を止めない)
                log.exception("%s に失敗しました", kind_label(kind))

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        stop = threading.Event()
        if settings.background_analysis:
            threading.Thread(target=background_loop, name="spinebox-analysis", daemon=True,
                             args=(stop, settings.background_analysis_interval, "background_analysis",
                                   data_jobs.background_analysis)).start()
        if settings.metadata.background and enabled_sources:
            threading.Thread(target=background_loop, name="spinebox-metadata", daemon=True,
                             args=(stop, settings.metadata.background_interval_seconds, "background_metadata",
                                   data_jobs.background_metadata)).start()
        if settings.follow.background and follows.SOURCE in enabled_sources:
            threading.Thread(target=background_loop, name="spinebox-follow", daemon=True,
                             args=(stop, settings.follow.background_interval_seconds, "background_follow",
                                   data_jobs.background_follow)).start()
        try:
            yield
        finally:
            stop.set()
            runner.stop()

    def submit_job(kind: str, params: dict | None = None, *, origin: str = "jobs") -> Job:
        """待ち行列に入れる。動かせない設定なら 409。"""
        params = params or {}
        try:
            fn = data_jobs.build(kind, params)
        except Unavailable as e:
            raise HTTPException(409, str(e)) from None
        return runner.submit(kind, kind_label(kind), fn, origin=origin, params=params)

    def run_job_now(kind: str, params: dict | None = None, *, fn=None, label: str | None = None) -> Job:
        """その場で動かして終わったジョブを返す(既存のボタンの同期 API 用)。ほかのジョブが実行中・待ち中なら 409。"""
        params = params or {}
        try:
            fn = fn or data_jobs.build(kind, params)
        except Unavailable as e:
            raise HTTPException(409, str(e)) from None
        try:
            return runner.run_now(kind, label or kind_label(kind), fn, origin="button", params=params)
        except Busy:
            raise HTTPException(409, "ほかのジョブを実行中です(データ管理の画面で確かめ・中止できます)") from None

    def job_state(kind: str) -> dict:
        return legacy_state(runner.latest(kind))

    app = FastAPI(title="SpineBox", lifespan=lifespan)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("Content-Security-Policy", APP_CSP)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        return response
    app.state.settings = settings
    app.state.cache = cache
    app.state.memory = memory
    app.state.scan_lock = scan_lock
    app.state.jobs = runner
    app.state.thumbs = thumbs
    commit = current_commit(REPO_ROOT)  # 起動時に一度だけ。古いプロセスが生き残っているかを見分けるため

    def get_conn() -> Iterator[sqlite3.Connection]:
        c = db.connect(settings.db_path)
        try:
            yield c
        finally:
            c.close()

    @app.exception_handler(VolumeNotFound)
    def _not_found(request: Request, exc: VolumeNotFound):
        return JSONResponse({"detail": "巻が見つかりません"}, status_code=404)

    @app.exception_handler(VolumeOffline)
    def _offline(request: Request, exc: VolumeOffline):
        return JSONResponse({"detail": "この巻の蔵書フォルダは今オフラインです", "offline": True}, status_code=409)

    @app.exception_handler(VolumeUnreadable)
    def _unreadable(request: Request, exc: VolumeUnreadable):
        return JSONResponse({"detail": f"この巻を読めません: {exc}"}, status_code=422)

    @app.exception_handler(CacheTooLarge)
    def _too_large(request: Request, exc: CacheTooLarge):
        return JSONResponse({"detail": f"この巻を読めません: {exc}"}, status_code=422)

    @app.exception_handler(OperationNotFound)
    def _op_not_found(request: Request, exc: OperationNotFound):
        return JSONResponse({"detail": "整理の操作が見つかりません"}, status_code=404)

    @app.exception_handler(RangeError)
    def _range_error(request: Request, exc: RangeError):
        return JSONResponse({"detail": str(exc)}, status_code=422)

    @app.exception_handler(RangeNotFound)
    def _range_not_found(request: Request, exc: RangeNotFound):
        return JSONResponse({"detail": str(exc)}, status_code=404)

    @app.exception_handler(OrganizeError)
    def _organize_error(request: Request, exc: OrganizeError):
        return JSONResponse({"detail": str(exc), "op": exc.op}, status_code=409)

    @app.get("/healthz")
    def healthz():
        """稼働確認(watchdog・CD の反映確認用)。DB に触れなければ 503 にして、異常を 200 で隠さない。"""
        try:
            c = db.connect(settings.db_path)
            try:
                c.execute("SELECT 1").fetchone()
            finally:
                c.close()
        except sqlite3.Error:
            return JSONResponse({"ok": False, "commit": commit, "detail": "DB に接続できません"}, status_code=503)
        return {"ok": True, "commit": commit}

    def _finished_or_raise(job: Job, what: str) -> None:
        if job.status == "failed":
            raise HTTPException(500, safe_text(f"{what}に失敗しました: {job.error}"))
        if job.status == "cancelled":
            raise HTTPException(409, f"{what}は中止されました")

    @app.post("/api/scan")
    def scan():
        """一覧の「スキャン」。データ管理の「ファイルのスキャンのみ」と同じジョブを、その場で動かして結果を返す。"""
        found: dict = {}

        def run(ctx):
            c = db.connect(settings.db_path)
            try:
                ctx.step("ファイルのスキャン")
                result = data_jobs.scan(ctx, c)
                _sync_works(c)  # 名前の解析だけ。内側のアーカイブは取り出さない
                ctx.check()
                found["result"] = result
                return data_jobs.scan_summary(result)
            finally:
                c.close()
        job = run_job_now("scan", fn=run)
        _finished_or_raise(job, "スキャン")
        result = found["result"]
        return {"volumes": result.volumes, "offline_roots": result.offline_roots,
                "issues": result.issues, "issues_total": result.issues_total}

    @app.get("/api/volumes")
    def list_volumes(conn: sqlite3.Connection = Depends(get_conn)):
        rows = conn.execute(
            """SELECT v.id, v.title, v.page_count, v.container_path, v.entry_prefix, v.inner_path, v.parent_id,
                      v.number_kind, v.number_start, v.number_end, v.number_evidence, v.analyzed_version,
                      v.format, pv.title AS parent_title, pv.entry_prefix AS parent_prefix, s.rel_path,
                      s.offline AS source_offline, r.online AS root_online, p.page, p.finished, p.position"""
            + LISTED_VOLUMES_JOIN + " LEFT JOIN reading_progress p ON p.volume_id = v.id " + LISTED_VOLUMES_WHERE
        ).fetchall()
        rows.sort(key=_list_sort_key)
        findings = compute_findings(conn)
        duplicates = candidate_counts(conn)
        work_ids = works.assignments(conn)
        marked = bookmarks.volume_ids(conn)
        return [{
            "id": r["id"],
            "title": r["title"],
            "source_name": Path(r["rel_path"]).name if r["rel_path"] else r["title"],
            "format": _format(r),
            "page_count": r["page_count"],
            "offline": is_offline(r),
            "progress": _progress(r),
            "parent_id": r["parent_id"],
            "parent_title": r["parent_title"],
            "volume_number": _number(r),
            "findings": findings.get(r["id"], []),
            "duplicate_count": duplicates.get(r["id"], 0),
            "work_id": work_ids.get(r["id"]),
            "bookmarked": r["id"] in marked,
            "can_analyze": is_analyzable(r) and r["analyzed_version"] is None and not is_offline(r),
        } for r in rows]

    def _get_progress(conn, volume_id: int):
        return _progress(conn.execute("SELECT page, finished, position FROM reading_progress WHERE volume_id = ?",
                                      (volume_id,)).fetchone())

    def _get_novel(conn, volume_id: int) -> dict:
        with books.open(conn, volume_id) as book:
            row = get_volume_row(conn, volume_id)
            return {
                "id": volume_id,
                "title": row["title"],
                "format": book.format,
                "page_count": len(book.lengths),
                "pages": [],
                "sections": [{"index": i, "length": n} for i, n in enumerate(book.lengths)],
                "encoding": book.encoding,
                "progress": _get_progress(conn, volume_id),
                "bookmarked": bookmarks.is_volume_bookmarked(conn, volume_id),
                "parent_id": row["parent_id"],
                "volume_number": _number(row),
                "findings": [],
            }

    @app.get("/api/volumes/{volume_id}")
    def get_volume(volume_id: int, conn: sqlite3.Connection = Depends(get_conn)):
        if get_volume_row(conn, volume_id)["format"] is not None:
            return _get_novel(conn, volume_id)
        with library.open(conn, volume_id) as vol:
            # 内側アーカイブの巻は、開いたついでに中の巻へ分解する(取り出しは済んでいるので追加の展開は無い)
            if library.register_analysis(conn, vol):
                _sync_works(conn)  # 中の巻(子)が現れた
            row = get_volume_row(conn, volume_id)
            return {
                "id": volume_id,
                "title": row["title"],
                "format": "images",
                "page_count": len(vol.page_names),
                "pages": [{"index": i, "name": n.rsplit("/", 1)[-1]} for i, n in enumerate(vol.page_names)],
                "progress": _get_progress(conn, volume_id),
                "bookmarked": bookmarks.is_volume_bookmarked(conn, volume_id),
                "parent_id": row["parent_id"],
                "volume_number": _number(row),
                "findings": compute_findings(conn).get(volume_id, []),
            }

    @app.post("/api/volumes/{volume_id}/analyze")
    def analyze_volume(volume_id: int, conn: sqlite3.Connection = Depends(get_conn)):
        """明示操作の解析。解析済みでも開き直す。分解の対象でない巻(フォルダ・子の巻)は analyzed: false。"""
        analyzed = library.analyze(conn, volume_id, force=True)
        if analyzed:
            _sync_works(conn)
        children = conn.execute(
            """SELECT id, title, page_count, number_kind, number_start, number_end, number_evidence, inner_path,
                      entry_prefix FROM volumes WHERE parent_id = ? AND missing = 0""", (volume_id,)).fetchall()
        children = sorted(children, key=lambda r: (natural_key(_chain_text(r["inner_path"])),
                                                   natural_key(r["entry_prefix"])))
        return {
            "id": volume_id,
            "analyzed": analyzed,
            "children": [{"id": r["id"], "title": r["title"], "page_count": r["page_count"],
                          "volume_number": _number(r)} for r in children],
            "findings": compute_findings(conn).get(volume_id, []),
        }

    # --- 別スキャンの重複候補(#38) ----------------------------------------------------

    def _start_legacy(kind: str, wait: bool, params: dict | None = None):
        """既存の「裏で動かす」ボタンの API。wait=true はその場で動かす(ほかのジョブが動いていれば 409)。
        既定は待ち行列に入れて 202(同じジョブが待ち・実行中なら、それの状態を返す)。"""
        if wait:
            return legacy_state(run_job_now(kind, params))
        return JSONResponse(legacy_state(submit_job(kind, params, origin="button")), status_code=202)

    @app.post("/api/duplicates/scan")
    def start_duplicate_scan(wait: bool = False):
        """見た目の指紋を作って重複候補を出す明示操作(データ管理の「重複を調べる」と同じジョブ)。
        スキャンでは行わない(内側アーカイブを取り出すため)。進み具合は GET /api/duplicates の scan。"""
        return _start_legacy("duplicates", wait)

    @app.get("/api/duplicates")
    def list_duplicates(status: str = "open", conn: sqlite3.Connection = Depends(get_conn)):
        """重複候補。status=open は未却下、dismissed は却下済み。どちらも一覧に見えている巻どうしの組だけ。"""
        if status not in ("open", "dismissed"):
            raise HTTPException(422, "status は open か dismissed")
        cond = "dismissed_at IS NULL" if status == "open" else "dismissed_at IS NOT NULL"
        rows = conn.execute(f"SELECT * FROM duplicate_candidates WHERE {cond} ORDER BY matched_pages DESC, id").fetchall()
        ids = sorted({i for r in rows for i in (r["volume_a"], r["volume_b"])})
        cards: dict[int, dict] = {}
        for chunk_start in range(0, len(ids), 500):
            chunk = ids[chunk_start:chunk_start + 500]
            marks = ",".join("?" * len(chunk))
            for v in conn.execute(
                    """SELECT v.id, v.title, v.page_count, v.number_kind, v.number_start, v.number_end,
                              v.number_evidence, pv.title AS parent_title, s.rel_path, s.offline AS source_offline,
                              r.online AS root_online, p.page, p.finished"""
                    + LISTED_VOLUMES_JOIN + " LEFT JOIN reading_progress p ON p.volume_id = v.id "
                    + LISTED_VOLUMES_WHERE + f" AND v.id IN ({marks})", chunk):
                cards[v["id"]] = {
                    "id": v["id"], "title": v["title"],
                    "source_name": Path(v["rel_path"]).name if v["rel_path"] else v["title"],
                    "parent_title": v["parent_title"], "page_count": v["page_count"], "offline": is_offline(v),
                    "volume_number": _number(v), "progress": _progress(v),
                }
        candidates = [{
            "id": r["id"], "rule": r["rule"], "matched_pages": r["matched_pages"],
            "compared_pages": r["compared_pages"], "evidence": json.loads(r["evidence"]),
            "dismissed": r["dismissed_at"] is not None,
            "a": cards[r["volume_a"]], "b": cards[r["volume_b"]],
        } for r in rows if r["volume_a"] in cards and r["volume_b"] in cards]
        return {"scan": job_state("duplicates"), "candidates": candidates}

    def _set_dismissed(candidate_id: int, conn, *, restore: bool):
        try:
            dismiss(conn, candidate_id, restore=restore)
        except CandidateNotFound:
            raise HTTPException(404, "候補が見つかりません") from None
        return {"id": candidate_id, "dismissed": not restore}

    @app.post("/api/duplicates/{candidate_id}/dismiss")
    def dismiss_duplicate(candidate_id: int, conn: sqlite3.Connection = Depends(get_conn)):
        """「重複ではない」と却下する。蔵書には触れない。再計算しても復活しない。"""
        return _set_dismissed(candidate_id, conn, restore=False)

    @app.post("/api/duplicates/{candidate_id}/restore")
    def restore_duplicate(candidate_id: int, conn: sqlite3.Connection = Depends(get_conn)):
        return _set_dismissed(candidate_id, conn, restore=True)

    # --- 作品(#43) ------------------------------------------------------------------------

    @app.exception_handler(works.WorkNotFound)
    def _work_not_found(request: Request, exc: works.WorkNotFound):
        return JSONResponse({"detail": "作品が見つかりません"}, status_code=404)

    @app.exception_handler(works.ProposalNotFound)
    def _proposal_not_found(request: Request, exc: works.ProposalNotFound):
        return JSONResponse({"detail": "提案が見つかりません"}, status_code=404)

    @app.exception_handler(works.Conflict)
    def _work_conflict(request: Request, exc: works.Conflict):
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @app.exception_handler(works.InvalidValue)
    def _work_invalid(request: Request, exc: works.InvalidValue):
        return JSONResponse({"detail": str(exc)}, status_code=422)

    @app.exception_handler(search.InvalidFilter)
    def _search_invalid(request: Request, exc: search.InvalidFilter):
        return JSONResponse({"detail": str(exc)}, status_code=422)

    def work_detail(conn: sqlite3.Connection, work_id: int) -> dict:
        detail = works.get_work(conn, work_id, enabled_sources)
        follows.resolve_merged(conn)  # 統合で吸収された作品のフォローは吸収先へ
        detail["follow"] = follows.work_follow(conn, work_id)
        return detail

    @app.get("/api/works")
    def list_works(q: str | None = None, author: str | None = None, kind: str | None = None,
                   genre: str | None = None, tag: str | None = None, status: str | None = None,
                   conn: sqlite3.Connection = Depends(get_conn)):
        """作品の一覧(見えている巻を持つ作品)。unassigned は作品に割り当てられていない巻の数。

        q は全文検索(作品名・別名・著者・巻の名前・その他の項目)、author / kind / genre / tag / status は絞り込み。
        """
        return works.list_works(conn, q, {"author": author, "kind": kind, "genre": genre, "tag": tag,
                                          "status": status})

    @app.post("/api/works/refresh")
    def refresh_works():
        """作品への割り当てと統合の提案を取り直す(名前の解析と DB の更新だけ)。手動の決定は変えない。"""
        job = run_job_now("works_refresh")
        _finished_or_raise(job, "名寄せの計算")
        return job.summary

    @app.get("/api/works/proposals")
    def list_work_proposals(status: str = "open", conn: sqlite3.Connection = Depends(get_conn)):
        """統合の提案。status は open / dismissed / applied。"""
        return works.list_proposals(conn, status)

    @app.post("/api/works/proposals/{proposal_id}/{action}")
    def decide_work_proposal(proposal_id: int, action: str, conn: sqlite3.Connection = Depends(get_conn)):
        """action: approve(統合する)/ dismiss(別の作品として却下)/ restore(却下を取り消す)。"""
        res = works.decide_proposal(conn, proposal_id, action)
        if action == "approve":
            _sync_works(conn)  # 統合で増えた巻へ、残った作品の連携から書誌を入れる
        return res

    @app.post("/api/works/assign")
    def assign_work_volumes(body: AssignIn, conn: sqlite3.Connection = Depends(get_conn)):
        """巻を別の作品へ付け替える(work_id が null なら新しい作品に分ける)。手動の決定として残る。"""
        work_id = works.assign_volumes(conn, body.volume_ids, body.work_id, body.title)
        _sync_works(conn)
        return {"work_id": work_id}

    @app.get("/api/works/metadata")
    def metadata_status():
        """「情報を補完」(公開 DB の情報を全作品について取りに行く明示操作)の進み具合と、有効な取得元。"""
        return {"state": job_state("metadata"), "sources": list(enabled_sources),
                "background": settings.metadata.background}

    @app.post("/api/works/metadata/fetch")
    def start_metadata_fetch(wait: bool = False, all: bool = False):
        """公開 DB の情報を、取得が必要な作品(all=true なら全作品)について取りに行く明示操作
        (データ管理の「作品情報のみ補完」と同じジョブ)。進み具合は GET /api/works/metadata。
        取得元が落ちていても作品ごとに失敗を記録して続ける。"""
        return _start_legacy("metadata", wait, {"all": True} if all else {})

    # --- フォロー(#87)。フォローした作品の続刊・欠けを公開 DB で調べて知らせる(本の入手はしない) ---

    @app.get("/api/follows")
    def list_follows(conn: sqlite3.Connection = Depends(get_conn)):
        """フォロー管理: フォロー中の作品と最後に調べた日時・結果、続刊の確認の進み具合と設定。"""
        return {"follows": follows.list_follows(conn), "state": job_state("follow_check"),
                "available": follows.SOURCE in enabled_sources, "background": settings.follow.background,
                "interval_hours": settings.follow.interval_hours}

    @app.get("/api/follows/releases")
    def list_follow_releases(seen: bool = False, conn: sqlite3.Connection = Depends(get_conn)):
        """サイドバーの「フォロー」: 続刊・欠けのある作品(新しい発売日順)。seen=true なら確認済みの巻も含める。"""
        return {"works": follows.list_releases(conn, include_seen=seen)}

    @app.post("/api/follows/releases/seen")
    def mark_follow_releases_seen(body: SeenIn, conn: sqlite3.Connection = Depends(get_conn)):
        """見つけた巻を「確認済み」にする。再確認でも再び出さない(新しく見つかった巻だけ出る)。"""
        return {"marked": follows.mark_seen(conn, body.work_id, body.volumes)}

    @app.post("/api/follows/check")
    def start_follow_check(body: FollowCheckIn | None = None, wait: bool = False):
        """明示操作「続刊を調べる」(データ管理の「フォロー中の作品の続刊を調べる」と同じジョブ)。既定は待ち行列に入れて 202
        (進み具合は GET /api/follows の state)。wait=true はその場で動かし、ほかのジョブが動いていれば 409。
        取得元が落ちていても、作品ごとに失敗を記録して続ける。"""
        params = {} if body is None or body.work_ids is None else {"work_ids": sorted(set(body.work_ids))}
        return _start_legacy("follow_check", wait, params)

    @app.put("/api/works/{work_id}/follow")
    def follow_work(work_id: int, conn: sqlite3.Connection = Depends(get_conn)):
        follows.follow(conn, work_id)
        return follows.work_follow(conn, work_id)

    @app.delete("/api/works/{work_id}/follow")
    def unfollow_work(work_id: int, conn: sqlite3.Connection = Depends(get_conn)):
        follows.unfollow(conn, work_id)
        return follows.work_follow(conn, work_id)

    @app.get("/api/works/{work_id}")
    def get_work(work_id: int, conn: sqlite3.Connection = Depends(get_conn)):
        return work_detail(conn, work_id)

    @app.patch("/api/works/{work_id}")
    def edit_work(work_id: int, body: WorkEditIn, conn: sqlite3.Connection = Depends(get_conn)):
        """作品の項目を手で直す。手動の項目は再計算でも公開 DB の取得でも上書きしない。"""
        sent = body.model_fields_set & set(works.EDITABLE_FIELDS)
        if not sent:
            raise HTTPException(422, "直す項目を指定してください(" + "・".join(works.EDITABLE_FIELDS) + ")")
        if "title" in sent and body.title is None:
            raise HTTPException(422, "作品名は null にできません")
        works.edit_fields(conn, work_id, {name: getattr(body, name) for name in works.EDITABLE_FIELDS if name in sent})
        _sync_works(conn)  # 直した作品名が別の作品の名前と一致したら、統合の提案が出る
        return work_detail(conn, work_id)

    @app.post("/api/works/{work_id}/fields/{field}/reset")
    def reset_work_field(work_id: int, field: str, conn: sqlite3.Connection = Depends(get_conn)):
        """手動の項目を自動に戻す(直す前の値、無ければ公開 DB の値)。"""
        works.reset_field(conn, work_id, field)
        linking.apply_work(conn, work_id)
        _sync_works(conn)
        # 手動が外れて止まっていた自動統合が成り立ち、この作品が吸収されることがある。吸収先を返す(404 にしない)
        return work_detail(conn, works.root_of(conn, work_id) or work_id)

    @app.post("/api/works/{work_id}/metadata/fetch")
    def fetch_work_metadata(work_id: int, conn: sqlite3.Connection = Depends(get_conn)):
        """この作品の公開 DB の情報を取りに行く(数秒かかる)。取得元の失敗は作品の連携に理由つきで記録し、
        応答は 200 で返す。別名の一致で他の作品に統合されたら、吸収先の詳細を返す。"""
        if not enabled_sources:
            raise HTTPException(409, "公開 DB の取得元が設定で無効になっています")
        if works.root_of(conn, work_id) != work_id:
            raise works.WorkNotFound(work_id)
        job = run_job_now("work_metadata", {"work_id": work_id},
                          label=f"{kind_label('work_metadata')}: {work_title(conn, work_id)}")
        if job.status == "failed" and job.error and job.error.startswith("WorkNotFound"):
            raise works.WorkNotFound(work_id)
        _finished_or_raise(job, "公開 DB の取得")
        return work_detail(conn, works.root_of(conn, work_id) or work_id)

    @app.post("/api/works/{work_id}/links/{source}/choose")
    def choose_work_link(work_id: int, source: str, body: ChooseIn, conn: sqlite3.Connection = Depends(get_conn)):
        """確認待ちの候補を選ぶ(source_id が null なら「該当なし」)。選んだ結果は手動の決定になる。"""
        linking.choose(conn, work_id, source, body.source_id)
        _sync_works(conn)
        return work_detail(conn, works.root_of(conn, work_id) or work_id)

    @app.post("/api/works/{work_id}/links/{source}/reset")
    def reset_work_link(work_id: int, source: str, conn: sqlite3.Connection = Depends(get_conn)):
        """選んだ連携の手動を解除して、自動の規則で決め直す。"""
        linking.reset_link(conn, work_id, source)
        _sync_works(conn)
        return work_detail(conn, works.root_of(conn, work_id) or work_id)

    def _volume_meta_json(conn: sqlite3.Connection, volume_id: int) -> dict:
        return {"volume_id": volume_id, "metadata": {
            r["field"]: {"value": r["value"], "source": r["source"], "manual": bool(r["manual"]),
                         "confidence": r["confidence"]}
            for r in conn.execute("SELECT * FROM volume_fields WHERE volume_id = ?", (volume_id,))}}

    @app.patch("/api/volumes/{volume_id}/metadata")
    def edit_volume_metadata(volume_id: int, body: VolumeMetaIn, conn: sqlite3.Connection = Depends(get_conn)):
        """巻の書誌(出版社・レーベル・発売日・ISBN)を手で直す。再取得・再計算で上書きしない。"""
        sent = body.model_fields_set & set(works.VOLUME_EDITABLE_FIELDS)
        if not sent:
            raise HTTPException(422, "直す項目を指定してください(" + "・".join(works.VOLUME_EDITABLE_FIELDS) + ")")
        works.edit_volume_fields(conn, volume_id, {name: getattr(body, name) for name in sent})
        return _volume_meta_json(conn, volume_id)

    @app.post("/api/volumes/{volume_id}/metadata/{field}/reset")
    def reset_volume_metadata(volume_id: int, field: str, conn: sqlite3.Connection = Depends(get_conn)):
        """巻の手動の項目を自動に戻す(直す前の値、無ければ公開 DB の値)。"""
        works.reset_volume_field(conn, volume_id, field)
        work_id = works.assignments(conn).get(volume_id)
        if work_id is not None:
            linking.apply_work(conn, work_id)
        return _volume_meta_json(conn, volume_id)

    @app.post("/api/works/{work_id}/aliases")
    def add_work_alias(work_id: int, body: AliasIn, conn: sqlite3.Connection = Depends(get_conn)):
        works.add_alias(conn, work_id, body.alias)
        _sync_works(conn)
        # 別名の一致で自動統合され、この作品が吸収されることがある。吸収先を返す(404 にしない)
        return work_detail(conn, works.root_of(conn, work_id) or work_id)

    @app.delete("/api/works/{work_id}/aliases/{alias_id}")
    def remove_work_alias(work_id: int, alias_id: int, conn: sqlite3.Connection = Depends(get_conn)):
        works.dismiss_alias(conn, work_id, alias_id)
        return work_detail(conn, work_id)

    @app.post("/api/works/{work_id}/merge")
    def merge_work(work_id: int, body: MergeIn, conn: sqlite3.Connection = Depends(get_conn)):
        """work_id に other_id を統合する(手動)。"""
        works.merge_manual(conn, work_id, body.other_id)
        _sync_works(conn)
        return work_detail(conn, work_id)

    # --- ブックマーク(#86) ---

    @app.exception_handler(bookmarks.InvalidSort)
    def _bookmark_sort_invalid(request: Request, exc: bookmarks.InvalidSort):
        return JSONResponse({"detail": str(exc)}, status_code=422)

    @app.get("/api/bookmarks")
    def list_bookmarks(sort: str = "added", conn: sqlite3.Connection = Depends(get_conn)):
        """印を付けた作品・巻。sort = added(付けた新しい順)/ title(作品名の順)。"""
        return bookmarks.list_bookmarks(conn, sort)

    @app.put("/api/bookmarks/works/{work_id}")
    def add_work_bookmark(work_id: int, conn: sqlite3.Connection = Depends(get_conn)):
        return bookmarks.set_work(conn, work_id, True)

    @app.delete("/api/bookmarks/works/{work_id}")
    def remove_work_bookmark(work_id: int, conn: sqlite3.Connection = Depends(get_conn)):
        return bookmarks.set_work(conn, work_id, False)

    @app.put("/api/bookmarks/volumes/{volume_id}")
    def add_volume_bookmark(volume_id: int, conn: sqlite3.Connection = Depends(get_conn)):
        return bookmarks.set_volume(conn, volume_id, True)

    @app.delete("/api/bookmarks/volumes/{volume_id}")
    def remove_volume_bookmark(volume_id: int, conn: sqlite3.Connection = Depends(get_conn)):
        return bookmarks.set_volume(conn, volume_id, False)

    @app.get("/api/volumes/{volume_id}/pages/{index}")
    def get_page(volume_id: int, index: int, request: Request, conn: sqlite3.Connection = Depends(get_conn)):
        with library.open(conn, volume_id) as vol:
            if not 0 <= index < len(vol.page_names):
                raise HTTPException(404, "ページがありません")
            name = vol.page_names[index]
            etag = '"' + hashlib.sha256(f"{volume_id}\0{vol.version}\0{index}\0{name}".encode(
                "utf-8", "surrogatepass")).hexdigest()[:32] + '"'
            headers = {"ETag": etag, "Cache-Control": PAGE_CACHE_CONTROL}
            if request.headers.get("if-none-match") == etag:
                return Response(status_code=304, headers=headers)
            data = vol.read_page(index)
        return Response(data, media_type=media_type(name), headers=headers)

    # --- 表紙のサムネイル(#75) ------------------------------------------------------

    @app.get("/api/volumes/{volume_id}/thumbnail")
    def get_thumbnail(volume_id: int, request: Request, conn: sqlite3.Connection = Depends(get_conn)):
        """巻の表紙(1 ページ目の縮小 JPEG)。一覧から巻の数だけ呼ばれるので、取り出し・7-Zip が要る巻は作らずに
        404(pending: true)を返す(巻を開いた後か、POST /api/thumbnails/build で作る)。"""
        no_store = {"Cache-Control": "no-store"}
        try:
            key, data = thumbs.get(conn, volume_id)
        except NeedsExtraction:
            return JSONResponse({"detail": "表紙はまだ作っていません", "pending": True}, status_code=404,
                                headers=no_store)
        except ThumbnailUnavailable as e:
            return JSONResponse({"detail": str(e), "pending": False}, status_code=404, headers=no_store)
        headers = {"ETag": f'"{key[:32]}"', "Cache-Control": "private, no-cache"}
        if request.headers.get("if-none-match") == headers["ETag"]:
            return Response(status_code=304, headers=headers)
        return Response(data, media_type="image/jpeg", headers=headers)

    @app.get("/api/thumbnails")
    def thumbnail_status():
        return job_state("thumbnails")

    @app.post("/api/thumbnails/build")
    def start_thumbnail_build(wait: bool = False):
        """明示操作: 作品の表紙を、内側アーカイブを取り出してでも作る(スキャンでは作らない。
        データ管理の「表紙のみ補完」と同じジョブ)。進み具合は GET /api/thumbnails。"""
        return _start_legacy("thumbnails", wait)

    @app.get("/api/volumes/{volume_id}/sections/{index}")
    def get_section(volume_id: int, index: int, conn: sqlite3.Connection = Depends(get_conn)):
        """小説の区切り 1 つの表示用 HTML(EPUB は無害化済み、テキストはエスケープして組み立てたもの)。"""
        with books.open(conn, volume_id) as book:
            if not 0 <= index < len(book.lengths):
                raise HTTPException(404, "区切りがありません")
            return JSONResponse({"index": index, "html": book.section_html(index)},
                                headers={"Cache-Control": "private, no-cache"})

    @app.get("/api/volumes/{volume_id}/resources/{index}")
    def get_resource(volume_id: int, index: int, conn: sqlite3.Connection = Depends(get_conn)):
        """EPUB の中の画像(manifest の画像だけ。SVG は返さない)。"""
        with books.open(conn, volume_id) as book:
            try:
                data, media = book.resource(index)
            except IndexError:
                raise HTTPException(404, "資源がありません") from None
        return Response(data, media_type=media, headers=RESOURCE_HEADERS)

    @app.put("/api/volumes/{volume_id}/progress")
    def put_progress(volume_id: int, body: ProgressIn, conn: sqlite3.Connection = Depends(get_conn)):
        row = get_volume_row(conn, volume_id)
        if is_offline(row):
            raise VolumeOffline(volume_id)
        if row["page_count"] is not None and body.page >= max(row["page_count"], 1):
            raise HTTPException(422, f"page は 0〜{max(row['page_count'] - 1, 0)} で指定してください")
        # 割合は小説だけが持つ(画像の巻はページで位置が決まるので捨てる)
        position = body.position if row["format"] is not None else None
        with db.transaction(conn) as c:
            c.execute("""INSERT INTO reading_progress (volume_id, page, finished, position, updated_at)
                         VALUES (?, ?, ?, ?, ?)
                         ON CONFLICT (volume_id) DO UPDATE SET page = excluded.page, finished = excluded.finished,
                         position = excluded.position, updated_at = excluded.updated_at""",
                      (volume_id, body.page, int(body.finished), position, db.now()))
        return _get_progress(conn, volume_id)

    # --- 整理処理(CBZ 化・テンプレートでの改名)。蔵書に書き込む唯一の経路。実行と元に戻すはスキャンと同じロックで直列化する ---

    def organizer(conn) -> Organizer:
        return Organizer(conn, settings, library)

    @app.post("/api/organize/plan")
    def organize_plan(body: OrganizePlanIn, conn: sqlite3.Connection = Depends(get_conn)):
        if (body.volume_id is None) == (body.source_id is None):
            raise HTTPException(422, "volume_id か source_id のどちらか一方を指定してください")
        return organizer(conn).plan(volume_id=body.volume_id, source_id=body.source_id, naming=body.naming)

    @app.post("/api/organize/rename-plan")
    def organize_rename_plan(body: RenamePlanIn, conn: sqlite3.Connection = Depends(get_conn)):
        return organizer(conn).plan_rename(body.work_id, exclude_volume_ids=body.exclude_volume_ids)

    # 範囲指定の整理(#84。データ管理の「整理」節)。選択肢・パスの例・フォルダ一覧・計画は読むだけ。
    # 実行と元に戻すは他の整理と同じ /api/organize/{op_id}/execute・undo(ジョブの自動実行には入れない)

    @app.get("/api/organize/range/options")
    def organize_range_options(conn: sqlite3.Connection = Depends(get_conn)):
        file_template, folder_template = NameTemplate(settings.name_template).range_templates()
        roots = [{"id": r["id"], "path": r["path"], "online": bool(r["online"])} for r in conn.execute(
            "SELECT id, path, online FROM library_roots WHERE enabled = 1 AND inbox = 0 ORDER BY id")]
        return {"roots": roots, "defaults": {"file_template": file_template, "folder_template": folder_template},
                "presets": [{"label": l, "file_template": f, "folder_template": d} for l, f, d in PRESETS],
                "parameters": [{"token": t, "label": l} for t, l in PARAMETERS]}

    @app.post("/api/organize/range/example")
    def organize_range_example(body: RangeExampleIn):
        """パスの例。テンプレートが不正なら error に理由を入れて返す(入力中に毎回呼ぶので 422 にしない)。"""
        try:
            return {"error": None, "examples": range_examples(body.file_template, body.folder_template, body.path)}
        except TemplateError as e:
            return {"error": str(e), "examples": []}

    @app.get("/api/organize/range/folders")
    def organize_range_folders(root_id: int, path: str = "", conn: sqlite3.Connection = Depends(get_conn)):
        return organizer(conn).range_folders(root_id, path)

    @app.post("/api/organize/range-plan")
    def organize_range_plan(body: RangePlanIn, conn: sqlite3.Connection = Depends(get_conn)):
        try:
            return organizer(conn).plan_range(body.root_id, body.path, file_template=body.file_template,
                                              folder_template=body.folder_template,
                                              exclude_volume_ids=body.exclude_volume_ids)
        except TemplateError as e:
            raise HTTPException(422, str(e)) from e

    @app.get("/api/organize")
    def organize_history(conn: sqlite3.Connection = Depends(get_conn)):
        return organizer(conn).history()

    @app.get("/api/organize/{op_id}")
    def organize_get(op_id: str, conn: sqlite3.Connection = Depends(get_conn)):
        return organizer(conn).get(op_id)

    def _exclusive(fn):
        if runner.busy() or not scan_lock.acquire(blocking=False):
            raise HTTPException(409, "ジョブか整理の処理中です(データ管理の画面で確かめられます)")
        try:
            return fn()
        finally:
            scan_lock.release()

    @app.post("/api/organize/{op_id}/execute")
    def organize_execute(op_id: str, conn: sqlite3.Connection = Depends(get_conn)):
        return _exclusive(lambda: organizer(conn).execute(op_id))

    @app.post("/api/organize/{op_id}/undo")
    def organize_undo(op_id: str, conn: sqlite3.Connection = Depends(get_conn)):
        res = _exclusive(lambda: organizer(conn).undo(op_id))
        if res.get("kind") == "intake":
            _sync_works(conn)  # 書庫から外れた巻の分、作品の割り当てを取り直す
        return res

    # --- 受け入れ(#66)。受け入れフォルダ → 同期で検出 → 整理案のプレビュー → 承認で書庫へ。
    #     同期とプレビューは何も動かさない。動かすのは承認(approve)だけで、履歴から元に戻せる ---

    def intake(conn) -> Intake:
        return Intake(organizer(conn), sync_works=lambda: _sync_works(conn))

    @app.get("/api/inbox")
    def inbox_list(conn: sqlite3.Connection = Depends(get_conn)):
        """受け入れフォルダの設定と、いま記録されている受け入れ待ちの本の整理案(ファイルは開かない)。"""
        return intake(conn).list_items()

    @app.post("/api/inbox/sync")
    def inbox_sync(conn: sqlite3.Connection = Depends(get_conn)):
        """受け入れフォルダを走査して新しいものを検出し、整理案を返す。読み取りだけ(蔵書にも受け入れフォルダにも書かない)。
        データ管理の「受け入れフォルダを同期」と同じジョブを、その場で動かす。受け入れフォルダが無い設定でも空の結果を返す。"""
        found: dict = {}

        def run(ctx):
            c = db.connect(settings.db_path)
            try:
                ctx.step("受け入れフォルダの同期")
                found["result"] = result = data_jobs.scan(ctx, c, inbox=True)
                ctx.check()
                return data_jobs.scan_summary(result)
            finally:
                c.close()
        job = run_job_now("inbox_sync", fn=run)
        _finished_or_raise(job, "受け入れフォルダの同期")
        result = found["result"]
        return {**intake(conn).list_items(), "volumes": result.volumes, "offline_roots": result.offline_roots}

    @app.post("/api/inbox/preview")
    def inbox_preview(body: InboxItemsIn, conn: sqlite3.Connection = Depends(get_conn)):
        """選んだ内容(作品・番号・取り込まない)での整理案を作り直す。目次を読み、何も書かない。"""
        return intake(conn).preview([d.to_decision() for d in body.items])

    @app.post("/api/inbox/approve")
    def inbox_approve(body: InboxItemsIn, conn: sqlite3.Connection = Depends(get_conn)):
        """選んだものだけを書庫へ入れる(1 件ずつ。失敗した件は巻き戻し済みで、受け入れフォルダに残る)。"""
        decisions = [d.to_decision() for d in body.items]
        return _exclusive(lambda: intake(conn).approve(decisions))

    # --- データ管理のジョブ(#83) ------------------------------------------------------------

    @app.get("/api/jobs")
    def list_jobs(limit: int = 50):
        """実行中のジョブ・待ち行列・履歴(新しい順)。"""
        return runner.snapshot(max(1, min(limit, 500)))

    @app.get("/api/jobs/catalog")
    def job_catalog(conn: sqlite3.Connection = Depends(get_conn)):
        """画面に並べるジョブと、それぞれの対象の件数。"""
        return {"kinds": data_jobs.catalog(conn)}

    @app.post("/api/jobs")
    def start_job(body: JobIn):
        """ジョブを待ち行列に入れる(202)。同じジョブが待ち・実行中なら新しく足さずにそれを返す。"""
        if body.kind not in KIND_BY_NAME:
            raise HTTPException(422, f"知らないジョブです: {body.kind}")
        allowed = {"metadata": {"all": bool}, "follow_check": {"work_ids": list}}.get(body.kind, {})
        unknown = set(body.params) - set(allowed)
        if unknown:
            raise HTTPException(422, "知らない指定です: " + "・".join(sorted(unknown)))
        if any(not isinstance(v, allowed[k]) for k, v in body.params.items()):
            raise HTTPException(422, "指定の形が違います")
        params = {k: (sorted(set(v)) if isinstance(v, list) else v) for k, v in body.params.items() if v is not False}
        return JSONResponse(submit_job(body.kind, params).to_json(), status_code=202)

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: int):
        job = runner.get(job_id)
        if job is None:
            raise HTTPException(404, "ジョブが見つかりません")
        return job.to_json()

    @app.post("/api/jobs/{job_id}/cancel")
    def cancel_job(job_id: int):
        """待ちなら取り下げ、実行中なら区切りのよいところで止める。終わったジョブ・裏の定期処理は 409。"""
        try:
            return runner.cancel(job_id).to_json()
        except JobNotFound:
            raise HTTPException(404, "ジョブが見つかりません") from None
        except NotCancellable:
            raise HTTPException(409, "このジョブは中止できません(終わっているか、裏の定期処理です)") from None

    if frontend_dist is not None and (frontend_dist / "index.html").is_file():
        _mount_frontend(app, frontend_dist)
    return app


def _mount_frontend(app: FastAPI, dist: Path) -> None:
    dist = dist.resolve()

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str):
        if full_path == "api" or full_path.startswith("api/"):
            raise HTTPException(404, "Not Found")
        if full_path:
            candidate = (dist / full_path).resolve()
            if candidate.is_relative_to(dist) and candidate.is_file():
                return FileResponse(candidate)
        return FileResponse(dist / "index.html")
