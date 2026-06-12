from app.database import get_db
from app.folder_utils import folder_path_prefix, normalize_folder, relative_folder_path
from app.path_display import display_path, real_path
from app.api.tags import create_tag
from app.tag_propagation import propagate_hash_tags_for_items


def _attach_item_tags(items: list[dict]):
    if not items:
        return items
    db = get_db()
    ids = [item["id"] for item in items]
    placeholders = ",".join("?" * len(ids))
    rows = db.execute(f"""
        SELECT it.item_id, t.id, t.name
        FROM item_tags it
        JOIN tags t ON t.id = it.tag_id
        WHERE it.item_id IN ({placeholders})
        ORDER BY t.sort_order, t.name
    """, ids).fetchall()
    by_item = {item_id: [] for item_id in ids}
    for row in rows:
        by_item[row["item_id"]].append({"id": row["id"], "name": row["name"]})
    for item in items:
        item["tags"] = by_item.get(item["id"], [])
    return items


def list_items(artist_id: int = None, tag_id: int = None, untagged: bool = False, search: str = None,
               date_from: str = None, date_to: str = None,
               tags: str = None, archive_only: bool = False,
               image_only: bool = False,
               media_type: str = None,
               folder: str = None,
               search_tags_only: bool = False,
               duplicates_only: bool = False,
               offset: int = 0, limit: int = 100, sort: str = "date_desc"):
    db = get_db()
    conditions = ["i.missing = 0"]
    params = []
    if artist_id is not None:
        conditions.append("i.artist_id = ?")
        params.append(artist_id)

    taggable_media = ("image", "video", "source")
    if tag_id is not None or tags or untagged or search_tags_only:
        conditions.append("(i.media_type IN ('image', 'video', 'source', 'archive') OR i.is_archive = 1)")

    if duplicates_only:
        conditions.append("i.media_type IN ('image', 'video', 'source')")
        conditions.append("i.is_archive = 0")

    if media_type:
        if media_type == "archive":
            conditions.append("(i.media_type = 'archive' OR i.is_archive = 1)")
        elif media_type in taggable_media:
            conditions.append("i.media_type = ?")
            conditions.append("i.is_archive = 0")
            params.append(media_type)
        else:
            conditions.append("1 = 0")
    elif archive_only:
        conditions.append("(i.media_type = 'archive' OR i.is_archive = 1)")
    elif image_only:
        conditions.append("i.media_type IN ('image', 'video', 'source')")
        conditions.append("i.is_archive = 0")
    else:
        conditions.append("(i.media_type IN ('image', 'video', 'source', 'archive') OR i.is_archive = 1)")

    if tag_id is not None:
        conditions.append("""
            EXISTS (
                SELECT 1 FROM item_tags it
                JOIN tags t ON t.id = it.tag_id
                WHERE it.item_id = i.id AND it.tag_id = ? AND t.artist_id = i.artist_id
            )
        """)
        params.append(tag_id)

    if tags:
        selected_tags = [tag_name.strip() for tag_name in tags.split(",") if tag_name.strip()]
        for tag_name in selected_tags:
            conditions.append("""
                EXISTS (
                    SELECT 1 FROM item_tags it
                    JOIN tags t ON t.id = it.tag_id
                    WHERE it.item_id = i.id AND t.artist_id = i.artist_id
                      AND t.name = ?
                )
            """)
            params.append(tag_name)

    if untagged:
        conditions.append("NOT EXISTS (SELECT 1 FROM item_tags it WHERE it.item_id = i.id)")

    artist = None
    prefix = None
    folder = normalize_folder(folder)
    if folder:
        artist = db.execute("SELECT path FROM artists WHERE id=?", (artist_id,)).fetchone() if artist_id is not None else None
        if artist:
            prefix = folder_path_prefix(artist["path"], folder)
            conditions.append("substr(replace(i.file_path, '\\', '/'), 1, ?) = ?")
            params.extend([len(prefix), prefix])
        else:
            conditions.append("1 = 0")

    if duplicates_only:
        conditions.append("i.hash_status = 'done'")
        conditions.append("i.content_hash != ''")
        duplicate_conditions = [
            "d.artist_id = i.artist_id",
            "d.id != i.id",
            "d.missing = 0",
            "d.hash_status = 'done'",
            "d.content_hash = i.content_hash",
            "d.content_hash != ''",
        ]
        duplicate_params = []
        if folder and prefix:
            duplicate_conditions.append("substr(replace(d.file_path, '\\', '/'), 1, ?) = ?")
            duplicate_params.extend([len(prefix), prefix])
        conditions.append(f"""
            EXISTS (
                SELECT 1 FROM items d
                WHERE {" AND ".join(duplicate_conditions)}
            )
        """)
        params.extend(duplicate_params)

    if search:
        like = f"%{search}%"
        tag_search = """
            EXISTS (
                SELECT 1
                FROM item_tags sit
                JOIN tags st ON st.id = sit.tag_id
                WHERE sit.item_id = i.id
                  AND st.artist_id = i.artist_id
                  AND st.name LIKE ?
            )
        """
        if search_tags_only:
            conditions.append(tag_search)
            params.append(like)
        else:
            conditions.append(f"""
                (
                    i.file_name LIKE ?
                    OR i.folder_name LIKE ?
                    OR i.file_path LIKE ?
                    OR {tag_search}
                )
            """)
            params.extend([like, like, like, like])

    if date_from:
        conditions.append("i.date >= ?")
        params.append(date_from)

    if date_to:
        conditions.append("i.date <= ?")
        params.append(date_to)

    where = " AND ".join(conditions)

    sort_map = {
        "date_desc": "i.date DESC, i.file_name",
        "date_asc": "i.date ASC, i.file_name",
        "name": "i.file_name",
        "size": "i.file_size DESC",
    }
    order = sort_map.get(sort, "i.date DESC, i.file_name")

    count_row = db.execute(
        f"SELECT COUNT(*) FROM items i WHERE {where}", params
    ).fetchone()
    total = count_row[0] if count_row else 0

    rows = db.execute(f"""
        SELECT i.*, a.name as artist_name, a.path as artist_path
        FROM items i
        JOIN artists a ON a.id = i.artist_id
        WHERE {where}
        ORDER BY {order}
        LIMIT ? OFFSET ?
    """, params + [limit, offset]).fetchall()

    items = []
    for r in rows:
        d = dict(r)
        d["tags"] = []
        d["folder_path"] = relative_folder_path(d["artist_path"], d["file_path"])
        d["display_file_path"] = display_path(d["file_path"])
        d["real_file_path"] = real_path(d["file_path"])
        folder_path = d["file_path"].replace("\\", "/").rsplit("/", 1)[0] if "/" in d["file_path"].replace("\\", "/") else d["file_path"]
        d["display_folder_path"] = display_path(folder_path)
        items.append(d)

    return {"items": _attach_item_tags(items), "total": total, "offset": offset, "limit": limit}


def update_item_tags(artist_id: int, item_ids: list[int], tag_ids: list[int], mode: str = "set"):
    db = get_db()
    item_placeholders = ",".join("?" * len(item_ids))
    valid_items = {
        r["id"] for r in db.execute(
            f"""
            SELECT id FROM items
            WHERE artist_id=? AND missing=0
              AND (media_type IN ('image', 'video', 'source', 'archive') OR is_archive=1)
              AND id IN ({item_placeholders})
            """,
            [artist_id] + item_ids,
        ).fetchall()
    }
    if not valid_items:
        return {"updated": 0}

    valid_tags = set()
    if tag_ids:
        tag_placeholders = ",".join("?" * len(tag_ids))
        valid_tags = {
            r["id"] for r in db.execute(
                f"SELECT id FROM tags WHERE artist_id=? AND id IN ({tag_placeholders})",
                [artist_id] + tag_ids,
            ).fetchall()
        }

    ids = sorted(valid_items)
    if mode == "set":
        placeholders = ",".join("?" * len(ids))
        db.execute(f"DELETE FROM item_tags WHERE item_id IN ({placeholders})", ids)

    if mode in ("set", "add"):
        db.executemany(
            "INSERT OR IGNORE INTO item_tags (item_id, tag_id) VALUES (?, ?)",
            [(item_id, tag_id) for item_id in ids for tag_id in sorted(valid_tags)],
        )
    elif mode == "remove" and valid_tags:
        item_placeholders = ",".join("?" * len(ids))
        tag_placeholders = ",".join("?" * len(valid_tags))
        db.execute(
            f"DELETE FROM item_tags WHERE item_id IN ({item_placeholders}) AND tag_id IN ({tag_placeholders})",
            ids + sorted(valid_tags),
        )

    db.commit()

    propagated = 0
    if mode in ("set", "add") and valid_tags:
        propagated = propagate_hash_tags_for_items(ids)

    return {"updated": len(ids), "propagated": propagated}


def update_item_tags_by_name(item_ids: list[int], tag_names: list[str], mode: str = "add"):
    db = get_db()
    if mode not in ("set", "add", "remove"):
        raise ValueError("Bad mode")
    if not item_ids:
        return {"updated": 0, "artists": 0, "tags": 0, "propagated": 0}

    names = []
    seen_names = set()
    for name in tag_names or []:
        clean = (name or "").strip()
        key = clean.lower()
        if clean and key not in seen_names:
            names.append(clean)
            seen_names.add(key)

    item_placeholders = ",".join("?" * len(item_ids))
    rows = db.execute(
        f"""
        SELECT id, artist_id FROM items
        WHERE missing=0
          AND (media_type IN ('image', 'video', 'source', 'archive') OR is_archive=1)
          AND id IN ({item_placeholders})
        """,
        item_ids,
    ).fetchall()

    by_artist: dict[int, list[int]] = {}
    for row in rows:
        by_artist.setdefault(row["artist_id"], []).append(row["id"])

    updated = 0
    propagated = 0
    for artist_id, ids in by_artist.items():
        tag_ids = []
        if names:
            if mode in ("set", "add"):
                tag_ids = [int(create_tag(artist_id, name)["id"]) for name in names]
            else:
                name_placeholders = ",".join("?" * len(names))
                tag_rows = db.execute(
                    f"""
                    SELECT id FROM tags
                    WHERE artist_id=? AND name IN ({name_placeholders})
                    """,
                    [artist_id] + names,
                ).fetchall()
                tag_ids = [row["id"] for row in tag_rows]

        result = update_item_tags(artist_id, ids, tag_ids, mode)
        updated += result.get("updated", 0)
        propagated += result.get("propagated", 0)

    return {
        "updated": updated,
        "artists": len(by_artist),
        "tags": len(names),
        "propagated": propagated,
    }


def get_item(item_id: int):
    db = get_db()
    row = db.execute("""
        SELECT i.*, a.name as artist_name, a.path as artist_path
        FROM items i
        JOIN artists a ON a.id = i.artist_id
        WHERE i.id = ?
    """, (item_id,)).fetchone()
    if not row:
        return None
    d = dict(row)
    d["tags"] = []
    d["folder_path"] = relative_folder_path(d["artist_path"], d["file_path"])
    d["display_file_path"] = display_path(d["file_path"])
    d["real_file_path"] = real_path(d["file_path"])
    folder_path = d["file_path"].replace("\\", "/").rsplit("/", 1)[0] if "/" in d["file_path"].replace("\\", "/") else d["file_path"]
    d["display_folder_path"] = display_path(folder_path)
    return _attach_item_tags([d])[0]
