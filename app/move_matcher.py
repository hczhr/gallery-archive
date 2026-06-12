import sqlite3
import time
from typing import Any

from app.database import get_db
from app.log import logger
from app.path_display import display_path
from app.role_extractor import media_type_for_file


def _row_dict(row) -> dict[str, Any] | None:
    return dict(row) if row else None


def _attach_display_move_paths(rows) -> list[dict[str, Any]]:
    db = get_db()

    def artist_context(artist_id: int | None) -> dict[str, Any] | None:
        if not artist_id:
            return None
        row = db.execute(
            "SELECT id, name, path FROM artists WHERE id=?",
            (artist_id,),
        ).fetchone()
        return dict(row) if row else None

    moves = []
    for row in rows:
        move = dict(row)
        old_path = move.get("old_path") or ""
        new_path = move.get("new_path") or ""
        move["display_old_path"] = display_path(old_path) if old_path else ""
        move["display_new_path"] = display_path(new_path) if new_path else ""

        item_artist_id = None
        if move.get("item_id"):
            item = db.execute(
                "SELECT artist_id FROM items WHERE id=?",
                (move["item_id"],),
            ).fetchone()
            if item:
                item_artist_id = item["artist_id"]

        candidate_artist_id = move.get("artist_id")
        if move.get("scan_candidate_id"):
            candidate = db.execute(
                "SELECT artist_id FROM scan_candidates WHERE id=?",
                (move["scan_candidate_id"],),
            ).fetchone()
            if candidate:
                candidate_artist_id = candidate["artist_id"]

        item_artist = artist_context(item_artist_id)
        candidate_artist = artist_context(candidate_artist_id)
        item_artist_name = item_artist["name"] if item_artist else ""
        candidate_artist_name = candidate_artist["name"] if candidate_artist else ""
        item_artist_path = item_artist["path"] if item_artist else ""
        candidate_artist_path = candidate_artist["path"] if candidate_artist else ""
        is_cross_artist = bool(
            item_artist_id
            and candidate_artist_id
            and item_artist_id != candidate_artist_id
        )

        move["item_artist_id"] = item_artist_id
        move["candidate_artist_id"] = candidate_artist_id
        move["item_artist_name"] = item_artist_name
        move["candidate_artist_name"] = candidate_artist_name
        move["item_artist_path"] = item_artist_path
        move["candidate_artist_path"] = candidate_artist_path
        move["display_item_artist_path"] = display_path(item_artist_path) if item_artist_path else ""
        move["display_candidate_artist_path"] = display_path(candidate_artist_path) if candidate_artist_path else ""
        move["is_cross_artist"] = is_cross_artist
        move["same_artist_name"] = bool(
            item_artist_name
            and candidate_artist_name
            and item_artist_name.casefold() == candidate_artist_name.casefold()
        )
        move["can_confirm"] = bool(move.get("item_id")) and not is_cross_artist
        moves.append(move)
    return moves


def _candidate(candidate_id: int) -> dict[str, Any] | None:
    row = get_db().execute(
        "SELECT * FROM scan_candidates WHERE id=?",
        (candidate_id,),
    ).fetchone()
    return _row_dict(row)


def _insert_move_history(
    item_id: int,
    artist_id: int,
    old_path: str,
    new_path: str,
    reason: str,
    status: str,
):
    db = get_db()
    applied_at = time.time() if status == "applied" else None
    db.execute(
        """
        INSERT INTO move_history
        (item_id, artist_id, old_path, new_path, reason, status, applied_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (item_id, artist_id, old_path, new_path, reason, status, applied_at),
    )


def _mark_scan_candidate(candidate_id: int, status: str):
    get_db().execute(
        "UPDATE scan_candidates SET status=?, resolved_at=? WHERE id=?",
        (status, time.time(), candidate_id),
    )


def _record_preview(item: dict[str, Any], candidate: dict[str, Any], reason: str) -> dict[str, Any]:
    db = get_db()
    _insert_move_history(
        item["id"],
        item["artist_id"],
        item["file_path"],
        candidate["file_path"],
        reason,
        "preview",
    )
    _mark_scan_candidate(candidate["id"], "previewed")
    db.execute(
        """
        UPDATE move_candidates
        SET status='previewed', resolved_at=?
        WHERE scan_candidate_id=? AND status='pending'
        """,
        (time.time(), candidate["id"]),
    )
    db.commit()
    return {"action": "preview", "item_id": item["id"], "reason": reason}


def _target_occupied(new_path: str, item_id: int | None = None) -> bool:
    db = get_db()
    if item_id is None:
        row = db.execute("SELECT id FROM items WHERE file_path=?", (new_path,)).fetchone()
    else:
        row = db.execute(
            "SELECT id FROM items WHERE file_path=? AND id != ?",
            (new_path, item_id),
        ).fetchone()
    return row is not None


def _mark_existing_item_for_candidate(candidate: dict[str, Any]) -> dict[str, Any] | None:
    db = get_db()
    existing = db.execute(
        "SELECT id FROM items WHERE file_path=?",
        (candidate["file_path"],),
    ).fetchone()
    if not existing:
        return None
    if candidate.get("id"):
        _mark_scan_candidate(candidate["id"], "resolved")
    db.execute(
        """
        UPDATE move_candidates
        SET status='resolved', resolved_at=?
        WHERE scan_candidate_id=? AND status='pending'
        """,
        (time.time(), candidate.get("id") or 0),
    )
    db.commit()
    return {"action": "existing", "item_id": existing["id"]}


def _apply_move(item: dict[str, Any], candidate: dict[str, Any], reason: str) -> dict[str, Any]:
    db = get_db()
    db.commit()
    db.execute("BEGIN IMMEDIATE")
    try:
        fresh = db.execute(
            "SELECT * FROM items WHERE id=?",
            (item["id"],),
        ).fetchone()
        if not fresh:
            db.rollback()
            return {"action": "stale", "reason": "item_missing"}
        if not fresh["missing"]:
            db.rollback()
            return {"action": "stale", "reason": "item_not_missing"}
        if _target_occupied(candidate["file_path"], item["id"]):
            db.rollback()
            return {"action": "candidate", "reason": "target_occupied"}

        db.execute(
            """
            UPDATE items
            SET file_path=?, file_name=?, file_size=?, file_mtime=?,
                folder_name=?, date=?, is_archive=?, media_type=?,
                content_hash=?, hash_status=?, hash_updated_at=?,
                st_dev=?, st_ino=?,
                missing=0, missing_at=NULL, scanned_at=strftime('%s','now')
            WHERE id=?
            """,
            (
                candidate["file_path"],
                candidate["file_name"],
                candidate["file_size"],
                candidate["file_mtime"],
                candidate["folder_name"],
                candidate["date"],
                candidate["is_archive"],
                candidate.get("media_type") or media_type_for_file(candidate["file_name"]) or "image",
                candidate["content_hash"],
                candidate["hash_status"],
                time.time() if candidate["hash_status"] == "done" else None,
                candidate["st_dev"],
                candidate["st_ino"],
                item["id"],
            ),
        )
        _insert_move_history(
            item["id"],
            item["artist_id"],
            fresh["file_path"],
            candidate["file_path"],
            reason,
            "applied",
        )
        _mark_scan_candidate(candidate["id"], "resolved")
        db.execute(
            """
            UPDATE move_candidates
            SET status='applied', resolved_at=?
            WHERE scan_candidate_id=? AND status='pending'
            """,
            (time.time(), candidate["id"]),
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    try:
        from app.tag_propagation import propagate_hash_tags_for_item

        propagate_hash_tags_for_item(item["id"])
    except Exception:
        logger.exception("Failed to propagate tags for moved item %s", item["id"])
    return {"action": "moved", "item_id": item["id"], "reason": reason}


def _create_new_item(candidate: dict[str, Any]) -> dict[str, Any]:
    db = get_db()
    if _target_occupied(candidate["file_path"]):
        existing = _mark_existing_item_for_candidate(candidate)
        if existing:
            return existing

    try:
        cur = db.execute(
            """
            INSERT INTO items
            (artist_id, file_path, file_name, file_size, file_mtime,
             folder_name, date, auto_role, tags, is_archive, media_type,
             content_hash, hash_status, hash_updated_at, st_dev, st_ino,
             missing, missing_at, scanned_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, '', '[]', ?, ?, ?, ?, ?, ?, ?, 0, NULL,
                    strftime('%s','now'))
            """,
            (
                candidate["artist_id"],
                candidate["file_path"],
                candidate["file_name"],
                candidate["file_size"],
                candidate["file_mtime"],
                candidate["folder_name"],
                candidate["date"],
                candidate["is_archive"],
                candidate.get("media_type") or media_type_for_file(candidate["file_name"]) or "image",
                candidate["content_hash"],
                candidate["hash_status"],
                time.time() if candidate["hash_status"] == "done" else None,
                candidate["st_dev"],
                candidate["st_ino"],
            ),
        )
    except sqlite3.IntegrityError:
        db.rollback()
        existing = _mark_existing_item_for_candidate(candidate)
        if existing:
            return existing
        raise
    item_id = cur.lastrowid
    _mark_scan_candidate(candidate["id"], "new")
    db.execute(
        """
        UPDATE move_candidates
        SET status='new', resolved_at=?
        WHERE scan_candidate_id=? AND status='pending'
        """,
        (time.time(), candidate["id"]),
    )
    db.commit()
    try:
        from app.tag_propagation import propagate_hash_tags_for_item

        propagate_hash_tags_for_item(item_id)
    except Exception:
        logger.exception("Failed to propagate tags for new item %s", item_id)
    return {"action": "new", "item_id": item_id}


def _create_move_candidate(
    candidate: dict[str, Any],
    item: dict[str, Any] | None,
    reason: str,
) -> dict[str, Any]:
    db = get_db()
    item_id = item["id"] if item else None
    old_path = item["file_path"] if item else ""
    existing = db.execute(
        """
        SELECT id FROM move_candidates
        WHERE status='pending'
          AND new_path=?
          AND COALESCE(item_id, 0)=COALESCE(?, 0)
          AND reason=?
        """,
        (candidate["file_path"], item_id, reason),
    ).fetchone()
    if existing:
        _mark_scan_candidate(candidate["id"], "candidate")
        db.commit()
        return {"action": "candidate", "id": existing["id"], "reason": reason}

    cur = db.execute(
        """
        INSERT INTO move_candidates
        (scan_candidate_id, item_id, artist_id, old_path, new_path,
         reason, content_hash, st_dev, st_ino)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            candidate["id"],
            item_id,
            candidate["artist_id"],
            old_path,
            candidate["file_path"],
            reason,
            candidate["content_hash"],
            candidate["st_dev"],
            candidate["st_ino"],
        ),
    )
    _mark_scan_candidate(candidate["id"], "candidate")
    db.commit()
    return {"action": "candidate", "id": cur.lastrowid, "reason": reason}


def _wait_for_hash(candidate: dict[str, Any], reason: str = "missing_hash_not_ready") -> dict[str, Any]:
    # Keep the scan candidate in the hash queue; this is not a manual decision.
    get_db().execute(
        """
        UPDATE scan_candidates
        SET status='pending', resolved_at=NULL
        WHERE id=? AND status IN ('pending', 'candidate')
        """,
        (candidate["id"],),
    )
    get_db().commit()
    return {"action": "waiting_hash", "reason": reason}


def _missing_inode_matches(candidate: dict[str, Any]) -> list[dict[str, Any]]:
    if not candidate["st_dev"] or not candidate["st_ino"]:
        return []
    rows = get_db().execute(
        """
        SELECT *
        FROM items
        WHERE artist_id=? AND missing=1 AND st_dev=? AND st_ino=?
        ORDER BY id
        """,
        (candidate["artist_id"], candidate["st_dev"], candidate["st_ino"]),
    ).fetchall()
    return [dict(row) for row in rows]


def _strip_category_prefix(segment: str) -> str:
    normalized = segment
    while normalized.startswith("-"):
        normalized = normalized[1:].lstrip()
    return normalized


def _category_normalized_path(path: str) -> str:
    parts = path.replace("\\", "/").split("/")
    if len(parts) <= 1:
        return path.replace("\\", "/")
    directories = [_strip_category_prefix(part) if part else part for part in parts[:-1]]
    return "/".join(directories + [parts[-1]])


def _missing_category_rename_matches(candidate: dict[str, Any]) -> list[dict[str, Any]]:
    target_path = _category_normalized_path(candidate["file_path"])
    rows = get_db().execute(
        """
        SELECT *
        FROM items
        WHERE artist_id=? AND missing=1
          AND file_name=? AND file_size=?
        ORDER BY id
        """,
        (candidate["artist_id"], candidate["file_name"], candidate["file_size"]),
    ).fetchall()
    return [
        dict(row)
        for row in rows
        if _category_normalized_path(row["file_path"]) == target_path
    ]


def _missing_hash_matches(candidate: dict[str, Any]) -> list[dict[str, Any]]:
    if candidate["hash_status"] != "done" or not candidate["content_hash"]:
        return []
    rows = get_db().execute(
        """
        SELECT *
        FROM items
        WHERE artist_id=? AND missing=1
          AND hash_status='done'
          AND content_hash=?
        ORDER BY id
        """,
        (candidate["artist_id"], candidate["content_hash"]),
    ).fetchall()
    return [dict(row) for row in rows]


def _cross_artist_hash_matches(candidate: dict[str, Any]) -> list[dict[str, Any]]:
    if candidate["hash_status"] != "done" or not candidate["content_hash"]:
        return []
    rows = get_db().execute(
        """
        SELECT *
        FROM items
        WHERE artist_id != ? AND missing=1
          AND hash_status='done'
          AND content_hash=?
        ORDER BY id
        """,
        (candidate["artist_id"], candidate["content_hash"]),
    ).fetchall()
    return [dict(row) for row in rows]


def _active_duplicate_count(candidate: dict[str, Any]) -> int:
    if candidate["hash_status"] != "done" or not candidate["content_hash"]:
        return 0
    return get_db().execute(
        """
        SELECT COUNT(*)
        FROM scan_seen ss
        JOIN items i
          ON i.artist_id = ss.artist_id
         AND i.file_path = ss.file_path
        WHERE ss.scan_id=?
          AND ss.artist_id=?
          AND ss.file_path != ?
          AND ss.hash_status='done'
          AND ss.content_hash=?
          AND i.missing=0
        """,
        (
            candidate["scan_id"],
            candidate["artist_id"],
            candidate["file_path"],
            candidate["content_hash"],
        ),
    ).fetchone()[0]


def _unhashed_missing_same_size(candidate: dict[str, Any]) -> dict[str, Any] | None:
    row = get_db().execute(
        """
        SELECT *
        FROM items
        WHERE artist_id=? AND missing=1
          AND hash_status != 'done'
          AND file_size=?
        ORDER BY id
        LIMIT 1
        """,
        (candidate["artist_id"], candidate["file_size"]),
    ).fetchone()
    return _row_dict(row)


def _missing_same_size(candidate: dict[str, Any]) -> dict[str, Any] | None:
    row = get_db().execute(
        """
        SELECT *
        FROM items
        WHERE artist_id=? AND missing=1 AND file_size=?
        ORDER BY id
        LIMIT 1
        """,
        (candidate["artist_id"], candidate["file_size"]),
    ).fetchone()
    return _row_dict(row)


def _cross_artist_missing_same_size(candidate: dict[str, Any]) -> dict[str, Any] | None:
    row = get_db().execute(
        """
        SELECT *
        FROM items
        WHERE artist_id != ? AND missing=1 AND file_size=?
        ORDER BY id
        LIMIT 1
        """,
        (candidate["artist_id"], candidate["file_size"]),
    ).fetchone()
    return _row_dict(row)


def resolve_scan_candidate(
    candidate_id: int,
    *,
    dry_run: bool = True,
    auto_move_inode: bool = True,
    auto_move_hash_unique: bool = True,
) -> dict[str, Any]:
    candidate = _candidate(candidate_id)
    if not candidate:
        return {"action": "missing"}
    if candidate["status"] not in ("pending", "candidate"):
        return {"action": candidate["status"]}

    existing = get_db().execute(
        "SELECT * FROM items WHERE file_path=?",
        (candidate["file_path"],),
    ).fetchone()
    if existing:
        item = dict(existing)
        get_db().execute(
            """
            UPDATE items
            SET missing=0, missing_at=NULL, file_size=?, file_mtime=?,
                media_type=?, st_dev=?, st_ino=?, scanned_at=strftime('%s','now')
            WHERE id=?
            """,
            (
                candidate["file_size"],
                candidate["file_mtime"],
                candidate.get("media_type") or media_type_for_file(candidate["file_name"]) or "image",
                candidate["st_dev"],
                candidate["st_ino"],
                item["id"],
            ),
        )
        _mark_scan_candidate(candidate["id"], "resolved")
        get_db().commit()
        return {"action": "existing", "item_id": item["id"]}

    inode_matches = _missing_inode_matches(candidate)
    if len(inode_matches) == 1:
        item = inode_matches[0]
        if auto_move_inode:
            if dry_run:
                return _record_preview(item, candidate, "inode")
            return _apply_move(item, candidate, "inode")
        return _create_move_candidate(candidate, item, "inode_untrusted")
    if len(inode_matches) > 1:
        for item in inode_matches:
            _create_move_candidate(candidate, item, "inode_untrusted")
        return {"action": "candidate", "reason": "inode_untrusted"}

    category_matches = _missing_category_rename_matches(candidate)
    if len(category_matches) == 1:
        item = category_matches[0]
        if dry_run:
            return _record_preview(item, candidate, "category_rename")
        return _apply_move(item, candidate, "category_rename")
    if len(category_matches) > 1:
        for item in category_matches:
            _create_move_candidate(candidate, item, "manual_needed")
        return {"action": "candidate", "reason": "manual_needed"}

    if candidate["hash_status"] != "done" or not candidate["content_hash"]:
        item = _missing_same_size(candidate)
        if item:
            return _wait_for_hash(candidate)
        item = _cross_artist_missing_same_size(candidate)
        if item:
            return _wait_for_hash(candidate)
        return _create_new_item(candidate)

    hash_matches = _missing_hash_matches(candidate)
    active_duplicates = _active_duplicate_count(candidate)
    if len(hash_matches) == 1 and active_duplicates == 0:
        item = hash_matches[0]
        if auto_move_hash_unique:
            if dry_run:
                return _record_preview(item, candidate, "hash_unique")
            return _apply_move(item, candidate, "hash_unique")
        return _create_move_candidate(candidate, item, "manual_needed")
    if len(hash_matches) == 1 and active_duplicates > 0:
        return _create_move_candidate(candidate, hash_matches[0], "hash_duplicate_active")
    if len(hash_matches) > 1:
        for item in hash_matches:
            _create_move_candidate(candidate, item, "hash_multiple_missing")
        return {"action": "candidate", "reason": "hash_multiple_missing"}

    cross_matches = _cross_artist_hash_matches(candidate)
    if cross_matches:
        for item in cross_matches:
            _create_move_candidate(candidate, item, "manual_needed")
        return {"action": "candidate", "reason": "manual_needed"}

    item = _unhashed_missing_same_size(candidate)
    if item:
        return _wait_for_hash(candidate)
    return _create_new_item(candidate)


def list_move_candidates(status: str = "pending") -> list[dict[str, Any]]:
    if status == "pending":
        rows = get_db().execute(
            """
            SELECT *
            FROM move_candidates
            WHERE status=?
              AND reason != 'missing_hash_not_ready'
            ORDER BY created_at, id
            """,
            (status,),
        ).fetchall()
    else:
        rows = get_db().execute(
            """
            SELECT *
            FROM move_candidates
            WHERE status=?
            ORDER BY created_at, id
            """,
            (status,),
        ).fetchall()
    return _attach_display_move_paths(rows)


def count_waiting_hash_candidates() -> int:
    row = get_db().execute(
        """
        SELECT COUNT(*)
        FROM (
            SELECT 's' || id AS key
            FROM scan_candidates
            WHERE status IN ('pending', 'candidate')
              AND hash_status != 'done'
            UNION
            SELECT
                CASE
                    WHEN scan_candidate_id IS NOT NULL THEN 's' || scan_candidate_id
                    ELSE 'm' || id
                END AS key
            FROM move_candidates
            WHERE status='pending'
              AND reason='missing_hash_not_ready'
        )
        """
    ).fetchone()
    return int(row[0] if row else 0)


def list_move_history(status: str | None = None) -> list[dict[str, Any]]:
    db = get_db()
    if status:
        rows = db.execute(
            "SELECT * FROM move_history WHERE status=? ORDER BY created_at, id",
            (status,),
        ).fetchall()
    else:
        rows = db.execute("SELECT * FROM move_history ORDER BY created_at, id").fetchall()
    return _attach_display_move_paths(rows)


def confirm_move_candidate(candidate_id: int) -> dict[str, Any]:
    db = get_db()
    row = db.execute(
        "SELECT * FROM move_candidates WHERE id=? AND status='pending'",
        (candidate_id,),
    ).fetchone()
    if not row:
        return {"action": "missing"}
    move = dict(row)
    if not move["item_id"]:
        return {"action": "missing", "reason": "no_item"}
    item = db.execute("SELECT * FROM items WHERE id=?", (move["item_id"],)).fetchone()
    if not item:
        return {"action": "missing", "reason": "item_missing"}
    if item["artist_id"] != move["artist_id"]:
        return {"action": "blocked", "reason": "cross_artist_manual_needed"}

    candidate = _candidate(move["scan_candidate_id"]) if move["scan_candidate_id"] else None
    if not candidate:
        candidate = {
            "id": 0,
            "artist_id": move["artist_id"],
            "file_path": move["new_path"],
            "file_name": move["new_path"].rsplit("/", 1)[-1],
            "file_size": 0,
            "file_mtime": 0,
            "folder_name": "",
            "date": "",
            "is_archive": 0,
            "media_type": media_type_for_file(move["new_path"]) or "image",
            "content_hash": move["content_hash"],
            "hash_status": "done" if move["content_hash"] else "pending",
            "st_dev": move["st_dev"],
            "st_ino": move["st_ino"],
        }

    result = _apply_move(dict(item), candidate, move["reason"])
    if result["action"] == "moved":
        db.execute(
            "UPDATE move_candidates SET status='applied', resolved_at=? WHERE id=?",
            (time.time(), candidate_id),
        )
        db.commit()
    return result


def mark_move_candidate_new(candidate_id: int) -> dict[str, Any]:
    db = get_db()
    row = db.execute(
        "SELECT * FROM move_candidates WHERE id=? AND status='pending'",
        (candidate_id,),
    ).fetchone()
    if not row:
        return {"action": "missing"}
    move = dict(row)
    candidate = _candidate(move["scan_candidate_id"]) if move["scan_candidate_id"] else None
    if not candidate:
        return {"action": "missing", "reason": "scan_candidate_missing"}
    result = _create_new_item(candidate)
    if result["action"] == "new":
        db.execute(
            "UPDATE move_candidates SET status='new', resolved_at=? WHERE id=?",
            (time.time(), candidate_id),
        )
        db.commit()
    return result


def ignore_move_candidate(candidate_id: int) -> dict[str, Any]:
    db = get_db()
    cur = db.execute(
        """
        UPDATE move_candidates
        SET status='ignored', resolved_at=?
        WHERE id=? AND status='pending'
        """,
        (time.time(), candidate_id),
    )
    db.commit()
    return {"action": "ignored", "updated": cur.rowcount}
