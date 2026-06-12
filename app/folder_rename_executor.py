from __future__ import annotations

import json
import os
import re
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

from app import database
from app.db_backup import create_db_backup
from app.folder_rename_planner import (
    PLAN_KIND_RENAME_FOLDER,
    PLAN_KIND_SPLIT_BY_TAG,
    build_target_name,
)
from app.folder_rename_execution_log import executed_target_folders as _executed_target_folders
from app.folder_utils import normalize_folder, normalize_slashes
from app.role_extractor import extract_date

ACTION_KIND_TAGGED_FILE = "tagged_file"

_FULL_DATE_RE = re.compile(r"(?<!\d)(20\d{2})[-._](0?[1-9]|1[0-2])[-._](0?[1-9]|[12]\d|3[01])(?!\d)")
_COMPACT_DATE_RE = re.compile(r"(?<!\d)(20\d{2})(0[1-9]|1[0-2])([0-2]\d|3[01])(?!\d)")
_MONTH_RE = re.compile(r"(?<!\d)(20\d{2})[-._](0?[1-9]|1[0-2])(?![-._]?\d)")


def _artist(artist_id: int):
    return database.get_db().execute(
        "SELECT id, name, path FROM artists WHERE id=?",
        (artist_id,),
    ).fetchone()


def _plan_rows(artist_id: int) -> list[dict[str, Any]]:
    rows = database.get_db().execute(
        """
        SELECT *
        FROM folder_rename_plans
        WHERE artist_id=? AND status='confirmed'
        ORDER BY source_folder
        """,
        (artist_id,),
    ).fetchall()
    executed_targets = _executed_target_folders(artist_id)
    return [
        dict(row)
        for row in rows
        if normalize_folder(row["source_folder"]) not in executed_targets
    ]


def _decode_selected_tag_ids(plan: dict[str, Any]) -> list[int]:
    try:
        raw = json.loads(plan.get("selected_tag_ids") or "[]")
    except (TypeError, ValueError):
        return []
    ids = []
    for value in raw:
        try:
            ids.append(int(value))
        except (TypeError, ValueError):
            continue
    return ids


def _plan_kind(plan: dict[str, Any]) -> str:
    value = str(plan.get("plan_kind") or PLAN_KIND_RENAME_FOLDER)
    return value if value in {PLAN_KIND_RENAME_FOLDER, PLAN_KIND_SPLIT_BY_TAG} else PLAN_KIND_RENAME_FOLDER


def _decode_split_actions(plan: dict[str, Any]) -> list[dict[str, Any]]:
    try:
        raw = json.loads(plan.get("split_actions") or "[]")
    except (TypeError, ValueError):
        return []
    return [action for action in raw if isinstance(action, dict)]


def _tag_names(artist_id: int, tag_ids: list[int]) -> list[str]:
    if not tag_ids:
        return []
    placeholders = ",".join("?" for _ in tag_ids)
    rows = database.get_db().execute(
        f"""
        SELECT id, name
        FROM tags
        WHERE artist_id=? AND id IN ({placeholders})
        ORDER BY sort_order, name, id
        """,
        [artist_id] + tag_ids,
    ).fetchall()
    by_id = {int(row["id"]): row["name"] for row in rows}
    return [by_id[tag_id] for tag_id in tag_ids if tag_id in by_id]


def _all_tagged_file_artist_ids() -> list[int]:
    rows = database.get_db().execute(
        """
        SELECT DISTINCT i.artist_id
        FROM items i
        JOIN item_tags it ON it.item_id = i.id
        WHERE i.missing=0
          AND (i.media_type IN ('image', 'video', 'source', 'archive') OR i.is_archive=1)
        ORDER BY i.artist_id
        """
    ).fetchall()
    return [int(row["artist_id"]) for row in rows]


def artist_ids_with_executable_work() -> list[int]:
    ids = set(_all_tagged_file_artist_ids())
    rows = database.get_db().execute(
        """
        SELECT DISTINCT artist_id
        FROM folder_rename_plans
        WHERE status='confirmed'
        """
    ).fetchall()
    ids.update(int(row["artist_id"]) for row in rows)
    return sorted(ids)


def _abs(path: str) -> str:
    return os.path.abspath(path)


def _real(path: str) -> str:
    return os.path.realpath(os.path.abspath(path))


def _is_under(path: str, root: str) -> bool:
    try:
        return os.path.commonpath([_real(path), _real(root)]) == _real(root)
    except ValueError:
        return False


def _join_path(root: str, relative_path: str) -> str:
    sep = "\\" if "\\" in root else "/"
    return root.rstrip("\\/") + sep + relative_path.replace("/", sep).replace("\\", sep)


def _path_key(path: str) -> str:
    return os.path.normcase(_real(path))


def _derived_path_fields(file_path: str) -> tuple[str, str]:
    folder_name = os.path.basename(os.path.dirname(file_path))
    return folder_name, extract_date(folder_name)


def _target_item_conflict(target: str, item_ids: list[int] | None = None) -> dict[str, str] | None:
    if not target:
        return None
    db = database.get_db()
    allowed_ids = {int(item_id) for item_id in (item_ids or []) if item_id is not None}
    row = db.execute(
        """
        SELECT id
        FROM items
        WHERE file_path=?
        LIMIT 1
        """,
        (target,),
    ).fetchone()
    if row and int(row["id"]) not in allowed_ids:
        return {"code": "target_item_exists", "message": f"Target item already exists in database: {target}"}
    return None


def _date_folder_from_part(part: str) -> str:
    for regex in (_FULL_DATE_RE, _COMPACT_DATE_RE):
        match = regex.search(part or "")
        if match:
            year, month, day = match.groups()
            return f"{year}-{int(month):02d}-{int(day):02d}"
    match = _MONTH_RE.search(part or "")
    if match:
        year, month = match.groups()
        return f"{year}-{int(month):02d}"
    return ""


def _target_folder_for_tagged_file(artist_path: str, file_path: str, tag_names: list[str]) -> str:
    if not tag_names:
        return ""
    artist_norm = normalize_slashes(os.path.abspath(artist_path)).rstrip("/")
    file_norm = normalize_slashes(os.path.abspath(file_path))
    prefix = artist_norm + "/"
    if not file_norm.startswith(prefix):
        return ""
    relative_file = file_norm[len(prefix):]
    parts = normalize_folder(relative_file).split("/")
    folder_parts = parts[:-1]
    for index in range(len(folder_parts) - 1, -1, -1):
        date_folder = _date_folder_from_part(folder_parts[index])
        if not date_folder:
            continue
        target_name = build_target_name(date_folder, tag_names)
        if index > 0:
            return normalize_slashes("/".join(folder_parts[:index] + [target_name]))
        return target_name
    return ""


def _item_tag_names(artist_id: int, item_ids: list[int]) -> dict[int, list[str]]:
    if not item_ids:
        return {}
    db = database.get_db()
    result: dict[int, list[str]] = defaultdict(list)
    for index in range(0, len(item_ids), 800):
        chunk = item_ids[index:index + 800]
        placeholders = ",".join("?" for _ in chunk)
        rows = db.execute(
            f"""
            SELECT it.item_id, t.name
            FROM item_tags it
            JOIN tags t ON t.id = it.tag_id
            WHERE t.artist_id=? AND it.item_id IN ({placeholders})
            ORDER BY t.sort_order, t.name, t.id
            """,
            [artist_id] + chunk,
        ).fetchall()
        for row in rows:
            result[int(row["item_id"])].append(row["name"])
    return result


def _is_under_any_folder(file_path: str, folders: list[str]) -> bool:
    for folder in folders:
        if _is_under(file_path, folder):
            return True
    return False


def _tagged_file_actions(
    artist_id: int,
    artist_path: str,
    *,
    excluded_source_folders: list[str] | None = None,
) -> list[dict[str, Any]]:
    rows = database.get_db().execute(
        """
        SELECT id, file_path, file_name, file_size, file_mtime, media_type, is_archive
        FROM items
        WHERE artist_id=? AND missing=0
          AND (media_type IN ('image', 'video', 'source', 'archive') OR is_archive=1)
        ORDER BY file_path
        """,
        (artist_id,),
    ).fetchall()
    tags_by_item = _item_tag_names(artist_id, [int(row["id"]) for row in rows])
    actions = []
    for row in rows:
        item_id = int(row["id"])
        if excluded_source_folders and _is_under_any_folder(row["file_path"], excluded_source_folders):
            continue
        tag_names = tags_by_item.get(item_id) or []
        if not tag_names:
            continue
        target_folder = _target_folder_for_tagged_file(artist_path, row["file_path"], tag_names)
        if not target_folder:
            continue
        target = _join_path(_join_path(artist_path, target_folder), row["file_name"])
        if _real(row["file_path"]) == _real(target):
            continue
        actions.append(
            {
                "kind": ACTION_KIND_TAGGED_FILE,
                "plan_id": None,
                "artist_id": artist_id,
                "artist_path": artist_path,
                "source": row["file_path"],
                "target": target,
                "source_folder": normalize_folder(os.path.dirname(normalize_slashes(os.path.relpath(row["file_path"], artist_path)))),
                "target_folder": target_folder,
                "plan": {},
                "stats": {
                    "file_count": 1,
                    "total_size": int(row["file_size"] or 0),
                    "max_mtime": float(row["file_mtime"] or 0),
                },
                "item_ids": [item_id],
                "items": [
                    {
                        "item_id": item_id,
                        "source_path": row["file_path"],
                        "target_path": target,
                        "relative_path": row["file_name"],
                    }
                ],
                "tag_names": tag_names,
                "warnings": [],
            }
        )
    return actions


def _folder_items(artist_id: int, source_folder: str) -> list[dict[str, Any]]:
    db = database.get_db()
    source = _abs(source_folder)
    prefix = source.rstrip(os.sep) + os.sep
    rows = db.execute(
        """
        SELECT id, file_path, file_name, file_size, file_mtime
        FROM items
        WHERE artist_id=? AND missing=0 AND is_archive=0
          AND media_type IN ('image', 'video', 'source')
        ORDER BY file_path
        """,
        (artist_id,),
    ).fetchall()
    items = []
    for row in rows:
        item_path = _abs(row["file_path"])
        if item_path == source or item_path.startswith(prefix):
            item = dict(row)
            item["relative_path"] = normalize_slashes(os.path.relpath(item_path, source))
            item["target_path"] = ""
            items.append(item)
    return items


def _folder_stats(items: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "file_count": len(items),
        "total_size": sum(int(item.get("file_size") or 0) for item in items),
        "max_mtime": max((float(item.get("file_mtime") or 0) for item in items), default=0),
    }


def _split_file_actions(artist_path: str, source: str, raw_actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    files = []
    for raw in raw_actions:
        source_relative = normalize_slashes(str(raw.get("source_relative_path") or ""))
        target_folder = normalize_slashes(str(raw.get("target_folder") or ""))
        target_relative = normalize_slashes(str(raw.get("target_relative_path") or source_relative))
        if not source_relative or not target_folder or not target_relative:
            continue
        try:
            item_id = int(raw.get("item_id")) if raw.get("item_id") is not None else None
        except (TypeError, ValueError):
            item_id = None
        files.append(
            {
                "item_id": item_id,
                "source_relative_path": source_relative,
                "target_folder": target_folder,
                "target_relative_path": target_relative,
                "source_path": _join_path(source, source_relative),
                "target_path": _join_path(_join_path(artist_path, target_folder), target_relative),
                "file_size": int(raw.get("file_size") or 0),
                "file_mtime": float(raw.get("file_mtime") or 0),
                "reason": str(raw.get("reason") or ""),
            }
        )
    return files


def _action_errors(action: dict[str, Any]) -> list[dict[str, str]]:
    errors = []
    artist_path = action["artist_path"]
    source = action["source"]
    if action.get("kind") == ACTION_KIND_TAGGED_FILE:
        target = action["target"]
        if not source:
            errors.append({"code": "missing_source_file", "message": "Source file is empty"})
        if source and not os.path.isfile(source):
            errors.append({"code": "source_missing", "message": f"Source file does not exist: {source}"})
        if source and not _is_under(source, artist_path):
            errors.append({"code": "source_outside_artist", "message": f"Source file is outside artist root: {source}"})
        if not target:
            errors.append({"code": "missing_target_file", "message": "Target file is empty"})
        if target and not _is_under(target, artist_path):
            errors.append({"code": "target_outside_artist", "message": f"Target file is outside artist root: {target}"})
        if target and os.path.exists(target):
            errors.append({"code": "target_exists", "message": f"Target file already exists: {target}"})
        target_item_error = _target_item_conflict(target, action.get("item_ids"))
        if target_item_error:
            errors.append(target_item_error)
        if source and target and os.path.abspath(source) == os.path.abspath(target):
            errors.append({"code": "same_source_target", "message": "Source and target are the same file"})
        return errors

    if action.get("kind") == PLAN_KIND_SPLIT_BY_TAG:
        target_paths: set[str] = set()
        target_folders: set[str] = set()
        if not source:
            errors.append({"code": "missing_source_folder", "message": "Source folder is empty"})
        if source and not os.path.isdir(source):
            errors.append({"code": "source_missing", "message": f"Source folder does not exist: {source}"})
        if source and not _is_under(source, artist_path):
            errors.append({"code": "source_outside_artist", "message": f"Source folder is outside artist root: {source}"})
        if not action.get("files"):
            errors.append({"code": "missing_split_actions", "message": "Split plan has no file actions"})
        for file_action in action.get("files", []):
            file_source = file_action["source_path"]
            file_target = file_action["target_path"]
            target_folder = _join_path(artist_path, file_action["target_folder"])
            if not _is_under(file_source, source):
                errors.append({"code": "split_source_outside_folder", "message": f"Split source is outside source folder: {file_source}"})
            if not os.path.isfile(file_source):
                errors.append({"code": "split_source_missing", "message": f"Split source file does not exist: {file_source}"})
            if not _is_under(file_target, artist_path):
                errors.append({"code": "target_outside_artist", "message": f"Split target is outside artist root: {file_target}"})
            if os.path.exists(file_target):
                errors.append({"code": "target_exists", "message": f"Split target already exists: {file_target}"})
            target_item_error = _target_item_conflict(
                file_target,
                [int(file_action["item_id"])] if file_action.get("item_id") else [],
            )
            if target_item_error:
                errors.append(target_item_error)
            if _real(file_target) in target_paths:
                errors.append({"code": "duplicate_target", "message": f"Duplicate split target file: {file_target}"})
            target_paths.add(_real(file_target))
            target_folders.add(_real(target_folder))
        for folder in target_folders:
            if os.path.exists(folder):
                errors.append({"code": "target_exists", "message": f"Split target folder already exists: {folder}"})
        if action["stats"]["file_count"] != int(action["plan"].get("file_count") or 0):
            errors.append({"code": "file_count_mismatch", "message": "Current file count does not match saved plan"})
        if action["stats"]["total_size"] != int(action["plan"].get("total_size") or 0):
            errors.append({"code": "total_size_mismatch", "message": "Current total size does not match saved plan"})
        if abs(action["stats"]["max_mtime"] - float(action["plan"].get("max_mtime") or 0)) > 0.001:
            errors.append({"code": "mtime_mismatch", "message": "Current max mtime does not match saved plan"})
        return errors

    target = action["target"]
    if not source:
        errors.append({"code": "missing_source_folder", "message": "Source folder is empty"})
    if not target:
        errors.append({"code": "missing_target_folder", "message": "Target folder is empty"})
    if source and not os.path.isdir(source):
        errors.append({"code": "source_missing", "message": f"Source folder does not exist: {source}"})
    if source and not _is_under(source, artist_path):
        errors.append({"code": "source_outside_artist", "message": f"Source folder is outside artist root: {source}"})
    if target and not _is_under(target, artist_path):
        errors.append({"code": "target_outside_artist", "message": f"Target folder is outside artist root: {target}"})
    if target and os.path.exists(target):
        errors.append({"code": "target_exists", "message": f"Target folder already exists: {target}"})
    for item in action.get("items", []):
        target_path = item.get("target_path") or _join_path(target, item.get("relative_path") or "")
        target_item_error = _target_item_conflict(
            target_path,
            [int(item["item_id"])] if item.get("item_id") else [],
        )
        if target_item_error:
            errors.append(target_item_error)
    if source and target and os.path.abspath(source) == os.path.abspath(target):
        errors.append({"code": "same_source_target", "message": "Source and target are the same folder"})
    if action["stats"]["file_count"] != int(action["plan"].get("file_count") or 0):
        errors.append({"code": "file_count_mismatch", "message": "Current file count does not match saved plan"})
    if action["stats"]["total_size"] != int(action["plan"].get("total_size") or 0):
        errors.append({"code": "total_size_mismatch", "message": "Current total size does not match saved plan"})
    if abs(action["stats"]["max_mtime"] - float(action["plan"].get("max_mtime") or 0)) > 0.001:
        errors.append({"code": "mtime_mismatch", "message": "Current max mtime does not match saved plan"})
    return errors


def _action_summary(action: dict[str, Any], errors: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    summary = {
        "plan_id": action.get("plan_id"),
        "artist_id": action.get("artist_id"),
        "kind": action.get("kind") or PLAN_KIND_RENAME_FOLDER,
        "source": action.get("source", ""),
        "target": action.get("target", ""),
        "source_folder": action.get("source_folder", ""),
        "target_folder": action.get("target_folder", ""),
        "file_count": int((action.get("stats") or {}).get("file_count") or 0),
        "total_size": int((action.get("stats") or {}).get("total_size") or 0),
    }
    if action.get("kind") == PLAN_KIND_SPLIT_BY_TAG:
        summary["targets"] = sorted({file["target_folder"] for file in action.get("files", [])})
    if action.get("kind") == ACTION_KIND_TAGGED_FILE:
        summary["item_id"] = int((action.get("item_ids") or [0])[0] or 0)
        summary["tags"] = list(action.get("tag_names") or [])
    if action.get("merge_group"):
        summary["merge_group"] = True
        summary["merge_group_size"] = int(action.get("merge_group_size") or 0)
    if errors:
        summary["errors"] = errors
    return summary


def _filesystem_relative_files(source: str) -> list[str]:
    files = []
    for root, dirs, names in os.walk(source):
        dirs.sort()
        for name in sorted(names):
            full_path = os.path.join(root, name)
            files.append(normalize_slashes(os.path.relpath(full_path, source)))
    return files


def _merge_group_errors(group: list[dict[str, Any]]) -> list[dict[str, Any]]:
    relative_sources: dict[str, list[str]] = defaultdict(list)
    for action in group:
        for relative_path in _filesystem_relative_files(action["source"]):
            relative_sources[relative_path].append(action["source_folder"])
    errors = []
    for relative_path, source_folders in sorted(relative_sources.items()):
        if len(source_folders) <= 1:
            continue
        errors.append(
            {
                "code": "duplicate_in_group",
                "message": f"Duplicate relative file in merge group: {relative_path}",
                "relative_path": relative_path,
                "source_folders": source_folders,
            }
        )
    return errors


def _target_keys(action: dict[str, Any]) -> set[str]:
    if action.get("kind") == PLAN_KIND_RENAME_FOLDER:
        keys = {
            _path_key(item["target_path"])
            for item in action.get("items", [])
            if item.get("target_path")
        }
        return keys or {_path_key(action["target"])}
    if action.get("kind") == ACTION_KIND_TAGGED_FILE:
        return {_path_key(action["target"])}
    return {
        _path_key(file["target_path"])
        for file in action.get("files", [])
        if file.get("target_path")
    }


def _resolve_target_conflicts(candidate_actions: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    ready_actions = []
    blocked_actions = []
    errors = []
    target_actions: dict[str, list[dict[str, Any]]] = defaultdict(list)
    rename_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for action in candidate_actions:
        keys = _target_keys(action)
        for key in keys:
            target_actions[key].append(action)
        if action.get("kind") == PLAN_KIND_RENAME_FOLDER:
            rename_groups[_path_key(action["target"])].append(action)
        ready_actions.append(action)

    blocked_ids = set()
    action_duplicate_errors: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for key, actions in sorted(target_actions.items()):
        unique_actions = list({id(action): action for action in actions}.values())
        if len(unique_actions) <= 1:
            continue
        if all(action.get("kind") == PLAN_KIND_RENAME_FOLDER for action in unique_actions):
            rename_targets = {_path_key(action["target"]) for action in unique_actions}
            if len(rename_targets) == 1:
                continue
        error = {"code": "duplicate_target", "message": f"Duplicate target file: {key}"}
        errors.append(error)
        for action in unique_actions:
            blocked_ids.add(id(action))
            action_duplicate_errors[id(action)].append(error)

    for group in rename_groups.values():
        if len(group) <= 1:
            continue
        group_errors = _merge_group_errors(group)
        if group_errors:
            errors.extend(group_errors)
            for action in group:
                blocked_ids.add(id(action))
                action_duplicate_errors[id(action)].extend(group_errors)
            continue
        for action in group:
            action["merge_group"] = True
            action["merge_group_size"] = len(group)

    for action in ready_actions:
        if id(action) in blocked_ids:
            blocked_actions.append(_action_summary(action, action_duplicate_errors[id(action)]))

    return [action for action in ready_actions if id(action) not in blocked_ids], blocked_actions, errors


def build_execution_plan(artist_id: int, *, dry_run: bool = True) -> dict[str, Any]:
    artist = _artist(artist_id)
    if not artist:
        return {
            "artist_id": artist_id,
            "artist": None,
            "dry_run": dry_run,
            "status": "conflict",
            "actions": [],
            "errors": [{"code": "artist_not_found", "message": "Artist not found"}],
        }

    candidate_actions = []
    errors = []
    blocked_actions = []
    plan_rows = _plan_rows(artist_id)
    plan_source_folders = [
        _abs(os.path.join(artist["path"], plan["source_folder"]))
        for plan in plan_rows
    ]
    for plan in plan_rows:
        kind = _plan_kind(plan)
        source = _abs(os.path.join(artist["path"], plan["source_folder"]))
        items = _folder_items(artist_id, source)
        stats = _folder_stats(items)
        if kind == PLAN_KIND_SPLIT_BY_TAG:
            files = _split_file_actions(artist["path"], source, _decode_split_actions(plan))
            action = {
                "kind": PLAN_KIND_SPLIT_BY_TAG,
                "plan_id": plan["id"],
                "artist_id": artist_id,
                "artist_path": artist["path"],
                "source": source,
                "source_folder": plan["source_folder"],
                "target": "",
                "target_folder": "",
                "plan": plan,
                "stats": stats,
                "item_ids": [int(file["item_id"]) for file in files if file.get("item_id")],
                "files": files,
                "items": [
                    {
                        "item_id": file.get("item_id"),
                        "source_path": file["source_path"],
                        "target_path": file["target_path"],
                        "relative_path": file["source_relative_path"],
                        "target_folder": file["target_folder"],
                    }
                    for file in files
                ],
                "warnings": [],
            }
        else:
            tag_ids = _decode_selected_tag_ids(plan)
            tag_names = _tag_names(artist_id, tag_ids)
            target_folder = plan.get("target_folder") or build_target_name(plan.get("parsed_date") or "", tag_names)
            target = _abs(os.path.join(artist["path"], target_folder))
            action = {
                "kind": PLAN_KIND_RENAME_FOLDER,
                "plan_id": plan["id"],
                "artist_id": artist_id,
                "artist_path": artist["path"],
                "source": source,
                "target": target,
                "source_folder": plan["source_folder"],
                "target_folder": target_folder,
                "plan": plan,
                "stats": stats,
                "item_ids": [int(item["id"]) for item in items],
                "items": [
                    {
                        "item_id": int(item["id"]),
                        "source_path": item["file_path"],
                        "target_path": "",
                        "relative_path": item["relative_path"],
                    }
                    for item in items
                ],
                "warnings": [],
            }
        action_errors = _action_errors(action)
        errors.extend(action_errors)
        if action_errors:
            blocked_actions.append(_action_summary(action, action_errors))
            continue
        if kind == PLAN_KIND_RENAME_FOLDER:
            for item in action["items"]:
                item["target_path"] = _join_path(action["target"], item["relative_path"])
        candidate_actions.append(action)
    for action in _tagged_file_actions(
        artist_id,
        artist["path"],
        excluded_source_folders=plan_source_folders,
    ):
        action_errors = _action_errors(action)
        errors.extend(action_errors)
        if action_errors:
            blocked_actions.append(_action_summary(action, action_errors))
            continue
        candidate_actions.append(action)

    ready_actions, target_blocked_actions, target_errors = _resolve_target_conflicts(candidate_actions)
    actions = ready_actions
    blocked_actions.extend(target_blocked_actions)
    errors.extend(target_errors)

    status = "ready" if actions and not errors else "conflict"
    if not actions and not errors:
        errors.append({"code": "no_confirmed_plans", "message": "No confirmed folder rename plans found"})
        status = "conflict"
    return {
        "artist_id": artist_id,
        "artist": {"id": artist["id"], "name": artist["name"], "path": artist["path"]},
        "dry_run": dry_run,
        "status": status,
        "actions": actions,
        "blocked_actions": blocked_actions,
        "errors": errors,
    }


def recheck_folder_rename_plan(plan_id: int) -> dict[str, Any]:
    db = database.get_db()
    row = db.execute(
        "SELECT id, artist_id, status FROM folder_rename_plans WHERE id=?",
        (int(plan_id),),
    ).fetchone()
    if not row:
        raise ValueError("Plan not found")
    if row["status"] != "confirmed":
        raise ValueError("Plan is not confirmed")

    artist_id = int(row["artist_id"])
    plan = build_execution_plan(artist_id, dry_run=True)
    for action in plan.get("actions", []):
        if int(action.get("plan_id") or 0) != int(plan_id):
            continue
        summary = _action_summary(action, [])
        summary["errors"] = []
        return {
            "plan_id": int(plan_id),
            "artist_id": artist_id,
            "status": "ready",
            "action": summary,
            "errors": [],
        }
    for action in plan.get("blocked_actions", []):
        if int(action.get("plan_id") or 0) != int(plan_id):
            continue
        errors = action.get("errors") or []
        return {
            "plan_id": int(plan_id),
            "artist_id": artist_id,
            "status": "blocked",
            "action": action,
            "errors": errors,
        }
    errors = plan.get("errors") or [{"code": "plan_not_found", "message": "Plan was not included in dry-run"}]
    return {
        "plan_id": int(plan_id),
        "artist_id": artist_id,
        "status": "blocked",
        "action": {
            "plan_id": int(plan_id),
            "artist_id": artist_id,
            "errors": errors,
        },
        "errors": errors,
    }


def _update_item_paths(artist_id: int, source: str, target: str) -> int:
    db = database.get_db()
    source_abs = _abs(source)
    target_abs = _abs(target)
    source_prefix = source_abs.rstrip(os.sep) + os.sep
    rows = db.execute(
        """
        SELECT id, file_path
        FROM items
        WHERE artist_id=? AND missing=0
        """,
        (artist_id,),
    ).fetchall()
    updates = []
    for row in rows:
        item_path = _abs(row["file_path"])
        if item_path == source_abs or item_path.startswith(source_prefix):
            relative = os.path.relpath(item_path, source_abs)
            target_path = _join_path(target_abs, relative)
            folder_name, date = _derived_path_fields(target_path)
            updates.append((target_path, folder_name, date, int(row["id"])))
    if updates:
        db.executemany("UPDATE items SET file_path=?, folder_name=?, date=? WHERE id=?", updates)
    return len(updates)


def _update_split_item_paths(files: list[dict[str, Any]]) -> int:
    updates = [
        (file["target_path"], *_derived_path_fields(file["target_path"]), int(file["item_id"]))
        for file in files
        if file.get("item_id")
    ]
    if updates:
        database.get_db().executemany("UPDATE items SET file_path=?, folder_name=?, date=? WHERE id=?", updates)
    return len(updates)


def _update_tagged_item_path(item_id: int, target: str) -> int:
    folder_name, date = _derived_path_fields(target)
    database.get_db().execute(
        "UPDATE items SET file_path=?, folder_name=?, date=? WHERE id=?",
        (target, folder_name, date, int(item_id)),
    )
    return 1


def _raise_target_item_conflicts(action: dict[str, Any]) -> None:
    targets = []
    if action.get("kind") == ACTION_KIND_TAGGED_FILE:
        targets.append((action["target"], action.get("item_ids") or []))
    elif action.get("kind") == PLAN_KIND_SPLIT_BY_TAG:
        targets.extend(
            (
                file["target_path"],
                [int(file["item_id"])] if file.get("item_id") else [],
            )
            for file in action.get("files", [])
        )
    else:
        targets.extend(
            (
                item.get("target_path") or _join_path(action["target"], item.get("relative_path") or ""),
                [int(item["item_id"])] if item.get("item_id") else [],
            )
            for item in action.get("items", [])
        )
    for target, item_ids in targets:
        conflict = _target_item_conflict(target, list(item_ids))
        if conflict:
            raise ValueError(conflict["message"])


def _remove_empty_dirs(root: str) -> None:
    if not os.path.isdir(root):
        return
    for current_root, dirs, _ in os.walk(root, topdown=False):
        for name in dirs:
            path = os.path.join(current_root, name)
            try:
                os.rmdir(path)
            except OSError:
                pass
    try:
        os.rmdir(root)
    except OSError:
        pass


def _move_folder_contents(source: str, target: str) -> list[str]:
    moved = []
    os.makedirs(target, exist_ok=True)
    for root, dirs, files in os.walk(source):
        dirs.sort()
        for name in sorted(files):
            source_path = os.path.join(root, name)
            relative_path = normalize_slashes(os.path.relpath(source_path, source))
            target_path = _join_path(target, relative_path)
            if os.path.exists(target_path):
                raise FileExistsError(f"Target file already exists: {target_path}")
            os.makedirs(os.path.dirname(target_path), exist_ok=True)
            os.replace(source_path, target_path)
            moved.append(relative_path)
    _remove_empty_dirs(source)
    return moved


def _record_execution(plan_id: int, log: dict[str, Any]) -> None:
    db = database.get_db()
    row = db.execute("SELECT execution_log FROM folder_rename_plans WHERE id=?", (plan_id,)).fetchone()
    try:
        history = json.loads(row["execution_log"] or "[]")
    except (TypeError, ValueError):
        history = []
    history.append(log)
    db.execute(
        """
        UPDATE folder_rename_plans
        SET status='executed', executed_at=?, execution_log=?
        WHERE id=?
        """,
        (time.time(), json.dumps(history, ensure_ascii=False), plan_id),
    )


def execute_prepared_action(action: dict[str, Any], backup_path: str, *, automatic: bool = False) -> dict[str, Any]:
    db = database.get_db()
    try:
        db.execute("BEGIN")
        if action.get("kind") == ACTION_KIND_TAGGED_FILE:
            source = action["source"]
            target = action["target"]
            _raise_target_item_conflicts(action)
            if os.path.exists(target):
                raise FileExistsError(f"Target file already exists: {target}")
            Path(target).parent.mkdir(parents=True, exist_ok=True)
            os.replace(source, target)
            item_id = int((action.get("item_ids") or [0])[0] or 0)
            updated_items = _update_tagged_item_path(item_id, target)
            log = {
                "at": time.time(),
                "kind": ACTION_KIND_TAGGED_FILE,
                "source": source,
                "target": target,
                "target_folder": action.get("target_folder", ""),
                "item_id": item_id,
                "tags": list(action.get("tag_names") or []),
                "updated_items": updated_items,
                "backup": backup_path,
            }
            result = {
                "plan_id": action.get("plan_id"),
                "artist_id": action["artist_id"],
                "kind": ACTION_KIND_TAGGED_FILE,
                "source": source,
                "target": target,
                "target_folder": action.get("target_folder", ""),
                "item_id": item_id,
                "tags": list(action.get("tag_names") or []),
                "updated_items": updated_items,
                "status": "executed",
            }
        elif action.get("kind") == PLAN_KIND_SPLIT_BY_TAG:
            _raise_target_item_conflicts(action)
            for file in action.get("files", []):
                Path(file["target_path"]).parent.mkdir(parents=True, exist_ok=True)
                os.replace(file["source_path"], file["target_path"])
            updated_items = _update_split_item_paths(action.get("files", []))
            # Untagged files are not part of split actions. This only removes
            # the source folder when every directory under it is empty.
            _remove_empty_dirs(action["source"])
            targets = sorted({file["target_folder"] for file in action.get("files", [])})
            log = {
                "at": time.time(),
                "kind": PLAN_KIND_SPLIT_BY_TAG,
                "source": action["source"],
                "target": "",
                "targets": targets,
                "updated_items": updated_items,
                "backup": backup_path,
            }
            result = {
                "plan_id": action["plan_id"],
                "artist_id": action["artist_id"],
                "kind": PLAN_KIND_SPLIT_BY_TAG,
                "source": action["source"],
                "target": "",
                "targets": targets,
                "updated_items": updated_items,
                "status": "executed",
            }
        else:
            source = action["source"]
            target = action["target"]
            _raise_target_item_conflicts(action)
            if action.get("merge_group"):
                _move_folder_contents(source, target)
            else:
                os.replace(source, target)
            updated_items = _update_item_paths(action["artist_id"], source, target)
            log = {
                "at": time.time(),
                "kind": PLAN_KIND_RENAME_FOLDER,
                "source": source,
                "target": target,
                "updated_items": updated_items,
                "backup": backup_path,
            }
            if action.get("merge_group"):
                log["merged"] = True
                log["merge_group_size"] = int(action.get("merge_group_size") or 0)
            result = {
                "plan_id": action["plan_id"],
                "artist_id": action["artist_id"],
                "kind": PLAN_KIND_RENAME_FOLDER,
                "source": source,
                "target": target,
                "updated_items": updated_items,
                "status": "executed",
            }
            if action.get("merge_group"):
                result["merged"] = True
        if automatic:
            log["automatic"] = True
        if action.get("plan_id") is not None:
            _record_execution(action["plan_id"], log)
        else:
            db.execute(
                """
                INSERT INTO move_history
                (item_id, artist_id, old_path, new_path, reason, status, applied_at)
                VALUES (?, ?, ?, ?, ?, 'applied', ?)
                """,
                (
                    int((action.get("item_ids") or [0])[0] or 0),
                    int(action["artist_id"]),
                    action.get("source", ""),
                    action.get("target", ""),
                    ACTION_KIND_TAGGED_FILE,
                    time.time(),
                ),
            )
        db.commit()
        return result
    except Exception as exc:
        db.rollback()
        return {
            "plan_id": action.get("plan_id"),
            "artist_id": action.get("artist_id"),
            "kind": action.get("kind") or PLAN_KIND_RENAME_FOLDER,
            "source": action.get("source", ""),
            "target": action.get("target", ""),
            "status": "failed",
            "error": {"code": "execution_failed", "message": str(exc)},
        }


def execute_folder_rename_plan(artist_id: int) -> dict[str, Any]:
    plan = build_execution_plan(artist_id, dry_run=False)
    if plan["status"] != "ready":
        return plan

    backup_path = ""
    try:
        backup_dir = create_db_backup()
        backup_path = str(backup_dir)
    except Exception as exc:
        plan["status"] = "conflict"
        plan["errors"].append({"code": "backup_failed", "message": str(exc)})
        return plan

    db = database.get_db()
    failed_actions = []
    executed_actions = []
    for action in plan["actions"]:
        result = execute_prepared_action(action, backup_path)
        if result["status"] == "executed":
            executed_actions.append(result)
        else:
            failed_actions.append(result)
    if not failed_actions:
        plan["executed"] = True
        plan["backup"] = backup_path
        plan["executed_actions"] = executed_actions
        return plan

    plan["status"] = "conflict"
    plan["executed"] = False
    plan["backup"] = backup_path
    plan["failed_actions"] = failed_actions
    plan["errors"].extend(result["error"] for result in failed_actions if result.get("error"))
    return plan
