from pathlib import Path
import time

from app import database
from app.db_backup import create_db_backup


SECONDS_PER_DAY = 24 * 60 * 60
DEFAULT_MISSING_RETENTION_DAYS = 90
DEFAULT_SCAN_SEEN_RETENTION_DAYS = 7
DEFAULT_SCAN_CANDIDATE_RETENTION_DAYS = 30

ELIGIBLE_MISSING_ITEMS_SQL = """
SELECT i.id
FROM items i
WHERE i.missing=1
  AND i.missing_at IS NOT NULL
  AND i.missing_at <= ?
  AND NOT EXISTS (
    SELECT 1
    FROM move_candidates mc
    WHERE mc.item_id=i.id
      AND mc.status='pending'
  )
"""


def _cutoff(now: float, days: int) -> float:
    return now - max(0, int(days)) * SECONDS_PER_DAY


def _count(sql: str, params: list | tuple = ()) -> int:
    return int(database.get_db().execute(sql, params).fetchone()[0])


def _count_eligible_missing_items(missing_cutoff: float) -> int:
    return _count(f"SELECT COUNT(*) FROM ({ELIGIBLE_MISSING_ITEMS_SQL})", (missing_cutoff,))


def _count_eligible_item_tags(missing_cutoff: float) -> int:
    return _count(
        f"""
        SELECT COUNT(*)
        FROM item_tags
        WHERE item_id IN ({ELIGIBLE_MISSING_ITEMS_SQL})
        """,
        (missing_cutoff,),
    )


def _count_protected_missing_items(missing_cutoff: float) -> int:
    return _count(
        """
        SELECT COUNT(*)
        FROM items i
        WHERE i.missing=1
          AND i.missing_at IS NOT NULL
          AND i.missing_at <= ?
          AND EXISTS (
            SELECT 1
            FROM move_candidates mc
            WHERE mc.item_id=i.id
              AND mc.status='pending'
          )
        """,
        (missing_cutoff,),
    )


def _count_old_scan_seen(scan_seen_cutoff: float) -> int:
    return _count("SELECT COUNT(*) FROM scan_seen WHERE created_at <= ?", (scan_seen_cutoff,))


def _count_old_resolved_scan_candidates(scan_candidate_cutoff: float) -> int:
    return _count(
        """
        SELECT COUNT(*)
        FROM scan_candidates
        WHERE status='resolved'
          AND COALESCE(resolved_at, created_at) <= ?
        """,
        (scan_candidate_cutoff,),
    )


def vacuum_database() -> None:
    db = database.get_db()
    db.execute("VACUUM")
    db.commit()


def cleanup_database_lifecycle(
    *,
    missing_retention_days: int = DEFAULT_MISSING_RETENTION_DAYS,
    scan_seen_retention_days: int = DEFAULT_SCAN_SEEN_RETENTION_DAYS,
    scan_candidate_retention_days: int = DEFAULT_SCAN_CANDIDATE_RETENTION_DAYS,
    execute: bool = False,
    backup_before: bool = True,
    backup_root: Path | None = None,
    vacuum: bool = False,
    now: float | None = None,
) -> dict:
    db_path = Path(database.DB_PATH)
    if not db_path.exists():
        raise FileNotFoundError(db_path)

    now = time.time() if now is None else float(now)
    missing_cutoff = _cutoff(now, missing_retention_days)
    scan_seen_cutoff = _cutoff(now, scan_seen_retention_days)
    scan_candidate_cutoff = _cutoff(now, scan_candidate_retention_days)

    result = {
        "dry_run": not execute,
        "items": _count_eligible_missing_items(missing_cutoff),
        "item_tags": _count_eligible_item_tags(missing_cutoff),
        "protected_items": _count_protected_missing_items(missing_cutoff),
        "scan_seen": _count_old_scan_seen(scan_seen_cutoff),
        "scan_candidates": _count_old_resolved_scan_candidates(scan_candidate_cutoff),
        "missing_retention_days": int(missing_retention_days),
        "scan_seen_retention_days": int(scan_seen_retention_days),
        "scan_candidate_retention_days": int(scan_candidate_retention_days),
        "backup_dir": None,
        "vacuumed": False,
    }
    if not execute:
        return result

    db = database.get_db()
    db.commit()
    if backup_before:
        backup_dir = create_db_backup(db_path=db_path, backup_root=backup_root)
        result["backup_dir"] = str(backup_dir)

    db.execute(
        f"DELETE FROM item_tags WHERE item_id IN ({ELIGIBLE_MISSING_ITEMS_SQL})",
        (missing_cutoff,),
    )
    db.execute(
        f"DELETE FROM items WHERE id IN ({ELIGIBLE_MISSING_ITEMS_SQL})",
        (missing_cutoff,),
    )
    db.execute("DELETE FROM scan_seen WHERE created_at <= ?", (scan_seen_cutoff,))
    db.execute(
        """
        DELETE FROM scan_candidates
        WHERE status='resolved'
          AND COALESCE(resolved_at, created_at) <= ?
        """,
        (scan_candidate_cutoff,),
    )
    db.commit()

    if vacuum:
        vacuum_database()
        result["vacuumed"] = True

    return result
