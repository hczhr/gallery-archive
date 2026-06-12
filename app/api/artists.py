from app.database import get_db
from app.path_display import display_path
from app.sort_utils import artist_sort_key


def list_artists():
    db = get_db()
    rows = db.execute("""
        SELECT a.*, COUNT(i.id) as item_count
        FROM artists a
        LEFT JOIN items i ON i.artist_id = a.id
            AND i.missing = 0
            AND (i.media_type IN ('image', 'video', 'source', 'archive') OR i.is_archive = 1)
        WHERE a.missing = 0
        GROUP BY a.id
    """).fetchall()
    artists = [dict(r) for r in rows]
    artists.sort(key=lambda a: artist_sort_key(a["name"]))
    return artists


def list_duplicate_artist_folders():
    db = get_db()
    rows = db.execute("""
        SELECT a.id, a.name, a.path, COUNT(i.id) as item_count
        FROM artists a
        LEFT JOIN items i ON i.artist_id = a.id
            AND i.missing = 0
            AND (i.media_type IN ('image', 'video', 'source', 'archive') OR i.is_archive = 1)
        WHERE a.missing = 0
        GROUP BY a.id
    """).fetchall()

    by_name = {}
    for row in rows:
        name = (row["name"] or "").strip()
        if not name:
            continue
        by_name.setdefault(name.casefold(), []).append(dict(row))

    groups = []
    for entries in by_name.values():
        if len(entries) < 2:
            continue
        entries.sort(key=lambda a: a["path"])
        display_name = sorted({a["name"] for a in entries}, key=artist_sort_key)[0]
        groups.append({
            "name": display_name,
            "count": len(entries),
            "paths": [
                {
                    "id": a["id"],
                    "name": a["name"],
                    "path": a["path"],
                    "display_path": display_path(a["path"]),
                    "item_count": a["item_count"],
                }
                for a in entries
            ],
        })

    groups.sort(key=lambda g: (-g["count"], artist_sort_key(g["name"])))
    return {"count": len(groups), "groups": groups}


def get_artist(artist_id: int):
    db = get_db()
    row = db.execute("SELECT * FROM artists WHERE id=?", (artist_id,)).fetchone()
    return dict(row) if row else None


def get_artist_stats(artist_id: int):
    db = get_db()
    total = db.execute(
        """
        SELECT COUNT(*) FROM items
        WHERE artist_id=? AND missing=0
          AND (media_type IN ('image', 'video', 'source', 'archive') OR is_archive=1)
        """,
        (artist_id,)
    ).fetchone()[0]
    videos = db.execute(
        "SELECT COUNT(*) FROM items WHERE artist_id=? AND media_type='video' AND is_archive=0 AND missing=0",
        (artist_id,)
    ).fetchone()[0]
    sources = db.execute(
        "SELECT COUNT(*) FROM items WHERE artist_id=? AND media_type='source' AND is_archive=0 AND missing=0",
        (artist_id,)
    ).fetchone()[0]
    archives = db.execute(
        "SELECT COUNT(*) FROM items WHERE artist_id=? AND (is_archive=1 OR media_type='archive') AND missing=0",
        (artist_id,)
    ).fetchone()[0]
    untagged = db.execute("""
        SELECT COUNT(*) FROM items
        WHERE artist_id=? AND missing=0
        AND (media_type IN ('image', 'video', 'source', 'archive') OR is_archive=1)
        AND NOT EXISTS (SELECT 1 FROM item_tags it WHERE it.item_id = items.id)
    """, (artist_id,)).fetchone()[0]

    tags = db.execute("""
        SELECT t.id, t.name, COUNT(i.id) as count
        FROM tags t
        LEFT JOIN item_tags it ON it.tag_id = t.id
        LEFT JOIN items i ON i.id = it.item_id
            AND i.missing=0
            AND (i.media_type IN ('image', 'video', 'source', 'archive') OR i.is_archive=1)
        WHERE t.artist_id=?
        GROUP BY t.id
        ORDER BY t.sort_order, t.name
    """, (artist_id,)).fetchall()

    return {
        "total": total,
        "archives": archives,
        "videos": videos,
        "sources": sources,
        "untagged": untagged,
        "tags": [{"id": r["id"], "name": r["name"], "count": r["count"]} for r in tags]
    }
