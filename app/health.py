import os
from collections import deque
from pathlib import Path

from app import database
from app.db_backup import _backup_root, get_backup_status
from app.hash_worker import get_hash_status
from app.scanner import get_auto_scan_schedule, get_scan_state


ERROR_MARKERS = ("[ERROR]", "Traceback", "frontend_error", "frontend_rejection")


def _file_summary(path: Path) -> dict:
    if not path.exists():
        return {"path": str(path), "exists": False, "size_bytes": 0, "updated_at": None}
    stat = path.stat()
    return {
        "path": str(path),
        "exists": True,
        "size_bytes": stat.st_size,
        "updated_at": stat.st_mtime,
    }


def _latest_backups(limit: int = 5) -> tuple[list[dict], int]:
    root = _backup_root()
    if not root.exists():
        return [], 0
    dirs = sorted(
        (path for path in root.iterdir() if path.is_dir() and not path.name.startswith(".")),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    backups = []
    for path in dirs[:limit]:
        db_path = path / "gallery.db"
        stat_target = db_path if db_path.exists() else path
        stat = stat_target.stat()
        backups.append({
            "name": path.name,
            "path": str(path),
            "size_bytes": db_path.stat().st_size if db_path.exists() else 0,
            "updated_at": stat.st_mtime,
        })
    return backups, len(dirs)


def _recent_log_errors(log_dir: Path, limit: int = 8) -> list[dict]:
    errors = deque(maxlen=limit)
    for path in (log_dir / "gallery.log", log_dir / "ui-actions.log"):
        if not path.exists():
            continue
        try:
            with path.open("r", encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    if any(marker in line for marker in ERROR_MARKERS):
                        errors.append({"source": path.name, "line": line.rstrip()[:500]})
        except OSError as exc:
            errors.append({"source": path.name, "line": f"Failed to read log: {exc}"})
    return list(reversed(errors))


def get_health_summary() -> dict:
    log_dir = Path(database.DATA_DIR) / "logs"
    backups, backup_count = _latest_backups()
    try:
        hash_status = get_hash_status()
        hash_ok = not hash_status.get("database_error")
    except Exception as exc:
        hash_status = {"ok": False, "error": str(exc)}
        hash_ok = False

    try:
        scan_state = get_scan_state()
    except Exception as exc:
        scan_state = {"status": "error", "phase": str(exc)}
    try:
        scan_schedule = get_auto_scan_schedule()
    except Exception as exc:
        scan_schedule = {"enabled": False, "error": str(exc)}
    try:
        backup_schedule = get_backup_status()
    except Exception as exc:
        backup_schedule = {"enabled": False, "error": str(exc)}

    return {
        "ok": hash_ok,
        "database": _file_summary(Path(database.DB_PATH)),
        "backups": {
            "root": str(_backup_root()),
            "count": backup_count,
            "latest": backups[0] if backups else None,
            "recent": backups,
        },
        "logs": {
            "root": str(log_dir),
            "gallery_log": _file_summary(log_dir / "gallery.log"),
            "ui_actions_log": _file_summary(log_dir / "ui-actions.log"),
        },
        "recent_errors": _recent_log_errors(log_dir),
        "scan": scan_state,
        "scan_schedule": scan_schedule,
        "backup_schedule": backup_schedule,
        "hash": hash_status,
        "process": {"pid": os.getpid()},
    }
