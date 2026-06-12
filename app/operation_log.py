from __future__ import annotations

from pathlib import Path
from typing import Any

from app import database
from app.folder_rename_execution_log import execution_log_entries
from app.health import _recent_log_errors
from app.path_display import display_path


def _clamp_limit(value: int | None, default: int, maximum: int) -> int:
    try:
        parsed = int(value if value is not None else default)
    except (TypeError, ValueError):
        parsed = default
    return max(1, min(parsed, maximum))


def _artist_names() -> dict[int, str]:
    rows = database.get_db().execute("SELECT id, name FROM artists").fetchall()
    return {int(row["id"]): str(row["name"] or "") for row in rows}


def _move_history(limit: int, artist_names: dict[int, str]) -> list[dict[str, Any]]:
    rows = database.get_db().execute(
        """
        SELECT id, item_id, artist_id, old_path, new_path, reason, status, created_at, applied_at, reverted_at
        FROM move_history
        ORDER BY COALESCE(applied_at, created_at) DESC, id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    history = []
    for row in rows:
        at = float(row["applied_at"] or row["created_at"] or 0)
        old_path = str(row["old_path"] or "")
        new_path = str(row["new_path"] or "")
        artist_id = int(row["artist_id"])
        history.append({
            "id": f"move:{row['id']}",
            "kind": "move",
            "status": str(row["status"] or ""),
            "at": at,
            "artist_id": artist_id,
            "artist_name": artist_names.get(artist_id, ""),
            "source": old_path,
            "target": new_path,
            "display_source": display_path(old_path) if old_path else "",
            "display_target": display_path(new_path) if new_path else "",
            "reason": str(row["reason"] or ""),
            "item_id": int(row["item_id"]),
            "updated_items": 1 if row["status"] == "applied" else 0,
        })
    return history


def _folder_rename_history(limit: int, artist_names: dict[int, str]) -> list[dict[str, Any]]:
    rows = database.get_db().execute(
        """
        SELECT id, artist_id, source_folder, target_folder, status, executed_at, execution_log, plan_kind
        FROM folder_rename_plans
        WHERE executed_at IS NOT NULL OR execution_log != '[]'
        ORDER BY COALESCE(executed_at, updated_at, created_at) DESC, id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    history = []
    for row in rows:
        entries = execution_log_entries(row["execution_log"])
        if not entries:
            entries = [{}]
        for index, entry in enumerate(entries):
            source = str(entry.get("source") or row["source_folder"] or "")
            target = str(entry.get("target") or row["target_folder"] or "")
            targets = entry.get("targets") if isinstance(entry.get("targets"), list) else []
            try:
                at = float(entry.get("at") or row["executed_at"] or 0)
            except (TypeError, ValueError):
                at = float(row["executed_at"] or 0)
            try:
                updated_items = int(entry.get("updated_items") or 0)
            except (TypeError, ValueError):
                updated_items = 0
            artist_id = int(row["artist_id"])
            history.append({
                "id": f"folder_rename:{row['id']}:{index}",
                "kind": "folder_rename",
                "status": str(row["status"] or "executed"),
                "at": at,
                "artist_id": artist_id,
                "artist_name": artist_names.get(artist_id, ""),
                "source": source,
                "target": target,
                "display_source": display_path(source) if source else str(row["source_folder"] or ""),
                "display_target": display_path(target) if target else str(row["target_folder"] or ""),
                "target_folders": [str(target) for target in targets],
                "reason": str(row["plan_kind"] or "folder_rename"),
                "plan_id": int(row["id"]),
                "updated_items": updated_items,
                "backup": str(entry.get("backup") or ""),
            })
    return history


def get_operation_log(limit: int = 80, error_limit: int = 40) -> dict[str, Any]:
    limit = _clamp_limit(limit, 80, 300)
    error_limit = _clamp_limit(error_limit, 40, 120)
    artist_names = _artist_names()
    history = _move_history(limit, artist_names) + _folder_rename_history(limit, artist_names)
    history.sort(key=lambda entry: (entry.get("at") or 0, entry.get("id") or ""), reverse=True)
    log_dir = Path(database.DATA_DIR) / "logs"
    return {
        "history": history[:limit],
        "errors": _recent_log_errors(log_dir, limit=error_limit),
        "total": len(history),
        "limit": limit,
        "error_limit": error_limit,
        "sources": {
            "moves": "move_history",
            "folder_renames": "folder_rename_plans.execution_log",
            "errors": str(log_dir),
        },
    }
