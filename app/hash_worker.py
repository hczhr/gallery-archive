import os
import sqlite3
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

from app.database import close_db, get_db
from app.fingerprint import hash_file, is_blake3_available, stat_path
from app.log import logger


HASH_WORKERS = int(os.environ.get("HASH_WORKERS", str(min(4, os.cpu_count() or 1))))
BLAKE3_THREADS = int(os.environ.get("BLAKE3_THREADS", "1"))
HASH_INTERVAL = int(os.environ.get("HASH_INTERVAL", "30"))
HASH_BATCH_SIZE = int(os.environ.get("HASH_BATCH_SIZE", "500"))
HASH_RESOLVE_BATCH_SIZE = int(os.environ.get("HASH_RESOLVE_BATCH_SIZE", str(max(HASH_BATCH_SIZE, 1000))))
MOVE_DRY_RUN = os.environ.get("MOVE_DRY_RUN", "1").lower() not in ("0", "false", "no")
AUTO_MOVE_INODE = os.environ.get("AUTO_MOVE_INODE", "1").lower() not in ("0", "false", "no")
AUTO_MOVE_HASH_UNIQUE = os.environ.get("AUTO_MOVE_HASH_UNIQUE", "1").lower() not in ("0", "false", "no")

_hash_thread = None
_hash_unavailable_logged = False
_hash_state_lock = threading.Lock()
_hash_worker_state = {
    "started_at": None,
    "last_heartbeat_at": None,
    "last_batch_started_at": None,
    "last_batch_finished_at": None,
    "last_result": None,
    "last_error": None,
    "batches": 0,
}


def _update_hash_worker_state(**values):
    with _hash_state_lock:
        _hash_worker_state.update(values)


def _summarize_hash_batch_result(result: dict | None) -> dict | None:
    if not result:
        return None
    return {k: v for k, v in result.items() if k != "status"}


def _hash_batch_progress(result: dict | None) -> int:
    if not result:
        return 0
    total = 0
    for key in ("pending_scan_candidates", "ready_scan_candidates", "scan_candidates", "items"):
        value = result.get(key) or {}
        if isinstance(value, dict):
            total += int(value.get("done") or 0)
    return total


def get_hash_worker_state() -> dict:
    with _hash_state_lock:
        state = dict(_hash_worker_state)
    state["thread_alive"] = bool(_hash_thread and _hash_thread.is_alive())
    now = time.time()
    heartbeat = state.get("last_heartbeat_at")
    state["idle_seconds"] = round(now - heartbeat, 1) if heartbeat else None
    return state


def _log_hash_unavailable_once():
    global _hash_unavailable_logged
    if _hash_unavailable_logged:
        return
    _hash_unavailable_logged = True
    logger.warning("BLAKE3 package is not installed; content hash worker is paused")


def is_scan_active() -> bool:
    db = get_db()
    row = db.execute("SELECT status FROM scan_state WHERE id=1").fetchone()
    return bool(row and row["status"] == "scanning")


def hash_item(item_id: int, blake3_threads: int = BLAKE3_THREADS) -> bool:
    if not is_blake3_available():
        _log_hash_unavailable_once()
        return False

    db = get_db()
    cur = db.execute(
        """
        UPDATE items
        SET hash_status='processing'
        WHERE id=? AND missing=0 AND hash_status NOT IN ('processing', 'done')
        """,
        (item_id,),
    )
    db.commit()
    if cur.rowcount != 1:
        return False

    row = db.execute(
        "SELECT id, file_path, missing FROM items WHERE id=?",
        (item_id,),
    ).fetchone()
    if not row or row["missing"]:
        return False

    try:
        digest = hash_file(row["file_path"], max_threads=blake3_threads)
        stat = stat_path(row["file_path"])
    except OSError:
        db.execute(
            """
            UPDATE items
            SET hash_status='error', hash_updated_at=?
            WHERE id=?
            """,
            (time.time(), item_id),
        )
        db.commit()
        return False
    except Exception:
        logger.exception("Failed to hash item %s", item_id)
        db.execute(
            "UPDATE items SET hash_status='error', hash_updated_at=? WHERE id=?",
            (time.time(), item_id),
        )
        db.commit()
        return False

    try:
        db.execute(
            """
            UPDATE items
            SET content_hash=?, hash_status='done', hash_updated_at=?,
                file_size=?, file_mtime=?, st_dev=?, st_ino=?
            WHERE id=?
            """,
            (
                digest,
                time.time(),
                stat.file_size,
                stat.file_mtime,
                stat.st_dev,
                stat.st_ino,
                item_id,
            ),
        )
        db.commit()
    except sqlite3.OperationalError:
        logger.exception("Failed to write hash result for item %s", item_id)
        try:
            db.rollback()
            db.execute(
                """
                UPDATE items
                SET hash_status='pending', hash_updated_at=?
                WHERE id=? AND hash_status='processing'
                """,
                (time.time(), item_id),
            )
            db.commit()
        except Exception:
            logger.exception("Failed to requeue hash item %s after write failure", item_id)
        return False
    try:
        from app.tag_propagation import propagate_hash_tags_for_item

        propagate_hash_tags_for_item(item_id)
    except Exception:
        logger.exception("Failed to propagate tags for hashed item %s", item_id)
    return True


def hash_scan_candidate(candidate_id: int, blake3_threads: int = BLAKE3_THREADS) -> bool:
    if not is_blake3_available():
        _log_hash_unavailable_once()
        return False

    db = get_db()
    cur = db.execute(
        """
        UPDATE scan_candidates
        SET hash_status='processing'
        WHERE id=? AND status IN ('pending', 'candidate')
          AND hash_status NOT IN ('processing', 'done')
        """,
        (candidate_id,),
    )
    db.commit()
    if cur.rowcount != 1:
        return False

    row = db.execute(
        "SELECT id, file_path, scan_id FROM scan_candidates WHERE id=? AND status IN ('pending', 'candidate')",
        (candidate_id,),
    ).fetchone()
    if not row:
        return False

    try:
        digest = hash_file(row["file_path"], max_threads=blake3_threads)
        stat = stat_path(row["file_path"])
    except OSError:
        db.execute(
            """
            UPDATE scan_candidates
            SET hash_status='error', resolved_at=?
            WHERE id=?
            """,
            (time.time(), candidate_id),
        )
        db.commit()
        return False
    except Exception:
        logger.exception("Failed to hash scan candidate %s", candidate_id)
        db.execute(
            "UPDATE scan_candidates SET hash_status='error', resolved_at=? WHERE id=?",
            (time.time(), candidate_id),
        )
        db.commit()
        return False

    db.execute(
        """
        UPDATE scan_candidates
        SET content_hash=?, hash_status='done',
            file_size=?, file_mtime=?, st_dev=?, st_ino=?
        WHERE id=?
        """,
        (
            digest,
            stat.file_size,
            stat.file_mtime,
            stat.st_dev,
            stat.st_ino,
            candidate_id,
        ),
    )
    db.execute(
        """
        UPDATE scan_seen
        SET content_hash=?, hash_status='done',
            file_size=?, file_mtime=?, st_dev=?, st_ino=?
        WHERE scan_id=? AND file_path=?
        """,
        (
            digest,
            stat.file_size,
            stat.file_mtime,
            stat.st_dev,
            stat.st_ino,
            row["scan_id"],
            row["file_path"],
        ),
    )
    db.commit()
    try:
        from app.move_matcher import resolve_scan_candidate

        resolve_scan_candidate(
            candidate_id,
            dry_run=MOVE_DRY_RUN,
            auto_move_inode=AUTO_MOVE_INODE,
            auto_move_hash_unique=AUTO_MOVE_HASH_UNIQUE,
        )
    except Exception:
        logger.exception("Failed to resolve hashed scan candidate %s", candidate_id)
        return False
    return True


def _run_parallel(ids: list[int], worker, max_workers: int, blake3_threads: int) -> dict:
    if not ids:
        return {"queued": 0, "done": 0}
    done = 0
    errors = 0

    def run(row_id: int):
        try:
            return worker(row_id, blake3_threads)
        finally:
            close_db()

    with ThreadPoolExecutor(max_workers=max(1, max_workers)) as pool:
        futures = [pool.submit(run, row_id) for row_id in ids]
        for future in as_completed(futures):
            try:
                if future.result():
                    done += 1
            except Exception:
                errors += 1
                logger.exception("Hash worker task failed")
    result = {"queued": len(ids), "done": done}
    if errors:
        result["errors"] = errors
    return result


def hash_pending_items(
    limit: int = 100,
    workers: int = HASH_WORKERS,
    blake3_threads: int = BLAKE3_THREADS,
) -> dict:
    if not is_blake3_available():
        _log_hash_unavailable_once()
        return {"queued": 0, "done": 0, "skipped": "blake3_unavailable"}
    if is_scan_active():
        return {"queued": 0, "done": 0, "skipped": "scan_active"}

    db = get_db()
    rows = db.execute(
        """
        SELECT id
        FROM items
        WHERE missing=0 AND hash_status IN ('pending', 'error')
        ORDER BY id
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return _run_parallel([row["id"] for row in rows], hash_item, workers, blake3_threads)


def hash_pending_scan_candidates(
    limit: int = 100,
    workers: int = HASH_WORKERS,
    blake3_threads: int = BLAKE3_THREADS,
) -> dict:
    if not is_blake3_available():
        _log_hash_unavailable_once()
        return {"queued": 0, "done": 0, "skipped": "blake3_unavailable"}
    if is_scan_active():
        return {"queued": 0, "done": 0, "skipped": "scan_active"}

    db = get_db()
    rows = db.execute(
        """
        SELECT sc.id
        FROM scan_candidates sc
        WHERE sc.status IN ('pending', 'candidate')
          AND sc.hash_status IN ('pending', 'error')
          AND NOT EXISTS (
              SELECT 1
              FROM move_candidates mc
              WHERE mc.scan_candidate_id = sc.id
                AND mc.status = 'pending'
          )
        ORDER BY sc.id
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return _run_parallel([row["id"] for row in rows], hash_scan_candidate, workers, blake3_threads)


def process_scan_candidates_now(
    candidate_ids: list[int],
    workers: int = HASH_WORKERS,
    blake3_threads: int = BLAKE3_THREADS,
) -> dict:
    ids = list(dict.fromkeys(candidate_ids))
    if not ids:
        return {"queued": 0, "done": 0}

    if is_blake3_available():
        return _run_parallel(ids, hash_scan_candidate, workers, blake3_threads)

    _log_hash_unavailable_once()
    resolved = 0
    from app.move_matcher import resolve_scan_candidate

    for candidate_id in ids:
        resolve_scan_candidate(
            candidate_id,
            dry_run=MOVE_DRY_RUN,
            auto_move_inode=AUTO_MOVE_INODE,
            auto_move_hash_unique=AUTO_MOVE_HASH_UNIQUE,
        )
        resolved += 1
    return {
        "queued": len(ids),
        "done": 0,
        "resolved_without_hash": resolved,
        "skipped": "blake3_unavailable",
    }


def process_item_hashes_now(
    item_ids: list[int],
    workers: int = HASH_WORKERS,
    blake3_threads: int = BLAKE3_THREADS,
) -> dict:
    ids = list(dict.fromkeys(item_ids))
    if not ids:
        return {"queued": 0, "done": 0}
    if not is_blake3_available():
        _log_hash_unavailable_once()
        return {"queued": len(ids), "done": 0, "skipped": "blake3_unavailable"}
    return _run_parallel(ids, hash_item, workers, blake3_threads)


def resolve_ready_scan_candidates(limit: int = HASH_BATCH_SIZE) -> dict:
    if is_scan_active():
        return {"queued": 0, "done": 0, "skipped": "scan_active"}

    db = get_db()
    rows = db.execute(
        """
        SELECT sc.id
        FROM scan_candidates sc
        WHERE sc.status IN ('pending', 'candidate')
          AND sc.hash_status='done'
          AND NOT EXISTS (
              SELECT 1
              FROM move_candidates mc
              WHERE mc.scan_candidate_id = sc.id
                AND mc.status = 'pending'
          )
        ORDER BY sc.id
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    ids = [row["id"] for row in rows]
    if not ids:
        return {"queued": 0, "done": 0}

    from app.move_matcher import resolve_scan_candidate

    done = 0
    for candidate_id in ids:
        if is_scan_active():
            return {"queued": len(ids), "done": done, "skipped": "scan_active"}
        try:
            result = resolve_scan_candidate(
                candidate_id,
                dry_run=MOVE_DRY_RUN,
                auto_move_inode=AUTO_MOVE_INODE,
                auto_move_hash_unique=AUTO_MOVE_HASH_UNIQUE,
            )
            if result.get("action") != "missing":
                done += 1
        except Exception:
            logger.exception("Failed to resolve ready scan candidate %s", candidate_id)
    return {"queued": len(ids), "done": done}


def resolve_pending_scan_candidates(limit: int = HASH_RESOLVE_BATCH_SIZE) -> dict:
    if is_scan_active():
        return {"queued": 0, "done": 0, "candidates": 0, "skipped": "scan_active"}

    db = get_db()
    rows = db.execute(
        """
        SELECT sc.id
        FROM scan_candidates sc
        WHERE sc.status IN ('pending', 'candidate')
          AND NOT EXISTS (
              SELECT 1
              FROM move_candidates mc
              WHERE mc.scan_candidate_id = sc.id
                AND mc.status = 'pending'
          )
        ORDER BY sc.id
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    ids = [row["id"] for row in rows]
    if not ids:
        return {"queued": 0, "done": 0, "candidates": 0}

    from app.move_matcher import resolve_scan_candidate

    done = 0
    candidates = 0
    for candidate_id in ids:
        if is_scan_active():
            return {"queued": len(ids), "done": done, "candidates": candidates, "skipped": "scan_active"}
        try:
            result = resolve_scan_candidate(
                candidate_id,
                dry_run=MOVE_DRY_RUN,
                auto_move_inode=AUTO_MOVE_INODE,
                auto_move_hash_unique=AUTO_MOVE_HASH_UNIQUE,
            )
            if result.get("action") == "candidate":
                candidates += 1
                continue
            if result.get("action") != "missing":
                done += 1
        except Exception:
            logger.exception("Failed to resolve pending scan candidate %s", candidate_id)
    return {"queued": len(ids), "done": done, "candidates": candidates}


def _hash_status_counts(table: str, where: str = "") -> dict[str, int]:
    db = get_db()
    query = f"SELECT hash_status, COUNT(*) AS count FROM {table}"
    if where:
        query += f" WHERE {where}"
    query += " GROUP BY hash_status"
    counts = {
        "pending": 0,
        "processing": 0,
        "done": 0,
        "error": 0,
    }
    for row in db.execute(query).fetchall():
        counts[row["hash_status"] or "pending"] = row["count"]
    counts["total"] = sum(counts.values())
    counts["remaining"] = counts["pending"] + counts["processing"] + counts["error"]
    return counts


def get_hash_status() -> dict:
    candidate_where = """
        status IN ('pending', 'candidate')
        AND NOT EXISTS (
            SELECT 1
            FROM move_candidates mc
            WHERE mc.scan_candidate_id = scan_candidates.id
              AND mc.status = 'pending'
        )
    """
    return {
        "blake3_available": is_blake3_available(),
        "workers": HASH_WORKERS,
        "blake3_threads": BLAKE3_THREADS,
        "interval": HASH_INTERVAL,
        "batch_size": HASH_BATCH_SIZE,
        "resolve_batch_size": HASH_RESOLVE_BATCH_SIZE,
        "worker": get_hash_worker_state(),
        "items": _hash_status_counts("items", "missing=0"),
        "scan_candidates": _hash_status_counts("scan_candidates", candidate_where),
    }


def run_hash_batch(limit: int = HASH_BATCH_SIZE) -> dict:
    if is_scan_active():
        return {
            "ok": True,
            "message": "scan_active",
            "skipped": "scan_active",
            "status": get_hash_status(),
        }

    pending = resolve_pending_scan_candidates(limit=HASH_RESOLVE_BATCH_SIZE)

    if not is_blake3_available():
        _log_hash_unavailable_once()
        return {
            "ok": False,
            "message": "blake3_unavailable",
            "pending_scan_candidates": pending,
            "status": get_hash_status(),
        }

    ready_before = resolve_ready_scan_candidates(limit=limit)
    scan_candidates = hash_pending_scan_candidates(limit=limit)
    ready_after = resolve_ready_scan_candidates(limit=limit)
    items = hash_pending_items(limit=limit)
    progress = (
        int(pending.get("done") or 0)
        + int(ready_before.get("done") or 0)
        + int(scan_candidates.get("done") or 0)
        + int(ready_after.get("done") or 0)
        + int(items.get("done") or 0)
    )
    return {
        "ok": True,
        "message": "hash_batch_progress" if progress else "hash_batch_idle",
        "pending_scan_candidates": pending,
        "ready_scan_candidates": {
            "queued": ready_before["queued"] + ready_after["queued"],
            "done": ready_before["done"] + ready_after["done"],
        },
        "scan_candidates": scan_candidates,
        "items": items,
        "status": get_hash_status(),
    }


def start_background_hash_worker():
    global _hash_thread
    if HASH_INTERVAL <= 0 or _hash_thread is not None:
        return

    def loop():
        logger.info(
            "Hash worker started: interval=%s batch_size=%s workers=%s blake3_threads=%s resolve_batch_size=%s",
            HASH_INTERVAL,
            HASH_BATCH_SIZE,
            HASH_WORKERS,
            BLAKE3_THREADS,
            HASH_RESOLVE_BATCH_SIZE,
        )
        while True:
            _update_hash_worker_state(
                last_heartbeat_at=time.time(),
                last_batch_started_at=time.time(),
            )
            try:
                result = run_hash_batch(limit=HASH_BATCH_SIZE)
                summary = _summarize_hash_batch_result(result)
                _update_hash_worker_state(
                    last_heartbeat_at=time.time(),
                    last_batch_finished_at=time.time(),
                    last_result=summary,
                    last_error=None,
                    batches=get_hash_worker_state().get("batches", 0) + 1,
                )
                if _hash_batch_progress(result):
                    logger.info("Hash worker batch: %s", summary)
            except Exception as exc:
                _update_hash_worker_state(
                    last_heartbeat_at=time.time(),
                    last_batch_finished_at=time.time(),
                    last_error=str(exc),
                    batches=get_hash_worker_state().get("batches", 0) + 1,
                )
                logger.exception("Hash worker failed")
            finally:
                close_db()
            time.sleep(HASH_INTERVAL)

    _update_hash_worker_state(started_at=time.time(), last_error=None)
    _hash_thread = threading.Thread(target=loop, daemon=True)
    _hash_thread.start()
