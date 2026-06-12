import json
import os
from typing import Any

from app import database
from app.folder_utils import normalize_folder


def execution_log_entries(raw: str | None) -> list[dict[str, Any]]:
    try:
        entries = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return []
    if not isinstance(entries, list):
        return []
    return [entry for entry in entries if isinstance(entry, dict)]


def target_folder_from_execution(entry: dict[str, Any], fallback: str = "") -> str:
    if fallback:
        return normalize_folder(fallback)
    target = str(entry.get("target") or "")
    if not target:
        return ""
    return normalize_folder(os.path.basename(os.path.normpath(target)))


def target_folders_from_execution(entry: dict[str, Any], fallback: str = "") -> list[str]:
    targets = []
    target_folder = target_folder_from_execution(entry, fallback)
    if target_folder:
        targets.append(target_folder)
    raw_targets = entry.get("targets") or []
    if isinstance(raw_targets, list):
        for raw_target in raw_targets:
            target = normalize_folder(str(raw_target or ""))
            if target and target not in targets:
                targets.append(target)
    return targets


def executed_target_folders(artist_id: int) -> set[str]:
    rows = database.get_db().execute(
        """
        SELECT target_folder, execution_log
        FROM folder_rename_plans
        WHERE artist_id=? AND status='executed'
        """,
        (artist_id,),
    ).fetchall()
    targets: set[str] = set()
    for row in rows:
        if row["target_folder"]:
            targets.add(normalize_folder(row["target_folder"]))
        for entry in execution_log_entries(row["execution_log"]):
            for target_folder in target_folders_from_execution(entry):
                targets.add(target_folder)
    return targets
