import os
import asyncio
import sqlite3
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI, Query, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.middleware.cors import CORSMiddleware

from app.database import init_db, get_db
from app.db_backup import start_background_db_backup
from app.hash_worker import get_hash_status, run_hash_batch, start_background_hash_worker
from app.health import get_health_summary
from app.operation_log import get_operation_log
from app.scanner import (
    scan_artists,
    scan_folder,
    resolve_scan_folder_target,
    stop_scan,
    get_scan_state,
    start_background_scanner,
)
from app.api.artists import (
    list_artists,
    list_duplicate_artist_folders,
    get_artist,
    get_artist_stats,
)
from app.api.items import list_items, update_item_tags, update_item_tags_by_name, get_item
from app.api.tags import list_tags, search_tags, create_tag, update_tag, delete_tag
from app.api.folders import list_folders, update_folder_tags
from app.api.files import router as files_router
from app.api.moves import (
    candidate_as_new,
    confirm_candidate,
    get_move_candidates,
    get_move_history,
    ignore_candidate,
)
from app.folder_rename_executor import build_execution_plan, execute_folder_rename_plan, recheck_folder_rename_plan
from app.folder_rename_auto import (
    get_folder_rename_auto_state,
    run_folder_rename_for_artist,
    set_folder_rename_auto_enabled,
)
from app.folder_rename_planner import (
    auto_confirm_folder_rename_plans,
    export_folder_rename_csv,
    export_folder_rename_plans,
    list_folder_rename_groups,
    refresh_confirmed_folder_rename_plan,
    save_folder_rename_plan,
    unconfirm_folder_rename_plan,
)
from app.log import logger, record_ui_action, start_background_log_cleanup

ws_clients: list[WebSocket] = []
_ws_lock = threading.Lock()
_last_state = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    start_background_scanner()
    start_background_hash_worker()
    start_background_db_backup()
    start_background_log_cleanup()
    asyncio.create_task(_broadcast_scan_state())
    yield


app = FastAPI(title="媒体库", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(files_router)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(
        "Unhandled exception",
        exc_info=(type(exc), exc, exc.__traceback__),
    )
    return JSONResponse(
        status_code=500,
        content={"error": "Internal server error", "detail": "Unhandled server error"},
    )


async def _broadcast_scan_state():
    global _last_state
    while True:
        state = get_scan_state()
        if state != _last_state:
            _last_state = state
            stale = []
            with _ws_lock:
                for ws in ws_clients:
                    try:
                        await ws.send_json(state)
                    except Exception:
                        stale.append(ws)
                for ws in stale:
                    ws_clients.remove(ws)
        await asyncio.sleep(0.5)


@app.websocket("/ws/scan")
async def ws_scan(websocket: WebSocket):
    await websocket.accept()
    with _ws_lock:
        ws_clients.append(websocket)
    state = get_scan_state()
    await websocket.send_json(state)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        with _ws_lock:
            if websocket in ws_clients:
                ws_clients.remove(websocket)


@app.get("/api/artists")
def api_list_artists():
    return list_artists()


@app.get("/api/artists/duplicates")
def api_duplicate_artists():
    return list_duplicate_artist_folders()


@app.get("/api/artists/{artist_id}")
def api_get_artist(artist_id: int):
    artist = get_artist(artist_id)
    if not artist:
        raise HTTPException(404)
    return artist


@app.get("/api/artists/{artist_id}/stats")
def api_artist_stats(artist_id: int):
    return get_artist_stats(artist_id)


@app.get("/api/items")
def api_list_items(
    artist_id: int = Query(None),
    tag_id: int = Query(None),
    untagged: bool = Query(False),
    search: str = Query(None),
    search_tags_only: bool = Query(False),
    date_from: str = Query(None),
    date_to: str = Query(None),
    tags: str = Query(None),
    archive_only: bool = Query(False),
    media_type: str = Query(None),
    folder: str = Query(None),
    duplicates_only: bool = Query(False),
    offset: int = Query(0),
    limit: int = Query(100),
    sort: str = Query("date_desc"),
):
    return list_items(
        artist_id=artist_id, tag_id=tag_id, untagged=untagged, search=search,
        search_tags_only=search_tags_only,
        date_from=date_from, date_to=date_to, tags=tags,
        archive_only=archive_only, image_only=False,
        media_type=media_type,
        folder=folder, duplicates_only=duplicates_only,
        offset=offset, limit=limit, sort=sort
    )


@app.get("/api/items/{item_id}")
def api_get_item(item_id: int):
    item = get_item(item_id)
    if not item:
        raise HTTPException(404)
    return item


@app.put("/api/items/tags")
def api_update_item_tags(
    artist_id: int = Query(...),
    item_ids: str = Query(...),
    tag_ids: str = Query(""),
    mode: str = Query("set"),
):
    ids = [int(x) for x in item_ids.split(",") if x.strip()]
    tids = [int(x) for x in tag_ids.split(",") if x.strip()]
    if not ids:
        raise HTTPException(400, "No item ids")
    if mode not in ("set", "add", "remove"):
        raise HTTPException(400, "Bad mode")
    return update_item_tags(artist_id, ids, tids, mode)


@app.put("/api/items/tags-by-name")
async def api_update_item_tags_by_name(request: Request):
    payload = await request.json()
    ids = [int(x) for x in payload.get("item_ids", []) if str(x).strip()]
    tag_names = [str(x).strip() for x in payload.get("tag_names", []) if str(x).strip()]
    mode = payload.get("mode", "add")
    if not ids:
        raise HTTPException(400, "No item ids")
    if mode not in ("set", "add", "remove"):
        raise HTTPException(400, "Bad mode")
    if not tag_names and mode != "set":
        raise HTTPException(400, "No tag names")
    try:
        return update_item_tags_by_name(ids, tag_names, mode)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.post("/api/ui-log")
async def api_ui_log(request: Request):
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    event = payload.get("event", "unknown") if isinstance(payload, dict) else "unknown"
    data = payload.get("data", {}) if isinstance(payload, dict) else {}
    record_ui_action(event, data)
    return {"ok": True}


@app.get("/api/folders")
def api_list_folders(artist_id: int = Query(...)):
    return list_folders(artist_id)


@app.put("/api/folders/tags")
def api_update_folder_tags(
    artist_id: int = Query(...),
    folder: str = Query(""),
    tag_ids: str = Query(""),
    mode: str = Query("add"),
):
    tids = [int(x) for x in tag_ids.split(",") if x.strip()]
    if mode not in ("set", "add", "remove"):
        raise HTTPException(400, "Bad mode")
    return update_folder_tags(artist_id, folder, tids, mode)


@app.get("/api/folder-renames")
def api_folder_renames(
    artist_id: int = Query(...),
    status: str | None = Query(None),
    offset: int = Query(0),
    limit: int = Query(200),
    refresh: bool = Query(False),
):
    return list_folder_rename_groups(
        artist_id,
        status=status,
        offset=offset,
        limit=limit,
        refresh=refresh,
    )


@app.put("/api/folder-renames")
async def api_save_folder_rename(request: Request):
    payload = await request.json()
    try:
        artist_id = int(payload.get("artist_id"))
        source_folder = str(payload.get("source_folder") or "")
        selected_tag_ids = [
            int(value)
            for value in payload.get("selected_tag_ids", [])
            if str(value).strip()
        ]
        status = str(payload.get("status") or "draft")
        plan_kind = str(payload.get("plan_kind") or "rename_folder")
        result = save_folder_rename_plan(
            artist_id,
            source_folder,
            selected_tag_ids=selected_tag_ids,
            status=status,
            plan_kind=plan_kind,
        )
        record_ui_action(
            "folder_rename_plan_saved",
            {
                "artist_id": artist_id,
                "source_folder": source_folder,
                "tag_count": len(selected_tag_ids),
                "status": status,
                "plan_kind": plan_kind,
            },
        )
        return result
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.post("/api/folder-renames/auto-confirm")
def api_auto_confirm_folder_renames(artist_id: int = Query(...)):
    return auto_confirm_folder_rename_plans(artist_id)


@app.get("/api/folder-renames/export")
def api_export_folder_renames(
    artist_id: int = Query(...),
    format: str = Query("json"),
):
    if format == "csv":
        return Response(
            export_folder_rename_csv(artist_id),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": "attachment; filename=folder-renames.csv"},
        )
    if format != "json":
        raise HTTPException(400, "Bad export format")
    return {"rows": export_folder_rename_plans(artist_id)}


@app.post("/api/folder-renames/execute")
def api_execute_folder_renames(artist_id: int = Query(...), execute: bool = Query(False)):
    if not execute:
        return build_execution_plan(artist_id, dry_run=True)
    return execute_folder_rename_plan(artist_id)


@app.get("/api/folder-renames/auto")
def api_get_folder_rename_auto():
    return get_folder_rename_auto_state()


@app.put("/api/folder-renames/auto")
async def api_set_folder_rename_auto(request: Request):
    payload = await request.json()
    enabled = (payload.get("enabled") is True) if isinstance(payload, dict) else False
    return set_folder_rename_auto_enabled(enabled)


@app.post("/api/folder-renames/auto/run")
def api_run_folder_rename_auto(artist_id: int = Query(...)):
    return run_folder_rename_for_artist(artist_id)


@app.post("/api/folder-renames/plans/{plan_id}/recheck")
def api_recheck_folder_rename_plan(plan_id: int):
    try:
        return recheck_folder_rename_plan(plan_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.post("/api/folder-renames/plans/{plan_id}/reconfirm")
def api_refresh_confirmed_folder_rename_plan(plan_id: int):
    try:
        plan = refresh_confirmed_folder_rename_plan(plan_id)
        return {"plan": plan, "check": recheck_folder_rename_plan(plan_id)}
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.post("/api/folder-renames/plans/{plan_id}/unconfirm")
def api_unconfirm_folder_rename_plan(plan_id: int):
    try:
        return {"plan": unconfirm_folder_rename_plan(plan_id)}
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.get("/api/tags")
def api_list_tags(artist_id: int = Query(...)):
    return list_tags(artist_id)


@app.get("/api/tags/search")
def api_search_tags(
    search: str = Query(""),
    artist_id: int = Query(None),
    limit: int = Query(100),
):
    return {"tags": search_tags(search, artist_id=artist_id, limit=limit)}


@app.post("/api/tags")
def api_create_tag(artist_id: int = Query(...), name: str = Query(...)):
    if not name.strip():
        raise HTTPException(400, "Empty tag")
    return create_tag(artist_id, name)


@app.put("/api/tags/{tag_id}")
def api_update_tag(
    tag_id: int,
    artist_id: int = Query(...),
    name: str = Query(None),
    sort_order: int = Query(None),
):
    result = update_tag(artist_id, tag_id, name, sort_order)
    if result is None:
        raise HTTPException(404)
    return result


@app.delete("/api/tags/{tag_id}")
def api_delete_tag(tag_id: int, artist_id: int = Query(...)):
    if not delete_tag(artist_id, tag_id):
        raise HTTPException(404)
    return {"ok": True}


@app.post("/api/scan")
def api_trigger_scan():
    state = get_scan_state()
    if state.get("status") == "scanning":
        return {"ok": False, "message": "Already scanning"}
    threading.Thread(target=scan_artists, kwargs={"manual": True}, daemon=True).start()
    return {"ok": True}


@app.post("/api/scan/folder")
def api_trigger_folder_scan(artist_id: int = Query(...), folder: str | None = Query(None)):
    state = get_scan_state()
    if state.get("status") == "scanning":
        return {"ok": False, "message": "Already scanning"}
    if not resolve_scan_folder_target(artist_id, folder):
        raise HTTPException(400, "Invalid folder")
    threading.Thread(target=scan_folder, args=(artist_id, folder), kwargs={"manual": True}, daemon=True).start()
    return {"ok": True}


@app.post("/api/scan/stop")
def api_stop_scan():
    state = get_scan_state()
    if state.get("status") != "scanning":
        return {"ok": False, "message": "Not scanning"}
    stop_scan()
    return {"ok": True}


@app.get("/api/scan/state")
def api_scan_state():
    return get_scan_state()


@app.get("/api/move-candidates")
def api_move_candidates(status: str = Query("pending")):
    return get_move_candidates(status)


@app.get("/api/move-history")
def api_move_history(status: str = Query(None)):
    return get_move_history(status)


@app.get("/api/hash/status")
def api_hash_status():
    try:
        status = get_hash_status()
        status.setdefault("ok", True)
        return status
    except sqlite3.DatabaseError as exc:
        logger.exception("Hash status database error")
        return {
            "ok": False,
            "database_error": True,
            "message": str(exc),
            "items": {"pending": 0, "processing": 0, "done": 0, "error": 0, "total": 0, "remaining": 0},
            "scan_candidates": {"pending": 0, "processing": 0, "done": 0, "error": 0, "total": 0, "remaining": 0},
        }


@app.get("/api/health")
def api_health():
    return get_health_summary()


@app.get("/api/operation-log")
def api_operation_log(limit: int = Query(80), error_limit: int = Query(40)):
    return get_operation_log(limit=limit, error_limit=error_limit)


@app.post("/api/backup")
def api_backup():
    from app.db_backup import create_db_backup
    try:
        backup_dir = create_db_backup()
        return {"ok": True, "path": str(backup_dir)}
    except Exception as exc:
        logger.exception("Manual backup failed")
        raise HTTPException(500, detail=str(exc))


@app.post("/api/hash/run")
def api_run_hash_batch(limit: int = Query(100, ge=1, le=1000)):
    try:
        return run_hash_batch(limit=limit)
    except sqlite3.DatabaseError as exc:
        logger.exception("Hash run database error")
        return {
            "ok": False,
            "database_error": True,
            "message": str(exc),
            "status": api_hash_status(),
        }


@app.post("/api/move-candidates/{candidate_id}/confirm")
def api_confirm_move_candidate(candidate_id: int):
    return confirm_candidate(candidate_id)


@app.post("/api/move-candidates/{candidate_id}/new")
def api_move_candidate_as_new(candidate_id: int):
    return candidate_as_new(candidate_id)


@app.post("/api/move-candidates/{candidate_id}/ignore")
def api_ignore_move_candidate(candidate_id: int):
    return ignore_candidate(candidate_id)


app.mount("/static", StaticFiles(directory=os.path.join(os.path.dirname(__file__), "static")), name="static")


@app.get("/")
def index():
    return FileResponse(os.path.join(os.path.dirname(__file__), "static", "index.html"))
