"""FastAPI アプリ。API の契約はフロント(frontend/)と共有している(wiki/components/backend.md)。

`frontend/dist` があれば `/` で静的配信し、API 以外の未知のパスは index.html を返す(SPA の fallback)。
"""
from __future__ import annotations

import hashlib
import sqlite3
import threading
from pathlib import Path
from typing import Iterator

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field, StrictBool, StrictInt

from . import db
from .buildinfo import current_commit
from .cache import CacheTooLarge, ExtractionCache
from .config import Settings
from .library import Library, VolumeNotFound, VolumeOffline, VolumeUnreadable, get_volume_row, is_offline
from .organize.organizer import OperationNotFound, OrganizeError, Organizer
from .pages import media_type, natural_key
from .scanner import Scanner

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


def _progress(row) -> dict | None:
    if row is None or row["page"] is None:
        return None
    return {"page": row["page"], "finished": bool(row["finished"])}


def create_app(settings: Settings, frontend_dist: Path | None = DEFAULT_FRONTEND_DIST) -> FastAPI:
    app = FastAPI(title="SpineBox")
    conn = db.open_db(settings.db_path)  # 起動時にマイグレーションを当てる
    conn.close()
    cache = ExtractionCache(settings.cache_dir, settings.cache_limit_bytes)
    library = Library(settings, cache)
    scan_lock = threading.Lock()
    app.state.settings = settings
    app.state.cache = cache
    app.state.scan_lock = scan_lock
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
        finally:
            scan_lock.release()
        return {"volumes": result.volumes, "offline_roots": result.offline_roots}

    @app.get("/api/volumes")
    def list_volumes(conn: sqlite3.Connection = Depends(get_conn)):
        rows = conn.execute(
            """SELECT v.id, v.title, v.page_count, v.container_path, v.entry_prefix, s.rel_path,
                      s.offline AS source_offline, r.online AS root_online, p.page, p.finished
               FROM volumes v JOIN sources s ON s.id = v.source_id JOIN library_roots r ON r.id = s.root_id
               LEFT JOIN reading_progress p ON p.volume_id = v.id
               WHERE r.enabled = 1 AND v.missing = 0 AND s.missing = 0 AND v.organized_op_id IS NULL"""
        ).fetchall()
        rows.sort(key=lambda r: (natural_key(r["rel_path"]), natural_key(r["entry_prefix"]),
                                 natural_key(r["container_path"] or "")))
        return [{
            "id": r["id"],
            "title": r["title"],
            "source_name": Path(r["rel_path"]).name if r["rel_path"] else r["title"],
            "page_count": r["page_count"],
            "offline": is_offline(r),
            "progress": _progress(r),
        } for r in rows]

    def _get_progress(conn, volume_id: int):
        return _progress(conn.execute("SELECT page, finished FROM reading_progress WHERE volume_id = ?",
                                      (volume_id,)).fetchone())

    @app.get("/api/volumes/{volume_id}")
    def get_volume(volume_id: int, conn: sqlite3.Connection = Depends(get_conn)):
        with library.open(conn, volume_id) as vol:
            row = get_volume_row(conn, volume_id)
            return {
                "id": volume_id,
                "title": row["title"],
                "page_count": len(vol.page_names),
                "pages": [{"index": i, "name": n.rsplit("/", 1)[-1]} for i, n in enumerate(vol.page_names)],
                "progress": _get_progress(conn, volume_id),
            }

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
