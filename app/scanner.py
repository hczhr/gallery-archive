import os
import time
import threading
import uuid
import inspect
from app.database import get_db
from app.fingerprint import normalize_path, stat_path
from app.folder_utils import folder_path_prefix, normalize_folder, normalize_slashes
from app.log import logger
from app.media_roots import load_media_root_globals
from app.move_matcher import resolve_scan_candidate
from app.role_extractor import (
    extract_date, is_media_file, media_type_for_file
)

PICTURES_ROOTS, _PICTURES_ROOT_LABELS, _PICTURES_ROOT_REAL_PATHS = load_media_root_globals()
PICTURES_ROOT = ",".join(PICTURES_ROOTS)
SCAN_INTERVAL = int(os.environ.get("SCAN_INTERVAL", "0"))
SCAN_ON_START = os.environ.get("SCAN_ON_START", "0").lower() in ("1", "true", "yes")
SCAN_STALE_SECONDS = int(os.environ.get("SCAN_STALE_SECONDS", "300"))
PARSE_PROGRESS_EVERY = int(os.environ.get("PARSE_PROGRESS_EVERY", "100"))
MOVE_DRY_RUN = os.environ.get("MOVE_DRY_RUN", "1").lower() not in ("0", "false", "no")
AUTO_MOVE_INODE = os.environ.get("AUTO_MOVE_INODE", "1").lower() not in ("0", "false", "no")
AUTO_MOVE_HASH_UNIQUE = os.environ.get("AUTO_MOVE_HASH_UNIQUE", "1").lower() not in ("0", "false", "no")

CATEGORY_DIR_NAMES = {
    "- R18",
    "- 全年龄",
    "- 停更",
    "- 无码",
    "- 有码",
    "- loli",
    "loli",
    "- 已收集未整理",
    "- 已整理",
    "R18",
    "全年龄",
    "无码",
    "有码",
    "已收集未整理",
    "已整理",
}

_scan_lock = threading.Lock()
_scan_running = False
_scan_stop = False
_scan_thread = None
_schedule_condition = threading.Condition()
_next_auto_scan_at = None
_next_auto_scan_deferred_by_manual = False


def _is_category_dir_name(name: str) -> bool:
    return name.startswith("-") or name in CATEGORY_DIR_NAMES


def stop_scan():
    global _scan_stop
    _scan_stop = True


def update_scan_state(**kwargs):
    db = get_db()
    sets = ", ".join(f"{k}=?" for k in kwargs)
    values = list(kwargs.values())
    db.execute(
        f"UPDATE scan_state SET {sets}, updated_at=? WHERE id=1",
        values + [time.time()]
    )


def get_scan_state():
    db = get_db()
    row = db.execute("SELECT * FROM scan_state WHERE id=1").fetchone()
    if not row:
        return {"status": "idle"}

    state = dict(row)
    if state.get("status") == "error" and state.get("phase") == "interrupted":
        update_scan_state(
            status="idle",
            phase="interrupted",
            scanned_count=0,
            total_estimate=0,
            current_path="",
        )
        db.commit()
        row = db.execute("SELECT * FROM scan_state WHERE id=1").fetchone()
        return dict(row) if row else {"status": "idle"}

    updated_at = state.get("updated_at") or state.get("started_at") or 0
    if (
        state.get("status") == "scanning"
        and not _scan_running
        and updated_at
        and time.time() - float(updated_at) > SCAN_STALE_SECONDS
    ):
        update_scan_state(
            status="idle",
            phase="interrupted",
            scanned_count=0,
            total_estimate=0,
            current_path="",
        )
        db.commit()
        row = db.execute("SELECT * FROM scan_state WHERE id=1").fetchone()
        return dict(row) if row else {"status": "idle"}
    return state


def _parse_progress_path(artist_name: str, index: int, total: int) -> str:
    return f"{artist_name} \u00b7 {index}/{total} 候选"


def _set_next_auto_scan_at(timestamp: float, *, deferred_by_manual: bool = False) -> None:
    global _next_auto_scan_at, _next_auto_scan_deferred_by_manual
    with _schedule_condition:
        _next_auto_scan_at = timestamp
        _next_auto_scan_deferred_by_manual = deferred_by_manual
        _schedule_condition.notify_all()


def defer_next_auto_scan(now: float | None = None) -> None:
    if SCAN_INTERVAL <= 0:
        return
    start_from = time.time() if now is None else now
    _set_next_auto_scan_at(start_from + SCAN_INTERVAL, deferred_by_manual=True)
    logger.info("Background scanner deferred %d seconds after manual scan", SCAN_INTERVAL)


def get_auto_scan_schedule(now: float | None = None) -> dict:
    current = time.time() if now is None else now
    with _schedule_condition:
        next_run = _next_auto_scan_at
        deferred_by_manual = _next_auto_scan_deferred_by_manual
    enabled = SCAN_INTERVAL > 0
    seconds_until_next = None
    overdue = False
    if enabled and next_run is not None:
        remaining = next_run - current
        overdue = remaining <= 0
        seconds_until_next = 0 if overdue else round(remaining, 1)
    return {
        "enabled": enabled,
        "interval": SCAN_INTERVAL,
        "on_start": SCAN_ON_START,
        "next_auto_scan_at": next_run,
        "seconds_until_next": seconds_until_next,
        "overdue": overdue,
        "deferred_by_manual": deferred_by_manual,
    }


def _run_folder_rename_auto_after_scan(scope: str, artist_id: int | None = None) -> None:
    from app.folder_rename_auto import run_folder_rename_auto_after_scan

    run_folder_rename_auto_after_scan(scope=scope, artist_id=artist_id)


def _run_scan(scan_fn, *args, on_finish=None, on_success=None):
    global _scan_running, _scan_stop
    started = False
    with _scan_lock:
        if _scan_running:
            return False
        _scan_running = True
        _scan_stop = False
        started = True

    success = False
    try:
        scan_fn(*args)
        success = True
    except Exception as exc:
        logger.exception("Scan failed")
        try:
            update_scan_state(status="error", phase=str(exc), current_path="")
            get_db().commit()
        except Exception:
            logger.exception("Failed to update scan state after scan error")
    finally:
        with _scan_lock:
            _scan_running = False
            _scan_stop = False
        if success and on_success:
            try:
                on_success()
            except Exception:
                logger.exception("Failed to run folder archive auto execution")
        if started and on_finish:
            try:
                on_finish()
            except Exception:
                logger.exception("Failed to update automatic scan schedule")

    return success


def scan_artists(manual: bool = False):
    return _run_scan(
        _do_scan,
        on_finish=defer_next_auto_scan if manual else None,
        on_success=lambda: _run_folder_rename_auto_after_scan(scope="full", artist_id=None),
    )


def resolve_scan_folder_target(artist_id: int, folder: str | None):
    db = get_db()
    artist = db.execute(
        "SELECT id, name, path FROM artists WHERE id=?",
        (artist_id,),
    ).fetchone()
    if not artist:
        return None

    folder = normalize_folder(folder)
    artist_root = os.path.abspath(artist["path"])
    if not folder:
        if not os.path.isdir(artist_root):
            return None
        return {
            "artist_id": artist["id"],
            "artist_name": artist["name"],
            "artist_path": artist["path"],
            "folder": "",
            "target_dir": artist_root,
        }

    target_dir = os.path.abspath(os.path.join(artist["path"], *folder.split("/")))
    artist_real = os.path.realpath(artist_root)
    target_real = os.path.realpath(target_dir)
    try:
        if os.path.commonpath([target_real, artist_real]) != artist_real:
            return None
    except ValueError:
        return None
    if target_real == artist_real or not os.path.isdir(target_dir):
        return None

    return {
        "artist_id": artist["id"],
        "artist_name": artist["name"],
        "artist_path": artist["path"],
        "folder": folder,
        "target_dir": target_dir,
    }


def scan_folder(artist_id: int, folder: str | None, manual: bool = False):
    target = resolve_scan_folder_target(artist_id, folder)
    if not target:
        return False
    return _run_scan(
        _do_scan_folder,
        target,
        on_finish=defer_next_auto_scan if manual else None,
    )


def _count_media_files(directory: str) -> int:
    count = 0
    try:
        for entry in os.listdir(directory):
            if entry.startswith("."):
                continue
            if is_media_file(entry):
                count += 1
    except OSError:
        pass
    return count


def _has_subdirs(directory: str) -> bool:
    try:
        for entry in os.listdir(directory):
            if entry.startswith("."):
                continue
            if os.path.isdir(os.path.join(directory, entry)):
                return True
    except OSError:
        pass
    return False


def _discover_artist_dirs(root_path: str) -> list[tuple[str, str]]:
    result = []

    def walk(current: str, depth: int):
        try:
            entries = sorted(os.listdir(current))
        except OSError:
            return

        for entry in entries:
            if entry.startswith("."):
                continue
            full = os.path.join(current, entry)
            if not os.path.isdir(full):
                continue

            if _is_category_dir_name(entry):
                walk(full, depth + 1)
                continue

            has_subdirs = _has_subdirs(full)
            media_count = _count_media_files(full)

            if has_subdirs or media_count > 0:
                name = os.path.basename(full)
                result.append((name, full))
                continue

            walk(full, depth + 1)

    walk(root_path, 0)
    return result


def _path_under_roots(path: str, roots: list[str]) -> bool:
    full = os.path.realpath(os.path.abspath(path))
    for root in roots:
        root_full = os.path.realpath(os.path.abspath(root))
        try:
            if os.path.commonpath([full, root_full]) == root_full:
                return True
        except ValueError:
            continue
    return False


def _append_known_artist_dirs(dirs: list[tuple[str, str]], roots: list[str]) -> list[tuple[str, str]]:
    db = get_db()
    by_path = {path: name for name, path in dirs}
    for row in db.execute("SELECT name, path FROM artists").fetchall():
        if row["path"] in by_path:
            continue
        if os.path.isdir(row["path"]) and _path_under_roots(row["path"], roots):
            by_path[row["path"]] = os.path.basename(row["path"]) or row["name"]
    return [(name, path) for path, name in by_path.items()]


def _do_scan():
    logger.info("Scan started, roots: %s", PICTURES_ROOTS)
    start_time = time.time()
    scan_id = uuid.uuid4().hex
    update_scan_state(status="scanning", phase="discover", scanned_count=0,
                      total_estimate=0, current_path="", started_at=start_time)
    get_db().commit()

    all_artist_dirs = []
    accessible_roots = []
    for root_path in PICTURES_ROOTS:
        if not os.path.isdir(root_path):
            logger.warning("Root not found: %s", root_path)
            continue
        accessible_roots.append(root_path)
        dirs = _discover_artist_dirs(root_path)
        logger.info("Root %s: found %d artist directories", root_path, len(dirs))
        all_artist_dirs.extend(dirs)

    if not accessible_roots:
        update_scan_state(status="error", phase="no roots found")
        logger.error("No artist directories found")
        return

    all_artist_dirs = _append_known_artist_dirs(all_artist_dirs, accessible_roots)

    logger.info("Total artists to scan: %d", len(all_artist_dirs))
    update_scan_state(status="scanning", phase="discover",
                      scanned_count=0, total_estimate=len(all_artist_dirs))

    db = get_db()
    db.commit()

    for i, (artist_name, artist_path) in enumerate(all_artist_dirs):
        if _scan_stop:
            logger.info("Scan stopped by user at artist %d/%d", i, len(all_artist_dirs))
            db.rollback()
            update_scan_state(status="idle", phase="stopped",
                              scanned_count=i, total_estimate=len(all_artist_dirs),
                              current_path="")
            get_db().commit()
            return

        update_scan_state(
            status="scanning", phase="scan",
            scanned_count=i + 1, total_estimate=len(all_artist_dirs),
            current_path=artist_name
        )
        db.commit()

        artist_row = db.execute(
            "SELECT id, name FROM artists WHERE path=?",
            (artist_path,)
        ).fetchone()

        if artist_row:
            artist_id = artist_row["id"]
            if artist_row["name"] != artist_name:
                db.execute("UPDATE artists SET name=? WHERE id=?", (artist_name, artist_id))
            db.execute(
                "UPDATE artists SET missing=0, missing_at=NULL WHERE id=?",
                (artist_id,),
            )
        else:
            cur = db.execute(
                "INSERT INTO artists (name, path) VALUES (?, ?)",
                (artist_name, artist_path)
            )
            artist_id = cur.lastrowid
            db.commit()

        if len(inspect.signature(_scan_artist).parameters) == 3:
            _scan_artist(artist_id, artist_name, artist_path)
        else:
            _scan_artist(artist_id, artist_name, artist_path, scan_id)
        db.commit()

    stale_count = 0
    current_paths = {p for _, p in all_artist_dirs}
    stale = db.execute("SELECT id, path FROM artists").fetchall()
    for row in stale:
        if (
            row["path"] not in current_paths
            and not os.path.isdir(row["path"])
            and _path_under_roots(row["path"], accessible_roots)
        ):
            now = time.time()
            db.execute(
                "UPDATE artists SET missing=1, missing_at=? WHERE id=?",
                (now, row["id"]),
            )
            db.execute(
                """
                UPDATE items
                SET missing=1, missing_at=?
                WHERE artist_id=? AND missing=0
                """,
                (now, row["id"]),
            )
            stale_count += 1

    db.commit()

    elapsed = time.time() - start_time
    total_items = db.execute("SELECT COUNT(*) FROM items WHERE missing=0").fetchone()[0]
    logger.info("Scan complete: %d artists, %d active items, %d stale marked, %.1fs",
                len(all_artist_dirs), total_items, stale_count, elapsed)

    update_scan_state(status="idle", phase="complete",
                      scanned_count=len(all_artist_dirs),
                      total_estimate=len(all_artist_dirs),
                      current_path="")
    db.commit()


def _do_scan_folder(target: dict):
    db = get_db()
    start_time = time.time()
    scan_id = uuid.uuid4().hex
    current_path = target["artist_name"] if not target["folder"] else f"{target['artist_name']}/{target['folder']}"
    missing_prefix = None if not target["folder"] else folder_path_prefix(target["artist_path"], target["folder"])
    logger.info("Folder scan started: %s", current_path)
    update_scan_state(status="scanning", phase="scan", scanned_count=0,
                      total_estimate=1, current_path=current_path,
                      started_at=start_time)
    db.commit()

    _scan_artist(
        target["artist_id"],
        target["artist_name"],
        target["artist_path"],
        scan_id,
        scan_root=target["target_dir"],
        missing_prefix=missing_prefix,
    )
    db.commit()

    elapsed = time.time() - start_time
    logger.info("Folder scan complete: %s, %.1fs", current_path, elapsed)
    update_scan_state(status="idle", phase="complete",
                      scanned_count=1, total_estimate=1, current_path="")
    db.commit()


def _scan_artist(
    artist_id: int,
    artist_name: str,
    artist_path: str,
    scan_id: str,
    scan_root: str | None = None,
    missing_prefix: str | None = None,
):
    db = get_db()

    existing = {}
    existing_sql = "SELECT * FROM items WHERE artist_id=?"
    existing_params = [artist_id]
    if missing_prefix:
        existing_sql += " AND substr(replace(file_path, '\\', '/'), 1, ?) = ?"
        existing_params.extend([len(missing_prefix), missing_prefix])
    for row in db.execute(existing_sql, existing_params):
        existing[row["file_path"]] = dict(row)

    current_paths = set()
    new_candidate_ids = []
    hash_item_ids = []
    updated_count = 0
    artist_path_norm = normalize_slashes(os.path.abspath(artist_path)).rstrip("/")
    scan_root = normalize_slashes(os.path.abspath(scan_root or artist_path))

    for root, dirs, files in os.walk(scan_root):
        dirs[:] = [d for d in dirs if not d.startswith(".")]

        folder_name = os.path.basename(root)
        if normalize_slashes(os.path.abspath(root)).rstrip("/") == artist_path_norm:
            folder_name = ""

        for fname in files:
            if fname.startswith("."):
                continue

            full_path = normalize_path(os.path.join(root, fname))

            media_type = media_type_for_file(fname)
            if not media_type:
                continue

            current_paths.add(full_path)

            try:
                file_stat = stat_path(full_path)
            except OSError:
                continue

            date_str = extract_date(folder_name)
            is_archive = 1 if media_type == "archive" else 0
            old = existing.get(full_path)
            content_hash = ""
            hash_status = "pending"
            hash_updated_at = None
            same_file = False
            if old:
                same_file = (
                    old["file_size"] == file_stat.file_size
                    and abs(old["file_mtime"] - file_stat.file_mtime) < 1.0
                )
                if same_file and old["hash_status"] == "done":
                    content_hash = old["content_hash"]
                    hash_status = old["hash_status"]
                    hash_updated_at = old["hash_updated_at"]

            db.execute(
                """
                INSERT INTO scan_seen
                (scan_id, artist_id, file_path, media_type, file_size, file_mtime,
                 st_dev, st_ino, content_hash, hash_status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    scan_id,
                    artist_id,
                    full_path,
                    media_type,
                    file_stat.file_size,
                    file_stat.file_mtime,
                    file_stat.st_dev,
                    file_stat.st_ino,
                    content_hash,
                    hash_status,
                ),
            )

            if full_path in existing:
                db.execute(
                    """
                    UPDATE items
                    SET file_name=?, file_size=?, file_mtime=?,
                        folder_name=?, date=?, is_archive=?, media_type=?,
                        content_hash=?, hash_status=?, hash_updated_at=?,
                        st_dev=?, st_ino=?, missing=0, missing_at=NULL,
                        scanned_at=strftime('%s','now')
                    WHERE id=?
                    """,
                    (
                        fname,
                        file_stat.file_size,
                        file_stat.file_mtime,
                        folder_name,
                        date_str,
                        is_archive,
                        media_type,
                        content_hash,
                        hash_status,
                        hash_updated_at,
                        file_stat.st_dev,
                        file_stat.st_ino,
                        existing[full_path]["id"],
                    ),
                )
                updated_count += 1
                if hash_status != "done" or not content_hash:
                    hash_item_ids.append(existing[full_path]["id"])
            else:
                existing_candidate = db.execute(
                    """
                    SELECT id, status FROM scan_candidates
                    WHERE file_path=? AND status IN ('pending', 'candidate', 'previewed')
                    ORDER BY id DESC LIMIT 1
                    """,
                    (full_path,),
                ).fetchone()
                if existing_candidate:
                    candidate_id = existing_candidate["id"]
                    next_status = "pending" if existing_candidate["status"] == "previewed" and not MOVE_DRY_RUN else existing_candidate["status"]
                    resolved_at_expr = ", resolved_at=NULL" if next_status == "pending" else ""
                    db.execute(
                        f"""
                        UPDATE scan_candidates
                        SET scan_id=?, artist_id=?, file_name=?, file_size=?,
                            file_mtime=?, folder_name=?, date=?, is_archive=?,
                            media_type=?, st_dev=?, st_ino=?, status=?
                            {resolved_at_expr}
                        WHERE id=?
                        """,
                        (
                            scan_id,
                            artist_id,
                            fname,
                            file_stat.file_size,
                            file_stat.file_mtime,
                            folder_name,
                            date_str,
                            is_archive,
                            media_type,
                            file_stat.st_dev,
                            file_stat.st_ino,
                            next_status,
                            candidate_id,
                        ),
                    )
                else:
                    cur = db.execute(
                        """
                        INSERT INTO scan_candidates
                        (scan_id, artist_id, file_path, file_name, file_size,
                         file_mtime, folder_name, date, is_archive, media_type, st_dev, st_ino)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            scan_id,
                            artist_id,
                            full_path,
                            fname,
                            file_stat.file_size,
                            file_stat.file_mtime,
                            folder_name,
                            date_str,
                            is_archive,
                            media_type,
                            file_stat.st_dev,
                            file_stat.st_ino,
                        ),
                    )
                    candidate_id = cur.lastrowid
                new_candidate_ids.append(candidate_id)

    total_candidates = len(new_candidate_ids)
    parse_started_at = time.time()
    progress_every = max(1, PARSE_PROGRESS_EVERY)
    log_parse_progress = total_candidates >= progress_every
    if total_candidates:
        update_scan_state(phase="parse", current_path=_parse_progress_path(artist_name, 0, total_candidates))
        if log_parse_progress:
            logger.info("Artist [%s]: resolving %d scan candidates", artist_name, total_candidates)
    else:
        update_scan_state(phase="parse", current_path=artist_name)
    db.commit()

    active_existing = {
        path for path, row in existing.items()
        if not row["missing"]
    }
    deleted = active_existing - current_paths
    if deleted:
        placeholders = ",".join("?" * len(deleted))
        db.execute(
            f"""
            UPDATE items
            SET missing=1, missing_at=?
            WHERE artist_id=? AND file_path IN ({placeholders})
            """,
            [time.time(), artist_id] + list(deleted)
        )
        db.commit()

    for index, candidate_id in enumerate(new_candidate_ids, start=1):
        if index == 1 or index % progress_every == 0 or index == total_candidates:
            update_scan_state(phase="parse", current_path=_parse_progress_path(artist_name, index, total_candidates))
            db.commit()
        resolve_scan_candidate(
            candidate_id,
            dry_run=MOVE_DRY_RUN,
            auto_move_inode=AUTO_MOVE_INODE,
            auto_move_hash_unique=AUTO_MOVE_HASH_UNIQUE,
        )

    if log_parse_progress:
        logger.info("Artist [%s]: resolved %d scan candidates in %.1fs",
                    artist_name, total_candidates, time.time() - parse_started_at)

    if new_candidate_ids or updated_count or deleted or hash_item_ids:
        logger.info("Artist [%s]: candidates=%d seen=%d missing=%d hash_items=%d",
                    artist_name, len(new_candidate_ids), updated_count, len(deleted), len(hash_item_ids))

    return


def start_background_scanner():
    global _scan_thread
    if SCAN_INTERVAL <= 0:
        return

    _scan_thread = threading.Thread(target=_background_scan_loop, daemon=True)
    _scan_thread.start()


def _wait_until_next_auto_scan(sleep_fn=None, time_fn=time.time):
    while True:
        with _schedule_condition:
            target = _next_auto_scan_at
            if target is None:
                return
            remaining = target - time_fn()
            if remaining <= 0:
                return
            if sleep_fn is None:
                _schedule_condition.wait(remaining)
                continue
        sleep_fn(remaining)


def _background_scan_loop(scan_fn=scan_artists, sleep_fn=None, time_fn=time.time):
    if not SCAN_ON_START:
        logger.info("Background scanner waiting %d seconds before first automatic scan", SCAN_INTERVAL)
        _set_next_auto_scan_at(time_fn() + SCAN_INTERVAL)
    else:
        _set_next_auto_scan_at(time_fn())

    while True:
        _wait_until_next_auto_scan(sleep_fn=sleep_fn, time_fn=time_fn)
        try:
            scan_fn()
        except Exception:
            logger.exception("Background scan failed")
        _set_next_auto_scan_at(time_fn() + SCAN_INTERVAL)
