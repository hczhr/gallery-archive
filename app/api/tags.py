from app.database import get_db


def list_tags(artist_id: int):
    db = get_db()
    rows = db.execute("""
        SELECT t.*, COUNT(i.id) as item_count
        FROM tags t
        LEFT JOIN item_tags it ON it.tag_id = t.id
        LEFT JOIN items i ON i.id = it.item_id
            AND i.missing = 0
            AND (i.media_type IN ('image', 'video', 'source', 'archive') OR i.is_archive = 1)
        WHERE t.artist_id = ?
        GROUP BY t.id
        ORDER BY t.sort_order, t.name
    """, (artist_id,)).fetchall()
    return [dict(r) for r in rows]


def search_tags(search: str, artist_id: int = None, limit: int = 100):
    db = get_db()
    search = (search or "").strip()

    conditions = []
    params = []
    if search:
        conditions.append("t.name LIKE ?")
        params.append(f"%{search}%")
    if artist_id is not None:
        conditions.append("t.artist_id = ?")
        params.append(artist_id)
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    rows = db.execute(f"""
        SELECT
            t.id,
            t.artist_id,
            t.name,
            t.sort_order,
            a.name as artist_name,
            a.path as artist_path,
            COUNT(i.id) as item_count
        FROM tags t
        JOIN artists a ON a.id = t.artist_id
        LEFT JOIN item_tags it ON it.tag_id = t.id
        LEFT JOIN items i ON i.id = it.item_id
            AND i.missing = 0
            AND (i.media_type IN ('image', 'video', 'source', 'archive') OR i.is_archive = 1)
        {where}
        GROUP BY t.id
        ORDER BY item_count DESC, t.name, a.name
        LIMIT ?
    """, params + [limit]).fetchall()
    return [dict(r) for r in rows]


def create_tag(artist_id: int, name: str):
    db = get_db()
    name = name.strip()
    max_order = db.execute(
        "SELECT MAX(sort_order) FROM tags WHERE artist_id=?", (artist_id,)
    ).fetchone()[0] or 0
    cur = db.execute(
        "INSERT OR IGNORE INTO tags (artist_id, name, sort_order) VALUES (?, ?, ?)",
        (artist_id, name, max_order + 1)
    )
    db.commit()
    if cur.rowcount == 1:
        return {"id": cur.lastrowid, "name": name, "sort_order": max_order + 1}

    row = db.execute(
        "SELECT id, name, sort_order FROM tags WHERE artist_id=? AND name=?",
        (artist_id, name),
    ).fetchone()
    return dict(row)


def update_tag(artist_id: int, tag_id: int, name: str = None, sort_order: int = None):
    db = get_db()
    row = db.execute(
        "SELECT * FROM tags WHERE id=? AND artist_id=?", (tag_id, artist_id)
    ).fetchone()
    if not row:
        return None

    new_name = row["name"]
    if name is not None:
        new_name = name.strip()
        db.execute("UPDATE tags SET name=? WHERE id=?", (new_name, tag_id))

    if sort_order is not None:
        db.execute("UPDATE tags SET sort_order=? WHERE id=?", (sort_order, tag_id))

    db.commit()
    return {"id": tag_id, "name": new_name}


def delete_tag(artist_id: int, tag_id: int):
    db = get_db()
    row = db.execute(
        "SELECT * FROM tags WHERE id=? AND artist_id=?", (tag_id, artist_id)
    ).fetchone()
    if not row:
        return False

    db.execute("DELETE FROM item_tags WHERE tag_id=?", (tag_id,))
    db.execute("DELETE FROM tags WHERE id=?", (tag_id,))
    db.commit()
    return True
