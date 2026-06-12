from __future__ import annotations

import json
import time
from typing import Any

from app import database
from app.db_backup import create_db_backup
from app.folder_rename_executor import (
    artist_ids_with_executable_work,
    build_execution_plan,
    execute_prepared_action,
)


ENABLED_KEY = "folder_rename_auto_enabled"
LAST_RUN_KEY = "folder_rename_auto_last_run"


def _setting(key: str) -> str | None:
    row = database.get_db().execute(
        "SELECT value FROM app_settings WHERE key=?",
        (key,),
    ).fetchone()
    return row["value"] if row else None


def _set_setting(key: str, value: str) -> None:
    database.get_db().execute(
        """
        INSERT INTO app_settings (key, value, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(key) DO UPDATE SET
            value=excluded.value,
            updated_at=excluded.updated_at
        """,
        (key, value, time.time()),
    )
    database.get_db().commit()


def _last_run() -> dict[str, Any] | None:
    raw = _setting(LAST_RUN_KEY)
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _error_codes(errors: list[dict[str, Any]]) -> set[str]:
    return {str(error.get("code") or "") for error in errors if isinstance(error, dict)}


def _enrich_skipped_group(group: dict[str, Any]) -> dict[str, Any] | None:
    enriched = dict(group)
    try:
        artist_id = int(enriched.get("artist_id"))
    except (TypeError, ValueError):
        return enriched

    try:
        plan = build_execution_plan(artist_id, dry_run=True)
    except Exception:
        return enriched

    artist = plan.get("artist") or {}
    if artist:
        enriched.setdefault("artist_name", artist.get("name") or "")
        enriched.setdefault("artist_path", artist.get("path") or "")
    blocked_actions = plan.get("blocked_actions") or []
    if blocked_actions:
        enriched["actions"] = blocked_actions
        enriched["count"] = len(blocked_actions)
        return enriched
    plan_errors = plan.get("errors") or []
    if plan.get("actions") or "no_confirmed_plans" in _error_codes(plan_errors):
        return None
    return enriched


def _skipped_group_count(group: dict[str, Any]) -> int:
    actions = group.get("actions") or []
    if actions:
        return len(actions)
    return int(group.get("count") or 0)


def _refresh_summary_status(summary: dict[str, Any]) -> None:
    executed = int(summary.get("executed_count") or 0)
    skipped = int(summary.get("skipped_count") or 0)
    failed = int(summary.get("failed_count") or 0)
    errors = summary.get("errors") or []
    if errors and not executed:
        summary["status"] = "failed"
    elif executed and (skipped or failed or errors):
        summary["status"] = "partial"
    elif failed:
        summary["status"] = "failed"
    elif executed:
        summary["status"] = "executed"
    elif skipped:
        summary["status"] = "skipped"
    else:
        summary["status"] = "no_actions"


def _enrich_last_run(last_run: dict[str, Any] | None) -> dict[str, Any] | None:
    if not last_run:
        return last_run
    enriched = dict(last_run)
    skipped = []
    for group in last_run.get("skipped") or []:
        if not isinstance(group, dict):
            skipped.append(group)
            continue
        enriched_group = _enrich_skipped_group(group)
        if enriched_group is not None:
            skipped.append(enriched_group)
    enriched["skipped"] = skipped
    enriched["skipped_count"] = sum(_skipped_group_count(group) for group in skipped if isinstance(group, dict))
    _refresh_summary_status(enriched)
    return enriched


def get_folder_rename_auto_state() -> dict[str, Any]:
    return {
        "enabled": _setting(ENABLED_KEY) == "1",
        "last_run": _enrich_last_run(_last_run()),
    }


def set_folder_rename_auto_enabled(enabled: bool) -> dict[str, Any]:
    _set_setting(ENABLED_KEY, "1" if enabled else "0")
    return get_folder_rename_auto_state()


def _confirmed_artist_ids() -> list[int]:
    return artist_ids_with_executable_work()


def _artist_ids_for_scope(scope: str, artist_id: int | None) -> list[int] | None:
    if scope == "full":
        return _confirmed_artist_ids()
    if scope == "artist" and artist_id:
        return [int(artist_id)]
    return None


def _execute_action(action: dict[str, Any], backup_path: str) -> dict[str, Any]:
    return execute_prepared_action(action, backup_path, automatic=True)


def _record_last_run(summary: dict[str, Any]) -> dict[str, Any]:
    _set_setting(LAST_RUN_KEY, json.dumps(summary, ensure_ascii=False))
    return summary


def _run_folder_rename_for_artist_ids(scope: str, artist_id: int | None, artist_ids: list[int]) -> dict[str, Any]:
    actions: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for current_artist_id in artist_ids:
        plan = build_execution_plan(current_artist_id, dry_run=True)
        plan_actions = plan.get("actions") or []
        blocked_actions = plan.get("blocked_actions") or []
        artist = plan.get("artist") or {}
        actions.extend(plan_actions)
        skipped_count = len(blocked_actions)
        if skipped_count:
            skipped.append(
                {
                    "artist_id": current_artist_id,
                    "artist_name": artist.get("name") or "",
                    "artist_path": artist.get("path") or "",
                    "count": skipped_count,
                    "actions": blocked_actions,
                    "errors": plan.get("errors") or [],
                }
            )

    backup_path = ""
    backup_error = None
    if actions:
        try:
            backup_path = str(create_db_backup())
        except Exception as exc:
            backup_error = {"code": "backup_failed", "message": str(exc)}

    executed_actions: list[dict[str, Any]] = []
    failed_actions: list[dict[str, Any]] = []
    if backup_error is None:
        for action in actions:
            result = _execute_action(action, backup_path)
            if result["status"] == "executed":
                executed_actions.append(result)
            else:
                failed_actions.append(result)

    status = "no_actions"
    if backup_error:
        status = "failed"
    elif executed_actions and (skipped or failed_actions):
        status = "partial"
    elif executed_actions:
        status = "executed"
    elif skipped:
        status = "skipped"

    summary = {
        "status": status,
        "scope": scope,
        "artist_id": artist_id,
        "at": time.time(),
        "backup": backup_path,
        "executed_count": len(executed_actions),
        "skipped_count": sum(int(item["count"]) for item in skipped),
        "failed_count": len(failed_actions),
        "actions": executed_actions,
        "skipped": skipped,
        "failed": failed_actions,
        "errors": [backup_error] if backup_error else [],
    }
    return _record_last_run(summary)


def run_folder_rename_for_artist(artist_id: int) -> dict[str, Any]:
    current_artist_id = int(artist_id)
    return _run_folder_rename_for_artist_ids("manual_artist", current_artist_id, [current_artist_id])


def run_folder_rename_auto_after_scan(scope: str, artist_id: int | None = None) -> dict[str, Any]:
    if not get_folder_rename_auto_state()["enabled"]:
        return {
            "status": "disabled",
            "scope": scope,
            "artist_id": artist_id,
            "executed_count": 0,
            "skipped_count": 0,
            "failed_count": 0,
            "actions": [],
            "skipped": [],
            "errors": [],
        }

    artist_ids = _artist_ids_for_scope(scope, artist_id)
    if artist_ids is None:
        return {
            "status": "scope_skipped",
            "scope": scope,
            "artist_id": artist_id,
            "executed_count": 0,
            "skipped_count": 0,
            "failed_count": 0,
            "actions": [],
            "skipped": [],
            "errors": [],
        }

    return _run_folder_rename_for_artist_ids(scope, artist_id, artist_ids)
