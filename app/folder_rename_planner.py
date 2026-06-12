import csv
import io
import json
import os
import re
import time
from collections import Counter, defaultdict
from typing import Any

from app.database import get_db
from app.folder_rename_execution_log import (
    executed_target_folders as _executed_target_folders,
    execution_log_entries as _execution_log_entries,
    target_folder_from_execution as _target_folder_from_execution,
    target_folders_from_execution as _target_folders_from_execution,
)
from app.folder_utils import normalize_folder, normalize_slashes, relative_folder_path

VALID_SOURCE_STATUSES = {"draft", "confirmed"}
FINAL_STATUSES = {"manual_review", "needs_tags", "ready", "conflict", "confirmed"}
PLAN_KIND_RENAME_FOLDER = "rename_folder"
PLAN_KIND_SPLIT_BY_TAG = "split_by_tag"
VALID_PLAN_KINDS = {PLAN_KIND_RENAME_FOLDER, PLAN_KIND_SPLIT_BY_TAG}
VALID_CONFIRMATION_SOURCES = {"", "manual", "auto", "split"}
UNCATEGORIZED_LABEL = "未分类"
TAGGABLE_MEDIA_TYPES = {"image", "video", "source"}

_DATE_PATTERNS = [
    re.compile(r"(?<!\d)(20\d{2})[-._](0?[1-9]|1[0-2])[-._](0?[1-9]|[12]\d|3[01])(?!\d)"),
    re.compile(r"(?<!\d)(20\d{2})(0[1-9]|1[0-2])([0-2]\d|3[01])(?!\d)"),
]
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
_WHITESPACE = re.compile(r"\s+")
_PATH_CHAR_REPLACEMENTS = {
    "\\": "＼",
    "/": "／",
    ":": "：",
    "*": "＊",
    "?": "？",
    '"': "＂",
    "<": "＜",
    ">": "＞",
    "|": "｜",
    "&": "＆",
}


def parse_folder_date(folder_name: str) -> dict[str, Any]:
    matches: list[tuple[int, str]] = []
    for pattern in _DATE_PATTERNS:
        for match in pattern.finditer(folder_name or ""):
            year, month, day = match.groups()
            matches.append((match.start(), f"{year}-{int(month):02d}-{int(day):02d}"))
    matches.sort(key=lambda item: item[0])
    warnings = []
    if len(matches) > 1:
        warnings.append("multiple_dates")
    if not matches:
        return {"date": "", "warnings": ["date_not_found"]}
    return {"date": matches[0][1], "warnings": warnings}


def sanitize_name_part(value: str) -> str:
    clean = _CONTROL_CHARS.sub("", value or "")
    clean = _WHITESPACE.sub(" ", clean).strip(" .")
    for source, replacement in _PATH_CHAR_REPLACEMENTS.items():
        clean = clean.replace(source, replacement)
    return clean.strip(" .")


def build_target_name(parsed_date: str, tag_names: list[str]) -> str:
    clean_tags = [sanitize_name_part(name) for name in tag_names if sanitize_name_part(name)]
    return f"{parsed_date}-{'&'.join(clean_tags)}" if clean_tags else parsed_date


def _artist(artist_id: int):
    return get_db().execute(
        "SELECT id, name, path FROM artists WHERE id=?",
        (artist_id,),
    ).fetchone()


def _top_source_folder(artist_path: str, file_path: str) -> str:
    folder = relative_folder_path(artist_path, file_path)
    if not folder:
        return ""
    return folder.split("/", 1)[0]


def _validate_source_folder(folder: str) -> str:
    source = normalize_folder(folder)
    parts = source.split("/") if source else []
    if not source or len(parts) != 1 or any(part in {"..", "."} for part in parts):
        raise ValueError("Bad source folder")
    return source


def _is_archive_item(item: dict[str, Any]) -> bool:
    return bool(item.get("is_archive")) or str(item.get("media_type") or "") == "archive"


def _is_taggable_item(item: dict[str, Any]) -> bool:
    return not _is_archive_item(item) and str(item.get("media_type") or "image") in TAGGABLE_MEDIA_TYPES


def _has_split_tags(item: dict[str, Any]) -> bool:
    return bool(item.get("tags")) and (_is_taggable_item(item) or _is_archive_item(item))


def _item_tags(artist_id: int, item_ids: list[int]) -> dict[int, list[dict[str, Any]]]:
    if not item_ids:
        return {}
    db = get_db()
    tags_by_item: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for index in range(0, len(item_ids), 800):
        chunk = item_ids[index:index + 800]
        placeholders = ",".join("?" for _ in chunk)
        rows = db.execute(
            f"""
            SELECT it.item_id, t.id, t.name, t.sort_order
            FROM item_tags it
            JOIN tags t ON t.id = it.tag_id
            WHERE t.artist_id=? AND it.item_id IN ({placeholders})
            ORDER BY t.sort_order, t.name, t.id
            """,
            [artist_id] + chunk,
        ).fetchall()
        for row in rows:
            tags_by_item[int(row["item_id"])].append(
                {
                    "id": int(row["id"]),
                    "name": row["name"],
                    "sort_order": row["sort_order"],
                }
            )
    return tags_by_item


def _folder_stats(artist_id: int) -> dict[str, dict[str, Any]]:
    db = get_db()
    artist = _artist(artist_id)
    if not artist:
        return {}
    rows = db.execute(
        """
        SELECT id, file_path, file_name, file_size, file_mtime, is_archive, media_type
        FROM items
        WHERE artist_id=? AND missing=0
          AND (is_archive=1 OR media_type IN ('image', 'video', 'source', 'archive'))
        ORDER BY file_path
        """,
        (artist_id,),
    ).fetchall()
    tags_by_item = _item_tags(artist_id, [int(row["id"]) for row in rows])
    by_folder: dict[str, dict[str, Any]] = {}
    for row in rows:
        source = _top_source_folder(artist["path"], row["file_path"])
        if not source:
            continue
        stats = by_folder.setdefault(
            source,
            {
                "source_folder": source,
                "original_folder_name": source,
                "original_title": source,
                "file_count": 0,
                "total_size": 0,
                "max_mtime": 0,
                "relative_files": [],
                "folder_tags": [],
                "tagged_file_count": 0,
                "untagged_file_count": 0,
                "archive_count": 0,
                "items": [],
                "_folder_tag_counts": {},
            },
        )
        item_folder = relative_folder_path(artist["path"], row["file_path"])
        rel = item_folder.split("/", 1)[1] if "/" in item_folder else ""
        relative_path = normalize_slashes(os.path.join(rel, row["file_name"])) if rel else row["file_name"]
        item = {
            "id": int(row["id"]),
            "file_path": row["file_path"],
            "file_name": row["file_name"],
            "file_size": int(row["file_size"] or 0),
            "file_mtime": float(row["file_mtime"] or 0),
            "is_archive": int(row["is_archive"] or 0),
            "media_type": row["media_type"],
            "relative_path": relative_path,
            "tags": tags_by_item.get(int(row["id"]), []),
        }
        stats["items"].append(item)
        if _is_archive_item(item):
            stats["archive_count"] += 1
            if item["tags"]:
                for tag in item["tags"]:
                    tag_id = int(tag["id"])
                    entry = stats["_folder_tag_counts"].setdefault(
                        tag_id,
                        {
                            "id": tag_id,
                            "name": tag["name"],
                            "sort_order": tag.get("sort_order") or 0,
                            "file_count": 0,
                        },
                    )
                    entry["file_count"] += 1
            continue
        if not _is_taggable_item(item):
            continue
        stats["file_count"] += 1
        stats["total_size"] += item["file_size"]
        stats["max_mtime"] = max(float(stats["max_mtime"] or 0), item["file_mtime"])
        stats["relative_files"].append(relative_path)
        if item["tags"]:
            stats["tagged_file_count"] += 1
            for tag in item["tags"]:
                tag_id = int(tag["id"])
                entry = stats["_folder_tag_counts"].setdefault(
                    tag_id,
                    {
                        "id": tag_id,
                        "name": tag["name"],
                        "sort_order": tag.get("sort_order") or 0,
                        "file_count": 0,
                    },
                )
                entry["file_count"] += 1
        else:
            stats["untagged_file_count"] += 1
    for stats in by_folder.values():
        stats["folder_tags"] = sorted(
            stats["_folder_tag_counts"].values(),
            key=lambda tag: (tag.get("sort_order") or 0, tag["name"], tag["id"]),
        )
        del stats["_folder_tag_counts"]
    return by_folder


def _plan_rows(artist_id: int) -> dict[str, dict[str, Any]]:
    rows = get_db().execute(
        "SELECT * FROM folder_rename_plans WHERE artist_id=?",
        (artist_id,),
    ).fetchall()
    return {row["source_folder"]: dict(row) for row in rows}


def _leftover_files(source: str, limit: int = 50) -> dict[str, Any]:
    if not source or not os.path.isdir(source):
        return {"count": 0, "files": []}
    files = []
    count = 0
    try:
        for root, dirs, names in os.walk(source):
            dirs.sort()
            for name in sorted(names):
                full_path = os.path.join(root, name)
                count += 1
                if len(files) >= limit:
                    continue
                try:
                    stat = os.stat(full_path)
                except OSError:
                    stat = None
                files.append(
                    {
                        "relative_path": normalize_slashes(os.path.relpath(full_path, source)),
                        "size": int(stat.st_size) if stat else 0,
                        "mtime": float(stat.st_mtime) if stat else 0,
                    }
                )
    except OSError:
        return {"count": 0, "files": []}
    return {"count": count, "files": files}


def _execution_history(artist_id: int, limit: int = 20) -> list[dict[str, Any]]:
    rows = get_db().execute(
        """
        SELECT id, source_folder, target_folder, executed_at, execution_log
        FROM folder_rename_plans
        WHERE artist_id=? AND executed_at IS NOT NULL
        ORDER BY executed_at DESC, id DESC
        LIMIT ?
        """,
        (artist_id, max(1, min(int(limit or 20), 100))),
    ).fetchall()
    history = []
    for row in rows:
        entries = _execution_log_entries(row["execution_log"])
        if not entries:
            entries = [{}]
        for entry in reversed(entries):
            source = str(entry.get("source") or "")
            target = str(entry.get("target") or "")
            target_folders = _target_folders_from_execution(entry, row["target_folder"])
            target_folder = target_folders[0] if len(target_folders) == 1 else _target_folder_from_execution(entry, row["target_folder"])
            leftovers = _leftover_files(source)
            try:
                updated_items = int(entry.get("updated_items") or 0)
            except (TypeError, ValueError):
                updated_items = 0
            try:
                at = float(entry.get("at") or row["executed_at"] or 0)
            except (TypeError, ValueError):
                at = 0
            history.append(
                {
                    "plan_id": int(row["id"]),
                    "source_folder": row["source_folder"],
                    "target_folder": target_folder,
                    "executed_at": float(row["executed_at"] or at or 0),
                    "at": at,
                    "source": source,
                    "target": target,
                    "target_folders": target_folders,
                    "updated_items": updated_items,
                    "backup": str(entry.get("backup") or ""),
                    "leftover_count": leftovers["count"],
                    "leftover_files": leftovers["files"],
                }
            )
    history.sort(key=lambda entry: (entry["at"], entry["plan_id"]), reverse=True)
    return history[:limit]


def _valid_tags(artist_id: int, tag_ids: list[int]) -> list[dict[str, Any]]:
    if not tag_ids:
        return []
    ids = []
    seen = set()
    for tag_id in tag_ids:
        try:
            value = int(tag_id)
        except (TypeError, ValueError):
            continue
        if value not in seen:
            ids.append(value)
            seen.add(value)
    if not ids:
        return []
    placeholders = ",".join("?" * len(ids))
    rows = get_db().execute(
        f"""
        SELECT id, name, sort_order FROM tags
        WHERE artist_id=? AND id IN ({placeholders})
        """,
        [artist_id] + ids,
    ).fetchall()
    by_id = {int(row["id"]): dict(row) for row in rows}
    return [by_id[tag_id] for tag_id in ids if tag_id in by_id]


def _decode_selected_tag_ids(plan: dict[str, Any] | None) -> list[int]:
    if not plan:
        return []
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


def _decode_split_actions(plan: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not plan:
        return []
    try:
        raw = json.loads(plan.get("split_actions") or "[]")
    except (TypeError, ValueError):
        return []
    return [action for action in raw if isinstance(action, dict)]


def _relative_dir(relative_path: str) -> str:
    normalized = normalize_slashes(relative_path)
    return normalized.rsplit("/", 1)[0] if "/" in normalized else ""


def _relative_stem(relative_path: str) -> str:
    name = os.path.basename(normalize_slashes(relative_path))
    return os.path.splitext(name)[0].lower()


def _target_from_tags(parsed_date: str, tags: list[dict[str, Any]]) -> str:
    return build_target_name(parsed_date, [tag["name"] for tag in tags]) if parsed_date and tags else ""


def _build_split_preview(stats: dict[str, Any], parsed_date: str) -> dict[str, Any]:
    if not parsed_date:
        return {"available": False, "targets": [], "actions": []}

    tagged_targets_by_item: dict[int, str] = {}
    for item in stats.get("items", []):
        if not _has_split_tags(item):
            continue
        target = _target_from_tags(parsed_date, item["tags"])
        if not target:
            continue
        tagged_targets_by_item[int(item["id"])] = target

    actions: list[dict[str, Any]] = []
    target_summaries: dict[str, dict[str, Any]] = {}
    for item in stats.get("items", []):
        if not _has_split_tags(item):
            continue
        tags = item["tags"]
        target = tagged_targets_by_item.get(int(item["id"])) or _target_from_tags(parsed_date, tags)
        reason = "tags"
        tag_ids = [int(tag["id"]) for tag in tags]
        if not target:
            continue
        action = {
            "item_id": int(item["id"]),
            "source_relative_path": item["relative_path"],
            "target_folder": target,
            "target_relative_path": item["relative_path"],
            "file_size": int(item.get("file_size") or 0),
            "file_mtime": float(item.get("file_mtime") or 0),
            "reason": reason,
        }
        actions.append(action)
        summary = target_summaries.setdefault(
            target,
            {
                "target_folder": target,
                "file_count": 0,
                "archive_count": 0,
                "untagged_file_count": 0,
                "tag_ids": [],
                "reasons": [],
            },
        )
        summary["file_count"] += 1
        if _is_archive_item(item):
            summary["archive_count"] += 1
        for tag_id in tag_ids:
            if tag_id not in summary["tag_ids"]:
                summary["tag_ids"].append(tag_id)
        if reason not in summary["reasons"]:
            summary["reasons"].append(reason)

    targets = sorted(target_summaries.values(), key=lambda target: target["target_folder"])
    return {
        "available": len(targets) > 1,
        "targets": targets,
        "actions": sorted(
            actions,
            key=lambda action: (action["target_folder"], action["source_relative_path"]),
        ),
    }


def _auto_confirm_tag_ids(stats: dict[str, Any], parsed_date: str) -> list[int]:
    if not parsed_date or int(stats.get("file_count") or 0) <= 0:
        return []
    if int(stats.get("untagged_file_count") or 0) != 0:
        return []
    tag_ids = {
        int(tag["id"])
        for item in stats.get("items", [])
        if _is_taggable_item(item) or (_is_archive_item(item) and item.get("tags"))
        for tag in item.get("tags", [])
    }
    return sorted(tag_ids) if len(tag_ids) == 1 else []


def _plan_kind(plan: dict[str, Any] | None) -> str:
    if not plan:
        return PLAN_KIND_RENAME_FOLDER
    value = str(plan.get("plan_kind") or PLAN_KIND_RENAME_FOLDER)
    return value if value in VALID_PLAN_KINDS else PLAN_KIND_RENAME_FOLDER


def _source_status(source: dict[str, Any], group_conflict: bool = False) -> str:
    if source["warnings"] and "date_not_found" in source["warnings"]:
        return "manual_review"
    if source.get("plan_kind") == PLAN_KIND_SPLIT_BY_TAG:
        if not source.get("split_preview", {}).get("targets"):
            return "needs_tags"
        if group_conflict:
            return "conflict"
        if source.get("stored_status") == "confirmed":
            return "confirmed"
        return "ready"
    if not source["selected_tags"]:
        return "needs_tags"
    if group_conflict:
        return "conflict"
    if source.get("stored_status") == "confirmed":
        return "confirmed"
    return "ready"


def _merge_status(statuses: list[str]) -> str:
    priority = ["conflict", "manual_review", "needs_tags", "ready", "confirmed"]
    for status in priority:
        if status in statuses:
            return status
    return "manual_review"


def _build_sources(artist_id: int) -> list[dict[str, Any]]:
    stats_by_folder = _folder_stats(artist_id)
    plans = _plan_rows(artist_id)
    executed_targets = _executed_target_folders(artist_id)
    sources = []
    for source_folder, stats in stats_by_folder.items():
        if normalize_folder(source_folder) in executed_targets:
            continue
        plan = plans.get(source_folder)
        parsed = parse_folder_date(source_folder)
        if (
            not parsed["date"]
            and not (
                plan
                and (
                    plan.get("status") == "confirmed"
                    or plan.get("executed_at") is not None
                    or str(plan.get("execution_log") or "[]") not in {"", "[]"}
                )
            )
        ):
            continue
        plan_kind = _plan_kind(plan)
        split_preview = _build_split_preview(stats, parsed["date"])
        selected_tags = _valid_tags(artist_id, _decode_selected_tag_ids(plan))
        target_name = (
            build_target_name(parsed["date"], [tag["name"] for tag in selected_tags])
            if plan_kind == PLAN_KIND_RENAME_FOLDER and parsed["date"] and selected_tags
            else ""
        )
        auto_confirm_ids = _auto_confirm_tag_ids(stats, parsed["date"])
        can_auto_confirm_plan = (
            bool(auto_confirm_ids)
            and (
                not plan
                or (
                    plan_kind == PLAN_KIND_RENAME_FOLDER
                    and plan.get("status") != "confirmed"
                    and not _decode_selected_tag_ids(plan)
                )
            )
        )
        source = {
            "id": plan["id"] if plan else None,
            "artist_id": artist_id,
            "source_folder": source_folder,
            "original_folder_name": source_folder,
            "original_title": source_folder,
            "parsed_date": parsed["date"],
            "plan_kind": plan_kind,
            "selected_tags": selected_tags,
            "folder_tags": stats["folder_tags"],
            "tagged_file_count": stats["tagged_file_count"],
            "untagged_file_count": stats["untagged_file_count"],
            "archive_count": stats["archive_count"],
            "auto_confirmable": can_auto_confirm_plan,
            "split_preview": split_preview,
            "target_name": target_name,
            "target_folder": target_name,
            "status": "",
            "stored_status": plan["status"] if plan else "",
            "is_confirmed_plan": bool(plan and plan["status"] == "confirmed"),
            "confirmation_source": plan["confirmation_source"] if plan else "",
            "confirmed_at": plan["confirmed_at"] if plan else None,
            "warnings": list(parsed["warnings"]),
            "file_count": stats["file_count"],
            "total_size": stats["total_size"],
            "max_mtime": stats["max_mtime"],
            "relative_files": stats["relative_files"],
        }
        source["status"] = _source_status(source)
        sources.append(source)
    sources.sort(key=_source_sort_key)
    return sources


def _source_has_folder_tags(source: dict[str, Any]) -> bool:
    return bool(source.get("folder_tags"))


def _source_sort_key(source: dict[str, Any]) -> tuple[int, str, str]:
    return (
        0 if _source_has_folder_tags(source) else 1,
        source["target_name"] or source["source_folder"],
        source["source_folder"],
    )


def _group_sort_key(group: dict[str, Any]) -> tuple[int, str]:
    return (
        0 if any(_source_has_folder_tags(source) for source in group["sources"]) else 1,
        group["target_name"] or group["sources"][0]["source_folder"],
    )


def _target_existing_conflicts(
    artist_path: str,
    target_name: str,
    planned_relative_files: list[str],
) -> list[dict[str, str]]:
    if not target_name:
        return []
    artist_root = os.path.realpath(os.path.abspath(artist_path))
    target_dir = os.path.realpath(os.path.abspath(os.path.join(artist_root, target_name)))
    try:
        if os.path.commonpath([artist_root, target_dir]) != artist_root:
            return [{"relative_path": target_name, "reason": "target_outside_artist"}]
    except ValueError:
        return [{"relative_path": target_name, "reason": "target_outside_artist"}]
    if not os.path.isdir(target_dir):
        return []

    planned = {normalize_slashes(path) for path in planned_relative_files}
    conflicts = []
    for root, _, files in os.walk(target_dir):
        for name in files:
            full = os.path.join(root, name)
            rel = normalize_slashes(os.path.relpath(full, target_dir))
            if rel in planned:
                conflicts.append({"relative_path": rel, "reason": "target_exists"})
    return sorted(conflicts, key=lambda conflict: conflict["relative_path"])


def _group_sources(
    sources: list[dict[str, Any]],
    artist_path: str = "",
    refresh: bool = False,
) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for source in sources:
        key = (
            f"__source__:{source['source_folder']}"
            if source.get("plan_kind") == PLAN_KIND_SPLIT_BY_TAG
            else source["target_name"] or f"__source__:{source['source_folder']}"
        )
        grouped[key].append(source)

    groups = []
    for key, group_sources in grouped.items():
        relative_counts = Counter()
        for source in group_sources:
            relative_counts.update(source["relative_files"])
        target_name = "" if key.startswith("__source__:") else key
        conflicts = [
            {"relative_path": path, "reason": "duplicate_in_group"}
            for path, count in sorted(relative_counts.items())
            if count > 1
        ]
        if refresh and artist_path and target_name:
            planned_files = []
            for source in group_sources:
                planned_files.extend(source["relative_files"])
            conflicts.extend(_target_existing_conflicts(artist_path, target_name, planned_files))
        conflict = bool(conflicts)
        for source in group_sources:
            source["status"] = _source_status(source, group_conflict=conflict)

        warnings = []
        for source in group_sources:
            for warning in source["warnings"]:
                if warning not in warnings:
                    warnings.append(warning)

        statuses = [source["status"] for source in group_sources]
        groups.append(
            {
                "target_name": target_name,
                "target_folder": target_name,
                "status": _merge_status(statuses),
                "sources": [
                    {
                        k: v
                        for k, v in source.items()
                        if k not in {"relative_files", "stored_status"}
                    }
                    for source in group_sources
                ],
                "warnings": warnings,
                "conflicts": conflicts,
                "file_count": sum(source["file_count"] for source in group_sources),
                "total_size": sum(source["total_size"] for source in group_sources),
                "max_mtime": max((source["max_mtime"] for source in group_sources), default=0),
            }
        )

    groups.sort(key=_group_sort_key)
    return groups


def list_folder_rename_groups(
    artist_id: int,
    status: str | None = None,
    offset: int = 0,
    limit: int = 200,
    refresh: bool = False,
) -> dict[str, Any]:
    artist = _artist(artist_id)
    if not artist:
        return {
            "artist": None,
            "groups": [],
            "execution_history": [],
            "total_groups": 0,
            "total_sources": 0,
            "offset": offset,
            "limit": limit,
        }
    groups = _group_sources(_build_sources(artist_id), artist_path=artist["path"], refresh=refresh)
    if status:
        groups = [group for group in groups if group["status"] == status]
    total_groups = len(groups)
    total_sources = sum(len(group["sources"]) for group in groups)
    limit = max(1, min(int(limit or 200), 500))
    offset = max(0, int(offset or 0))
    return {
        "artist": {"id": artist["id"], "name": artist["name"], "path": artist["path"]},
        "groups": groups[offset:offset + limit],
        "execution_history": _execution_history(artist_id),
        "total_groups": total_groups,
        "total_sources": total_sources,
        "offset": offset,
        "limit": limit,
        "refresh": bool(refresh),
    }


def save_folder_rename_plan(
    artist_id: int,
    source_folder: str,
    selected_tag_ids: list[int] | None = None,
    status: str = "draft",
    plan_kind: str = PLAN_KIND_RENAME_FOLDER,
    confirmation_source: str = "",
) -> dict[str, Any]:
    db = get_db()
    artist = _artist(artist_id)
    if not artist:
        raise ValueError("Artist not found")
    source = _validate_source_folder(source_folder)
    stats = _folder_stats(artist_id).get(source)
    if not stats:
        raise ValueError("Source folder not found")
    if status not in VALID_SOURCE_STATUSES:
        raise ValueError("Bad status")
    if plan_kind not in VALID_PLAN_KINDS:
        raise ValueError("Bad plan kind")
    source_label = str(confirmation_source or "")
    if source_label not in VALID_CONFIRMATION_SOURCES:
        raise ValueError("Bad confirmation source")
    parsed = parse_folder_date(source)
    split_preview = _build_split_preview(stats, parsed["date"])
    if plan_kind == PLAN_KIND_SPLIT_BY_TAG:
        if not split_preview["available"]:
            raise ValueError("Split plan needs multiple targets")
        tags = []
        selected_ids = []
        target = ""
        split_actions = split_preview["actions"]
    else:
        tags = _valid_tags(artist_id, selected_tag_ids or [])
        selected_ids = [tag["id"] for tag in tags]
        target = build_target_name(parsed["date"], [tag["name"] for tag in tags]) if parsed["date"] and tags else ""
        split_actions = []
    now = time.time()
    confirmed_at = now if status == "confirmed" else None
    if status != "confirmed":
        source_label = ""
    elif not source_label:
        source_label = "split" if plan_kind == PLAN_KIND_SPLIT_BY_TAG else "manual"
    db.execute(
        """
        INSERT INTO folder_rename_plans
        (artist_id, source_folder, original_folder_name, original_title, parsed_date,
         selected_tag_ids, status, file_count, total_size, max_mtime, updated_at,
         confirmed_at, confirmation_source, target_folder, executed_at, execution_log,
         plan_kind, split_actions)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, '[]', ?, ?)
        ON CONFLICT(artist_id, source_folder) DO UPDATE SET
            original_folder_name=excluded.original_folder_name,
            original_title=excluded.original_title,
            parsed_date=excluded.parsed_date,
            selected_tag_ids=excluded.selected_tag_ids,
            status=excluded.status,
            file_count=excluded.file_count,
            total_size=excluded.total_size,
            max_mtime=excluded.max_mtime,
            updated_at=excluded.updated_at,
            confirmed_at=excluded.confirmed_at,
            confirmation_source=excluded.confirmation_source,
            target_folder=excluded.target_folder,
            executed_at=NULL,
            execution_log='[]',
            plan_kind=excluded.plan_kind,
            split_actions=excluded.split_actions
        """,
        (
            artist_id,
            source,
            source,
            source,
            parsed["date"],
            json.dumps(selected_ids, ensure_ascii=False),
            status,
            stats["file_count"],
            stats["total_size"],
            stats["max_mtime"],
            now,
            confirmed_at,
            source_label,
            target,
            plan_kind,
            json.dumps(split_actions, ensure_ascii=False),
        ),
    )
    db.commit()
    row = db.execute(
        "SELECT * FROM folder_rename_plans WHERE artist_id=? AND source_folder=?",
        (artist_id, source),
    ).fetchone()
    return {
        "id": row["id"],
        "artist_id": artist_id,
        "source_folder": source,
        "plan_kind": plan_kind,
        "selected_tags": tags,
        "target_folder": target,
        "split_preview": split_preview,
        "status": status,
        "confirmation_source": source_label,
    }


def _plan_by_id(plan_id: int) -> dict[str, Any]:
    row = get_db().execute(
        "SELECT * FROM folder_rename_plans WHERE id=?",
        (int(plan_id),),
    ).fetchone()
    if not row:
        raise ValueError("Plan not found")
    return dict(row)


def refresh_confirmed_folder_rename_plan(plan_id: int) -> dict[str, Any]:
    plan = _plan_by_id(plan_id)
    if plan.get("status") != "confirmed":
        raise ValueError("Plan is not confirmed")
    plan_kind = _plan_kind(plan)
    source_label = str(plan.get("confirmation_source") or "")
    if not source_label:
        source_label = "split" if plan_kind == PLAN_KIND_SPLIT_BY_TAG else "manual"
    return save_folder_rename_plan(
        int(plan["artist_id"]),
        str(plan["source_folder"]),
        selected_tag_ids=_decode_selected_tag_ids(plan),
        status="confirmed",
        plan_kind=plan_kind,
        confirmation_source=source_label,
    )


def unconfirm_folder_rename_plan(plan_id: int) -> dict[str, Any]:
    plan = _plan_by_id(plan_id)
    if plan.get("status") != "confirmed":
        raise ValueError("Plan is not confirmed")
    now = time.time()
    db = get_db()
    db.execute(
        """
        UPDATE folder_rename_plans
        SET status='draft',
            confirmed_at=NULL,
            confirmation_source='',
            updated_at=?,
            executed_at=NULL,
            execution_log='[]'
        WHERE id=?
        """,
        (now, int(plan_id)),
    )
    db.commit()
    updated = _plan_by_id(plan_id)
    return {
        "id": updated["id"],
        "artist_id": updated["artist_id"],
        "source_folder": updated["source_folder"],
        "plan_kind": _plan_kind(updated),
        "target_folder": updated["target_folder"],
        "status": updated["status"],
        "confirmation_source": updated["confirmation_source"],
    }


def auto_confirm_folder_rename_plans(artist_id: int) -> dict[str, Any]:
    stats_by_folder = _folder_stats(artist_id)
    plans = _plan_rows(artist_id)
    executed_targets = _executed_target_folders(artist_id)
    confirmed = []
    skipped = []
    for source_folder, stats in stats_by_folder.items():
        if normalize_folder(source_folder) in executed_targets:
            skipped.append({"source_folder": source_folder, "reason": "executed_target"})
            continue
        plan = plans.get(source_folder)
        if plan and (
            _plan_kind(plan) != PLAN_KIND_RENAME_FOLDER
            or plan.get("status") == "confirmed"
            or _decode_selected_tag_ids(plan)
        ):
            skipped.append({"source_folder": source_folder, "reason": "has_plan"})
            continue
        parsed = parse_folder_date(source_folder)
        tag_ids = _auto_confirm_tag_ids(stats, parsed["date"])
        if not tag_ids:
            skipped.append({"source_folder": source_folder, "reason": "not_single_tag"})
            continue
        saved = save_folder_rename_plan(
            artist_id,
            source_folder,
            selected_tag_ids=tag_ids,
            status="confirmed",
            plan_kind=PLAN_KIND_RENAME_FOLDER,
            confirmation_source="auto",
        )
        confirmed.append(saved)
    return {
        "artist_id": artist_id,
        "confirmed_count": len(confirmed),
        "skipped_count": len(skipped),
        "confirmed": confirmed,
        "skipped": skipped,
    }


def export_folder_rename_plans(artist_id: int) -> list[dict[str, Any]]:
    groups = list_folder_rename_groups(artist_id, limit=500)["groups"]
    rows = []
    artist = _artist(artist_id)
    for group in groups:
        for source in group["sources"]:
            rows.append(
                {
                    "artist_id": artist_id,
                    "artist_name": artist["name"] if artist else "",
                    "source_folder": source["source_folder"],
                    "original_title": source["original_title"],
                    "parsed_date": source["parsed_date"],
                    "selected_tags": ";".join(tag["name"] for tag in source["selected_tags"]),
                    "target_folder": source["target_folder"],
                    "status": source["status"],
                    "warnings": ";".join(source["warnings"]),
                    "file_count": source["file_count"],
                    "total_size": source["total_size"],
                    "max_mtime": source["max_mtime"],
                }
            )
    return rows


def export_folder_rename_csv(artist_id: int) -> str:
    rows = export_folder_rename_plans(artist_id)
    output = io.StringIO()
    fieldnames = [
        "artist_id",
        "artist_name",
        "source_folder",
        "original_title",
        "parsed_date",
        "selected_tags",
        "target_folder",
        "status",
        "warnings",
        "file_count",
        "total_size",
        "max_mtime",
    ]
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue()
