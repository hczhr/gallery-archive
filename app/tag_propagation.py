from app.database import get_db


def _active_hash_groups_for_items(item_ids: list[int]) -> list[tuple[int, str]]:
    if not item_ids:
        return []
    db = get_db()
    placeholders = ",".join("?" * len(item_ids))
    rows = db.execute(
        f"""
        SELECT DISTINCT artist_id, content_hash
        FROM items
        WHERE id IN ({placeholders})
          AND missing=0
          AND is_archive=0
          AND media_type IN ('image', 'video', 'source')
          AND hash_status='done'
          AND content_hash != ''
        """,
        item_ids,
    ).fetchall()
    return [(row["artist_id"], row["content_hash"]) for row in rows]


def _group_item_ids(artist_id: int, content_hash: str) -> list[int]:
    rows = get_db().execute(
        """
        SELECT id
        FROM items
        WHERE artist_id=?
          AND content_hash=?
          AND hash_status='done'
          AND missing=0
          AND is_archive=0
          AND media_type IN ('image', 'video', 'source')
        ORDER BY id
        """,
        (artist_id, content_hash),
    ).fetchall()
    return [row["id"] for row in rows]


def _tag_ids_for_items(item_ids: list[int]) -> list[int]:
    if not item_ids:
        return []
    placeholders = ",".join("?" * len(item_ids))
    rows = get_db().execute(
        f"""
        SELECT DISTINCT tag_id
        FROM item_tags
        WHERE item_id IN ({placeholders})
        ORDER BY tag_id
        """,
        item_ids,
    ).fetchall()
    return [row["tag_id"] for row in rows]


def _existing_pairs(item_ids: list[int], tag_ids: list[int]) -> set[tuple[int, int]]:
    if not item_ids or not tag_ids:
        return set()
    item_placeholders = ",".join("?" * len(item_ids))
    tag_placeholders = ",".join("?" * len(tag_ids))
    rows = get_db().execute(
        f"""
        SELECT item_id, tag_id
        FROM item_tags
        WHERE item_id IN ({item_placeholders})
          AND tag_id IN ({tag_placeholders})
        """,
        item_ids + tag_ids,
    ).fetchall()
    return {(row["item_id"], row["tag_id"]) for row in rows}


def propagate_hash_tags_for_items(item_ids: list[int]) -> int:
    db = get_db()
    inserted = 0
    for artist_id, content_hash in _active_hash_groups_for_items(item_ids):
        group_item_ids = _group_item_ids(artist_id, content_hash)
        tag_ids = _tag_ids_for_items(group_item_ids)
        existing = _existing_pairs(group_item_ids, tag_ids)
        missing_pairs = [
            (item_id, tag_id)
            for item_id in group_item_ids
            for tag_id in tag_ids
            if (item_id, tag_id) not in existing
        ]
        if not missing_pairs:
            continue
        db.executemany(
            "INSERT OR IGNORE INTO item_tags (item_id, tag_id) VALUES (?, ?)",
            missing_pairs,
        )
        inserted += len(missing_pairs)
    db.commit()
    return inserted


def propagate_hash_tags_for_item(item_id: int) -> int:
    return propagate_hash_tags_for_items([item_id])
