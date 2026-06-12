import os
import logging
import logging.handlers
import json
import threading
import time
from pathlib import Path


def _env_int(name, default):
    raw = os.environ.get(name, "")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default

LOG_DIR = os.environ.get("DATA_DIR", os.path.join(os.path.dirname(__file__), "..", "data"))
LOG_BASE_DIR = os.path.join(LOG_DIR, "logs")
LOG_PATH = os.path.join(LOG_BASE_DIR, "gallery.log")
UI_LOG_PATH = os.path.join(LOG_BASE_DIR, "ui-actions.log")
LOG_MAX_BYTES = _env_int("LOG_MAX_BYTES", 10 * 1024 * 1024)
LOG_BACKUP_COUNT = _env_int("LOG_BACKUP_COUNT", 5)
UI_LOG_MAX_BYTES = _env_int("UI_LOG_MAX_BYTES", 2 * 1024 * 1024)
UI_LOG_BACKUP_COUNT = _env_int("UI_LOG_BACKUP_COUNT", 3)
LOG_RETENTION_DAYS = _env_int("LOG_RETENTION_DAYS", 14)
LOG_CLEANUP_INTERVAL = _env_int("LOG_CLEANUP_INTERVAL", 86400)
LOG_CLEANUP_FILE_NAMES = ("gallery.log", "ui-actions.log")
_log_cleanup_started = False

os.makedirs(LOG_BASE_DIR, exist_ok=True)

logger = logging.getLogger("gallery")
logger.setLevel(logging.INFO)

fh = logging.handlers.RotatingFileHandler(
    LOG_PATH, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUP_COUNT, encoding="utf-8"
)
fh.setLevel(logging.INFO)
fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))

sh = logging.StreamHandler()
sh.setLevel(logging.INFO)
sh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))

logger.addHandler(fh)
logger.addHandler(sh)

ui_actions_logger = logging.getLogger("gallery.ui_actions")
ui_actions_logger.setLevel(logging.INFO)
ui_actions_logger.propagate = False

ui_fh = logging.handlers.RotatingFileHandler(
    UI_LOG_PATH, maxBytes=UI_LOG_MAX_BYTES, backupCount=UI_LOG_BACKUP_COUNT, encoding="utf-8"
)
ui_fh.setLevel(logging.INFO)
ui_fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
ui_actions_logger.addHandler(ui_fh)


def _clean_ui_log_value(value, depth=0):
    if depth > 3:
        return str(value)[:120]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value[:500]
    if isinstance(value, list):
        return [_clean_ui_log_value(v, depth + 1) for v in value[:20]]
    if isinstance(value, dict):
        return {
            str(k)[:80]: _clean_ui_log_value(v, depth + 1)
            for k, v in list(value.items())[:30]
        }
    return str(value)[:200]


def record_ui_action(event, payload=None):
    event = str(event or "unknown")[:80]
    clean_payload = _clean_ui_log_value(payload or {})
    ui_actions_logger.info(
        "%s %s",
        event,
        json.dumps(clean_payload, ensure_ascii=False, sort_keys=True),
    )


def cleanup_old_log_files(log_dir=LOG_BASE_DIR, retention_days=LOG_RETENTION_DAYS, now=None):
    """Remove old rotated application logs, never the active log files."""
    root = Path(log_dir)
    if retention_days <= 0 or not root.exists():
        return []

    cutoff = (time.time() if now is None else now) - retention_days * 86400
    removed = []
    for name in LOG_CLEANUP_FILE_NAMES:
        for path in root.glob(f"{name}.*"):
            if not path.is_file():
                continue
            try:
                if path.stat().st_mtime >= cutoff:
                    continue
                path.unlink()
                removed.append(path)
            except OSError:
                logger.exception("Failed to clean old log file: %s", path)
    return removed


def _background_log_cleanup():
    while True:
        time.sleep(LOG_CLEANUP_INTERVAL)
        removed = cleanup_old_log_files()
        if removed:
            logger.info("Log cleanup removed %d old files", len(removed))


def start_background_log_cleanup():
    global _log_cleanup_started
    if _log_cleanup_started:
        return
    _log_cleanup_started = True
    removed = cleanup_old_log_files()
    if removed:
        logger.info("Log cleanup removed %d old files", len(removed))
    thread = threading.Thread(target=_background_log_cleanup, daemon=True)
    thread.start()
