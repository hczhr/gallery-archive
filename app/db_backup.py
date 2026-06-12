import json
import os
import shutil
import sqlite3
import threading
import time
from pathlib import Path

from app import database
from app.log import logger


DB_BACKUP_INTERVAL = int(os.environ.get("DB_BACKUP_INTERVAL", str(12 * 60 * 60)))
DB_BACKUP_RETENTION = int(os.environ.get("DB_BACKUP_RETENTION", "8"))
DB_BACKUP_ON_START = os.environ.get("DB_BACKUP_ON_START", "1").lower() not in (
    "0",
    "false",
    "no",
)
DB_BACKUP_START_DELAY = int(os.environ.get("DB_BACKUP_START_DELAY", "120"))
SQLITE_BACKUP_PAGES = int(os.environ.get("DB_BACKUP_PAGES", "1000"))
SQLITE_BACKUP_SLEEP = float(os.environ.get("DB_BACKUP_SLEEP", "0.05"))

_backup_thread = None
_backup_state_lock = threading.Lock()
_backup_wakeup = threading.Event()
_backup_state = {
    "started_at": None,
    "last_started_at": None,
    "last_finished_at": None,
    "last_success_at": None,
    "last_error": None,
    "last_backup_dir": None,
}
_BACKUP_STATE_DEFAULTS = dict(_backup_state)


def _update_backup_state(**values) -> None:
    with _backup_state_lock:
        _backup_state.update(values)


def _is_default_backup_request(db_path: Path | None, backup_root: Path | None) -> bool:
    if db_path is not None and Path(db_path) != Path(database.DB_PATH):
        return False
    if backup_root is not None and Path(backup_root) != _backup_root():
        return False
    return True


def _backup_label_at(path: Path) -> float | None:
    label = path.name[:15]
    try:
        return time.mktime(time.strptime(label, "%Y%m%d-%H%M%S"))
    except ValueError:
        return None


def _backup_created_at(path: Path) -> float:
    label_at = _backup_label_at(path)
    metadata_path = path / "metadata.json"
    if metadata_path.exists():
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            created_at = float(metadata.get("created_at") or 0)
            if created_at > 0:
                return created_at
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            pass
    if label_at is not None:
        return label_at
    db_path = path / "gallery.db"
    stat_target = db_path if db_path.exists() else path
    return stat_target.stat().st_mtime


def _backup_sort_at(path: Path) -> float:
    label_at = _backup_label_at(path)
    if label_at is not None:
        return label_at
    metadata_path = path / "metadata.json"
    if metadata_path.exists():
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            created_at = float(metadata.get("created_at") or 0)
            if created_at > 0:
                return created_at
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            pass
    db_path = path / "gallery.db"
    stat_target = db_path if db_path.exists() else path
    return stat_target.stat().st_mtime


def _latest_backup_snapshot(backup_root: Path | None = None) -> dict | None:
    root = Path(backup_root or _backup_root())
    if not root.exists():
        return None
    snapshots = []
    for path in root.iterdir():
        if not path.is_dir() or path.name.startswith("."):
            continue
        try:
            snapshots.append((_backup_sort_at(path), path))
        except OSError:
            continue
    if not snapshots:
        return None
    _, path = max(snapshots, key=lambda item: (item[0], item[1].name))
    created_at = _backup_created_at(path)
    return {"last_success_at": created_at, "last_backup_dir": str(path)}


def _sync_latest_backup_state_from_disk() -> None:
    snapshot = _latest_backup_snapshot()
    if not snapshot:
        return
    with _backup_state_lock:
        current_success = _backup_state.get("last_success_at") or 0
        current_dir = _backup_state.get("last_backup_dir")
        if current_dir == snapshot["last_backup_dir"] and current_success:
            return
        if current_success and current_success >= snapshot["last_success_at"]:
            return
        _backup_state.update(
            last_success_at=snapshot["last_success_at"],
            last_backup_dir=snapshot["last_backup_dir"],
        )


def _next_backup_run_at(state: dict) -> float | None:
    if DB_BACKUP_INTERVAL <= 0:
        return None
    started_at = state.get("started_at")
    last_success_at = state.get("last_success_at")
    if last_success_at:
        return last_success_at + DB_BACKUP_INTERVAL
    if started_at:
        first_delay = DB_BACKUP_START_DELAY
        if not DB_BACKUP_ON_START:
            first_delay += DB_BACKUP_INTERVAL
        return started_at + first_delay
    return None


def get_backup_status(now: float | None = None) -> dict:
    current = time.time() if now is None else now
    _sync_latest_backup_state_from_disk()
    with _backup_state_lock:
        state = {**_BACKUP_STATE_DEFAULTS, **_backup_state}
    enabled = DB_BACKUP_INTERVAL > 0
    started_at = state.get("started_at")
    next_run_at = _next_backup_run_at(state)

    seconds_until_next = None
    overdue = False
    if enabled and next_run_at is not None:
        remaining = next_run_at - current
        overdue = remaining <= 0
        seconds_until_next = 0 if overdue else round(remaining, 1)

    return {
        "enabled": enabled,
        "interval": DB_BACKUP_INTERVAL,
        "retention": DB_BACKUP_RETENTION,
        "on_start": DB_BACKUP_ON_START,
        "start_delay": DB_BACKUP_START_DELAY,
        "worker_started_at": started_at,
        "thread_alive": bool(_backup_thread and _backup_thread.is_alive()),
        "next_run_at": next_run_at,
        "seconds_until_next": seconds_until_next,
        "overdue": overdue,
        **state,
    }


def _timestamp() -> str:
    return time.strftime("%Y%m%d-%H%M%S")


def _backup_root() -> Path:
    raw = os.environ.get("DB_BACKUP_DIR")
    return Path(raw) if raw else Path(database.DATA_DIR) / "db-backups"


def _unique_backup_dir(backup_root: Path, label: str) -> Path:
    candidate = backup_root / label
    if not candidate.exists():
        return candidate
    index = 1
    while True:
        candidate = backup_root / f"{label}-{index}"
        if not candidate.exists():
            return candidate
        index += 1


def prune_old_backups(backup_root: Path, keep: int = DB_BACKUP_RETENTION) -> None:
    keep = max(1, int(keep))
    if not backup_root.exists():
        return
    backups = sorted(
        path for path in backup_root.iterdir()
        if path.is_dir() and not path.name.startswith(".")
    )
    for old in backups[:-keep]:
        shutil.rmtree(old)


def create_db_backup(
    *,
    db_path: Path | None = None,
    backup_root: Path | None = None,
    retention: int = DB_BACKUP_RETENTION,
    interval_label: str | None = None,
) -> Path:
    source_path = Path(db_path or database.DB_PATH)
    if not source_path.exists():
        raise FileNotFoundError(source_path)

    root = Path(backup_root or _backup_root())
    root.mkdir(parents=True, exist_ok=True)
    label = interval_label or _timestamp()
    target_dir = _unique_backup_dir(root, label)
    temp_dir = root / f".{target_dir.name}.tmp-{os.getpid()}-{threading.get_ident()}"
    temp_dir.mkdir(parents=True, exist_ok=False)
    temp_db = temp_dir / "gallery.db"

    source = None
    dest = None
    try:
        source = sqlite3.connect(f"file:{source_path}?mode=ro", uri=True, timeout=30)
        source.execute("PRAGMA busy_timeout=30000")
        dest = sqlite3.connect(str(temp_db), timeout=30)
        source.backup(dest, pages=max(1, SQLITE_BACKUP_PAGES), sleep=max(0.0, SQLITE_BACKUP_SLEEP))
        dest.commit()
        metadata = {
            "created_at": time.time(),
            "source": str(source_path),
            "size": temp_db.stat().st_size,
        }
        (temp_dir / "metadata.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        dest.close()
        dest = None
        source.close()
        source = None
        temp_dir.rename(target_dir)
    except Exception:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise
    finally:
        if dest is not None:
            dest.close()
        if source is not None:
            source.close()

    prune_old_backups(root, keep=retention)
    if _is_default_backup_request(db_path, backup_root):
        finished_at = time.time()
        _update_backup_state(
            last_finished_at=finished_at,
            last_success_at=finished_at,
            last_error=None,
            last_backup_dir=str(target_dir),
        )
        _backup_wakeup.set()
    return target_dir


def _run_backup_once(now: float | None = None) -> None:
    started_at = time.time() if now is None else now
    _update_backup_state(last_started_at=started_at)
    try:
        backup_dir = create_db_backup()
        finished_at = time.time() if now is None else now
        _update_backup_state(
            last_finished_at=finished_at,
            last_success_at=finished_at,
            last_error=None,
            last_backup_dir=str(backup_dir),
        )
        logger.info("DB backup complete: %s", backup_dir)
    except Exception as exc:
        finished_at = time.time() if now is None else now
        _update_backup_state(
            last_finished_at=finished_at,
            last_error=str(exc),
        )
        logger.exception("DB backup failed")


def start_background_db_backup() -> None:
    global _backup_thread
    if DB_BACKUP_INTERVAL <= 0 or _backup_thread is not None:
        return
    _update_backup_state(started_at=time.time(), last_error=None)

    def loop():
        logger.info(
            "DB backup worker started: interval=%s retention=%s start_delay=%s on_start=%s",
            DB_BACKUP_INTERVAL,
            DB_BACKUP_RETENTION,
            DB_BACKUP_START_DELAY,
            DB_BACKUP_ON_START,
        )
        _sync_latest_backup_state_from_disk()
        if DB_BACKUP_ON_START:
            if DB_BACKUP_START_DELAY > 0:
                time.sleep(DB_BACKUP_START_DELAY)
            _run_backup_once()
        while True:
            _sync_latest_backup_state_from_disk()
            status = get_backup_status()
            next_run_at = status.get("next_run_at")
            delay = DB_BACKUP_INTERVAL if next_run_at is None else max(0.0, float(next_run_at) - time.time())
            if delay > 0:
                _backup_wakeup.wait(delay)
                _backup_wakeup.clear()
                continue
            _run_backup_once()

    _backup_thread = threading.Thread(target=loop, daemon=True, name="db-backup")
    _backup_thread.start()
