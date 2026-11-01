"""FastAPI アプリ。API の契約はフロント(frontend/)と共有している(wiki/components/backend.md)。

`frontend/dist` があれば `/` で静的配信し、API 以外の未知のパスは index.html を返す(SPA の fallback)。
"""
from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import threading
from dataclasses import asdict
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Iterator

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field, StrictBool, StrictInt, StrictStr

from . import db, linking, works
from .analysis import compute_findings
from .duplicates import CandidateNotFound, DuplicateScanner, candidate_counts, dismiss
from .buildinfo import current_commit
from .cache import CacheTooLarge, ExtractionCache
from .config import Settings
from .library import (Library, VolumeNotFound, VolumeOffline, VolumeUnreadable, get_volume_row, is_analyzable,
                      is_offline)
from .metadata import MetadataService
from .metadata.http import HttpFetcher
from .organize.organizer import OperationNotFound, OrganizeError, Organizer
from .pages import media_type, natural_key
from .scanner import LISTED_VOLUMES_JOIN, LISTED_VOLUMES_WHERE, Scanner, safe_text

log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FRONTEND_DIST = REPO_ROOT / "frontend" / "dist"
PAGE_CACHE_CONTROL = "private, max-age=3600"


class OrganizePlanIn(BaseModel):
    """どちらか一方を指定する。volume_id ならその巻を含むソース(元ファイル/フォルダ)全体が対象。"""
    volume_id: StrictInt | None = None
    source_id: StrictInt | None = None


class ProgressIn(BaseModel):
    page: StrictInt = Field(ge=0)
    finished: StrictBool


class WorkEditIn(BaseModel):
    """作品の手動編集。送ったものだけ直す(title 以外は null / 空文字 / 空配列で空欄にできる)。"""
    title: StrictStr | None = None
    author: StrictStr | None = None
    kind: StrictStr | None = None  # manga / novel
    publisher: StrictStr | None = None
    synopsis: StrictStr | None = None
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


class AliasIn(BaseModel):
    alias: StrictStr


class MergeIn(BaseModel):
    other_id: StrictInt


class AssignIn(BaseModel):
    """巻の付け替え。work_id が null なら新しい作品に分ける(title は新しい作品の名前。省略すると巻の名前から)。"""
    volume_ids: list[StrictInt] = Field(min_length=1)
    work_id: StrictInt | None = None
    title: StrictStr | None = None


def _progress(row) -> dict | None:
    if row is None or row["page"] is None:
        return None
    return {"page": row["page"], "finished": bool(row["finished"])}


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
    _sync_works(conn)  # 作品への割り当てを取り直す(作品の機能が入る前の DB は、ここで初めて作品になる)
    conn.close()
    cache = ExtractionCache(settings.cache_dir, settings.cache_limit_bytes)
    library = Library(settings, cache)
    scan_lock = threading.Lock()
    duplicate_lock = threading.Lock()
    duplicate_state: dict = {"running": False, "started_at": None, "finished_at": None, "done": 0, "total": 0,
                             "summary": None, "error": None}
    metadata_lock = threading.Lock()
    metadata_state: dict = {"running": False, "started_at": None, "finished_at": None, "done": 0, "total": 0,
                            "current": None, "summary": None, "error": None}
    enabled_sources = tuple(n for n in linking.SEARCH_ORDER if n in settings.metadata.sources)

    def metadata_service(c: sqlite3.Connection) -> MetadataService:
        return MetadataService(settings.metadata, c, fetcher=metadata_fetcher)

    def background_metadata(stop: threading.Event) -> None:
        """設定で有効にしたとき、裏で 1 作品ずつ公開 DB の情報を取りに行く。失敗してもスキャン・閲覧を止めない。"""
        while not stop.wait(settings.metadata.background_interval_seconds):
            if scan_lock.locked() or not metadata_lock.acquire(blocking=False):
                continue
            try:
                c = db.connect(settings.db_path)
                try:
                    ids = linking.works_needing_fetch(c, settings.metadata, enabled_sources)
                    if ids:
                        linking.fetch_work(c, metadata_service(c), ids[0])
                        _sync_works(c)
                finally:
                    c.close()
            except Exception:  # noqa: BLE001 (裏の処理で本体を止めない)
                log.exception("裏の公開 DB の取得に失敗しました")
            finally:
                metadata_lock.release()

    def background_analysis(stop: threading.Event) -> None:
        """裏で少しずつ解析する(設定で有効なときだけ)。1 回に 1 巻。スキャン中は待つ。失敗しても止まらない。"""
        while not stop.wait(settings.background_analysis_interval):
            if scan_lock.locked():
                continue
            try:
                c = db.connect(settings.db_path)
                try:
                    library.analyze_next(c)
                finally:
                    c.close()
            except Exception:  # noqa: BLE001 (裏の処理で本体を止めない)
                log.exception("裏の解析に失敗しました")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        stop = threading.Event()
        if settings.background_analysis:
            threading.Thread(target=background_analysis, args=(stop,), name="spinebox-analysis", daemon=True).start()
        if settings.metadata.background and enabled_sources:
            threading.Thread(target=background_metadata, args=(stop,), name="spinebox-metadata", daemon=True).start()
        try:
            yield
        finally:
            stop.set()

    app = FastAPI(title="SpineBox", lifespan=lifespan)
    app.state.settings = settings
    app.state.cache = cache
    app.state.scan_lock = scan_lock
    app.state.duplicate_state = duplicate_state
    app.state.duplicate_lock = duplicate_lock
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

    @app.post("/api/scan")
    def scan(conn: sqlite3.Connection = Depends(get_conn)):
        if not scan_lock.acquire(blocking=False):
            raise HTTPException(409, "スキャン中です")
        try:
            result = Scanner(conn, settings).scan()
            _sync_works(conn)  # 名前の解析だけ。内側のアーカイブは取り出さない
        except Exception as e:  # noqa: BLE001 (画面に原因を出す。トレースバックはログへ)
            log.exception("スキャンに失敗しました")
            raise HTTPException(500, safe_text(f"スキャンに失敗しました: {type(e).__name__}: {e}")) from e
        finally:
            scan_lock.release()
        return {"volumes": result.volumes, "offline_roots": result.offline_roots,
                "issues": result.issues, "issues_total": result.issues_total}

    @app.get("/api/volumes")
    def list_volumes(conn: sqlite3.Connection = Depends(get_conn)):
        rows = conn.execute(
            """SELECT v.id, v.title, v.page_count, v.container_path, v.entry_prefix, v.inner_path, v.parent_id,
                      v.number_kind, v.number_start, v.number_end, v.number_evidence, v.analyzed_version,
                      pv.title AS parent_title, pv.entry_prefix AS parent_prefix, s.rel_path,
                      s.offline AS source_offline, r.online AS root_online, p.page, p.finished"""
            + LISTED_VOLUMES_JOIN + " LEFT JOIN reading_progress p ON p.volume_id = v.id " + LISTED_VOLUMES_WHERE
        ).fetchall()
        rows.sort(key=_list_sort_key)
        findings = compute_findings(conn)
        duplicates = candidate_counts(conn)
        work_ids = works.assignments(conn)
        return [{
            "id": r["id"],
            "title": r["title"],
            "source_name": Path(r["rel_path"]).name if r["rel_path"] else r["title"],
            "page_count": r["page_count"],
            "offline": is_offline(r),
            "progress": _progress(r),
            "parent_id": r["parent_id"],
            "parent_title": r["parent_title"],
            "volume_number": _number(r),
            "findings": findings.get(r["id"], []),
            "duplicate_count": duplicates.get(r["id"], 0),
            "work_id": work_ids.get(r["id"]),
            "can_analyze": is_analyzable(r) and r["analyzed_version"] is None and not is_offline(r),
        } for r in rows]

    def _get_progress(conn, volume_id: int):
        return _progress(conn.execute("SELECT page, finished FROM reading_progress WHERE volume_id = ?",
                                      (volume_id,)).fetchone())

    @app.get("/api/volumes/{volume_id}")
    def get_volume(volume_id: int, conn: sqlite3.Connection = Depends(get_conn)):
        with library.open(conn, volume_id) as vol:
            # 内側アーカイブの巻は、開いたついでに中の巻へ分解する(取り出しは済んでいるので追加の展開は無い)
            if library.register_analysis(conn, vol):
                _sync_works(conn)  # 中の巻(子)が現れた
            row = get_volume_row(conn, volume_id)
            return {
                "id": volume_id,
                "title": row["title"],
                "page_count": len(vol.page_names),
                "pages": [{"index": i, "name": n.rsplit("/", 1)[-1]} for i, n in enumerate(vol.page_names)],
                "progress": _get_progress(conn, volume_id),
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

    def _run_duplicate_scan() -> None:
        """指紋を作って候補を計算し直す。duplicate_lock を持ったまま呼び、終わったら離す。"""
        c = None
        try:
            c = db.connect(settings.db_path)

            def progress(done: int, total: int) -> None:
                duplicate_state.update(done=done, total=total)

            summary = DuplicateScanner(c, library, progress).run()
            _sync_works(c)  # 重複候補の証拠で、作品の統合を提案・実行する
            duplicate_state.update(summary=asdict(summary), error=None)
        except Exception as e:  # noqa: BLE001 (裏の処理。失敗は状態に残して本体を止めない)
            log.exception("重複候補の調査に失敗しました")
            duplicate_state.update(error=str(e))
        finally:
            if c is not None:
                c.close()
            duplicate_state.update(running=False, finished_at=db.now())
            duplicate_lock.release()

    @app.post("/api/duplicates/scan")
    def start_duplicate_scan(wait: bool = False):
        """見た目の指紋を作って重複候補を出す明示操作。スキャンでは行わない(内側アーカイブを取り出すため)。

        既定は裏で動かして 202 を返す(進み具合は GET /api/duplicates の scan)。wait=true なら終わるまで待つ。
        """
        if scan_lock.locked():
            raise HTTPException(409, "スキャン中です")
        if not duplicate_lock.acquire(blocking=False):
            raise HTTPException(409, "重複候補を調べています")
        duplicate_state.update(running=True, started_at=db.now(), finished_at=None, done=0, total=0, summary=None,
                               error=None)
        if wait:
            _run_duplicate_scan()
            return dict(duplicate_state)
        threading.Thread(target=_run_duplicate_scan, name="spinebox-duplicates", daemon=True).start()
        return JSONResponse(dict(duplicate_state), status_code=202)

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
        return {"scan": dict(duplicate_state), "candidates": candidates}

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

    def work_detail(conn: sqlite3.Connection, work_id: int) -> dict:
        return works.get_work(conn, work_id, enabled_sources)

    @app.get("/api/works")
    def list_works(conn: sqlite3.Connection = Depends(get_conn)):
        """作品の一覧(見えている巻を持つ作品)。unassigned は作品に割り当てられていない巻の数。"""
        return works.list_works(conn)

    @app.post("/api/works/refresh")
    def refresh_works(conn: sqlite3.Connection = Depends(get_conn)):
        """作品への割り当てと統合の提案を取り直す(名前の解析と DB の更新だけ)。手動の決定は変えない。"""
        return asdict(works.sync(conn))

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
        return {"state": dict(metadata_state), "sources": list(enabled_sources),
                "background": settings.metadata.background}

    def _run_metadata_fetch(ids: list[int]) -> None:
        """metadata_lock を持ったまま呼ぶ。作品ごとに取得し、取得元の失敗は作品に記録して次へ進む。"""
        c = None
        counts: dict[str, int] = {}
        try:
            c = db.connect(settings.db_path)
            service = metadata_service(c)
            for i, work_id in enumerate(ids):
                metadata_state.update(done=i, current=work_id)
                try:
                    result = linking.fetch_work(c, service, work_id)
                except works.WorkNotFound:
                    continue  # 取得の途中で別の作品に統合された
                for status in result.sources.values():
                    counts[status] = counts.get(status, 0) + 1
                _sync_works(c, apply_links=False)
            _sync_works(c)
            metadata_state.update(summary={"works": len(ids), "sources": counts}, error=None)
        except Exception as e:  # noqa: BLE001 (裏の処理。失敗は状態に残して本体を止めない)
            log.exception("公開 DB の情報の補完に失敗しました")
            metadata_state.update(error=str(e))
        finally:
            if c is not None:
                c.close()
            metadata_state.update(running=False, finished_at=db.now(), current=None)
            metadata_lock.release()

    @app.post("/api/works/metadata/fetch")
    def start_metadata_fetch(wait: bool = False, all: bool = False, conn: sqlite3.Connection = Depends(get_conn)):
        """公開 DB の情報を、取得が必要な作品(all=true なら全作品)について取りに行く明示操作。

        既定は裏で動かして 202 を返す(進み具合は GET /api/works/metadata)。スキャンとは独立で、
        取得元が落ちていても作品ごとに失敗を記録して続ける。
        """
        if not enabled_sources:
            raise HTTPException(409, "公開 DB の取得元が設定で無効になっています")
        if not metadata_lock.acquire(blocking=False):
            raise HTTPException(409, "公開 DB の情報を取得中です")
        try:
            if all:
                ids = [w["id"] for w in works.list_works(conn)["works"]]
            else:
                ids = linking.works_needing_fetch(conn, settings.metadata, enabled_sources)
        except Exception:
            metadata_lock.release()
            raise
        metadata_state.update(running=True, started_at=db.now(), finished_at=None, done=0, total=len(ids),
                              current=None, summary=None, error=None)
        if wait:
            _run_metadata_fetch(ids)
            return dict(metadata_state)
        threading.Thread(target=_run_metadata_fetch, args=(ids,), name="spinebox-metadata-fetch", daemon=True).start()
        return JSONResponse(dict(metadata_state), status_code=202)

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
        return work_detail(conn, work_id)

    @app.post("/api/works/{work_id}/metadata/fetch")
    def fetch_work_metadata(work_id: int, conn: sqlite3.Connection = Depends(get_conn)):
        """この作品の公開 DB の情報を取りに行く(数秒かかる)。取得元の失敗は作品の連携に理由つきで記録し、
        応答は 200 で返す。別名の一致で他の作品に統合されたら、吸収先の詳細を返す。"""
        if not enabled_sources:
            raise HTTPException(409, "公開 DB の取得元が設定で無効になっています")
        linking.fetch_work(conn, metadata_service(conn), work_id)
        _sync_works(conn)
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

    @app.put("/api/volumes/{volume_id}/progress")
    def put_progress(volume_id: int, body: ProgressIn, conn: sqlite3.Connection = Depends(get_conn)):
        row = get_volume_row(conn, volume_id)
        if is_offline(row):
            raise VolumeOffline(volume_id)
        if row["page_count"] is not None and body.page >= max(row["page_count"], 1):
            raise HTTPException(422, f"page は 0〜{max(row['page_count'] - 1, 0)} で指定してください")
        with db.transaction(conn) as c:
            c.execute("""INSERT INTO reading_progress (volume_id, page, finished, updated_at) VALUES (?, ?, ?, ?)
                         ON CONFLICT (volume_id) DO UPDATE SET page = excluded.page, finished = excluded.finished,
                         updated_at = excluded.updated_at""",
                      (volume_id, body.page, int(body.finished), db.now()))
        return _get_progress(conn, volume_id)

    # --- 整理処理(CBZ 化)。蔵書に書き込む唯一の経路。実行と元に戻すはスキャンと同じロックで直列化する ---

    def organizer(conn) -> Organizer:
        return Organizer(conn, settings, library)

    @app.post("/api/organize/plan")
    def organize_plan(body: OrganizePlanIn, conn: sqlite3.Connection = Depends(get_conn)):
        if (body.volume_id is None) == (body.source_id is None):
            raise HTTPException(422, "volume_id か source_id のどちらか一方を指定してください")
        return organizer(conn).plan(volume_id=body.volume_id, source_id=body.source_id)

    @app.get("/api/organize")
    def organize_history(conn: sqlite3.Connection = Depends(get_conn)):
        return organizer(conn).history()

    @app.get("/api/organize/{op_id}")
    def organize_get(op_id: str, conn: sqlite3.Connection = Depends(get_conn)):
        return organizer(conn).get(op_id)

    def _exclusive(fn):
        if not scan_lock.acquire(blocking=False):
            raise HTTPException(409, "スキャンか整理の処理中です")
        try:
            return fn()
        finally:
            scan_lock.release()

    @app.post("/api/organize/{op_id}/execute")
    def organize_execute(op_id: str, conn: sqlite3.Connection = Depends(get_conn)):
        return _exclusive(lambda: organizer(conn).execute(op_id))

    @app.post("/api/organize/{op_id}/undo")
    def organize_undo(op_id: str, conn: sqlite3.Connection = Depends(get_conn)):
        return _exclusive(lambda: organizer(conn).undo(op_id))

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
